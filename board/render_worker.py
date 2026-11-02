"""Durable one-stage worker. Imported code is stdlib only until admitted."""
import json
import os
from pathlib import Path
import time
from .render_offline import validate_launch


def boot_id():return Path('/proc/sys/kernel/random/boot_id').read_text().strip()


def write_json(path,value):
    path=Path(path);tmp=path.with_name(path.name+'.tmp')
    with tmp.open('wb') as f:
        f.write(json.dumps(value,ensure_ascii=True,allow_nan=False,indent=2).encode('utf-8'));f.flush();os.fsync(f.fileno())
    os.replace(str(tmp),str(path))
    if hasattr(os,'O_DIRECTORY'):
        fd=os.open(str(path.parent),os.O_RDONLY|os.O_DIRECTORY)
        try:os.fsync(fd)
        finally:os.close(fd)


class Journal:
    def __init__(self,result,bound):self.result=Path(result);self.bound=bound;self.hardware_started=False
    def emit(self,**values):
        self.hardware_started=self.hardware_started or bool(values.get('hardware_started'))
        values.update(self.bound,hardware_started=self.hardware_started,timestamp=time.time())
        with (self.result/'events.jsonl').open('ab') as f:
            f.write((json.dumps(values,allow_nan=False)+'\n').encode('utf-8'));f.flush();os.fsync(f.fileno())
        write_json(self.result/'status.json',values)


def empty_ledger():
    return dict(groups={g:dict(submitted=False,halted=False) for g in ('seg','video','render')},
                buffers={},all_released=True,release_failed=False,release_blocks=[])


class RetainedEvidenceError(RuntimeError):
    def __init__(self,owner,lease,primary_errors):
        super().__init__('Evidence persistence failed; retain owner and lease, no automatic retry')
        self.owner=owner;self.lease=lease;self.primary_errors=list(primary_errors)


def execute_stage(package,result,expected_sha,stage,boot,previous,token,device_factory=None,lease_factory=None):
    checked=validate_launch(package,expected_sha,stage,boot,boot_id(),stage,previous)
    package=Path(package).resolve();result=Path(result).resolve()
    if package/'offline_results' not in result.parents:raise ValueError('Result outside new package')
    bound=dict(stage=stage,boot_id=boot,package_sha256=expected_sha,pid=os.getpid(),run_token=token,
               bit_sha256=checked['manifest']['bit_sha256'],hwh_sha256=checked['manifest']['hwh_sha256'])
    with (result/'claim.json').open('x',encoding='utf-8') as f:json.dump(bound,f);f.flush();os.fsync(f.fileno())
    journal=Journal(result,bound);journal.emit(state='ADMITTED',hardware_started=False)
    errors=[];device=None;lease=None;safe=True;details={};begin=time.perf_counter()
    def event(**values):
        if (result/'stop_requested').exists():raise RuntimeError('Operator requested cooperative stop')
        journal.emit(**values)
    try:
        if stage!='precheck':
            if lease_factory is None:
                from .owner_lease import OwnerLease
                lease_factory=OwnerLease
            lease=lease_factory();lease.acquire()
            event(state='OWNER_ACQUIRED')
            if device_factory is None:
                from .render_device import PynqRenderDevice
                device_factory=PynqRenderDevice
            device=device_factory(package,journal)
            event(state='BEFORE_OVERLAY_LOAD',hardware_started=True);loaded=device.load()
            if isinstance(loaded,dict):details.update(loaded)
            event(state='AFTER_OVERLAY_LOAD')
            if stage!='load':details.update(device.identity());event(state='IDENTITY_VERIFIED')
            if stage.startswith('render_'):details.update(device.run_benchmark(stage,event))
        else:details.update(precheck_only=True,files_verified=True)
    except BaseException as exc:errors.append(repr(exc))
    finally:
        if device is not None:
            try:device.close()
            except BaseException as exc:safe=False;errors.append('cleanup: '+repr(exc))
    report=dict(bound,execution_success=not errors and safe,errors=errors,cleanup_safe=safe,
                previous_result=str(Path(previous).resolve()) if previous else None,
                hardware_started=journal.hardware_started,camera_started=False,hdmi_started=False,
                segmentation_started=False,diagnostic_only=True,promotion_allowed=False,production_promotion_allowed=False,
                backend=getattr(device,'backend','file-only'),total_stage_ms=(time.perf_counter()-begin)*1000,
                release_ledger=device.ledger.snapshot() if device is not None else empty_ledger())
    report.update(details)
    try:
        write_json(result/'final_report.json',report)
        journal.emit(state='FINISHED_SAFELY' if report['execution_success'] else ('STOPPED_SAFELY' if safe else 'UNSAFE_REBOOT_REQUIRED'),
                     cleanup_safe=safe,report=str(result/'final_report.json'),promotion_allowed=False)
        if safe and lease is not None:lease.release()
    except BaseException as exc:
        # Do not turn failed evidence persistence into a reusable success. The
        # lease stays retained even if hardware was already safely halted.
        safe=False
        if device is not None:device.ledger.block_releases('Final evidence persistence failed')
        raise RetainedEvidenceError(device,lease,errors) from exc
    if not safe:
        report['_retained_owner']=device;report['_retained_lease']=lease
    return report,safe
