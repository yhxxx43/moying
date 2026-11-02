"""Current-boot resident entry; stdlib only until a detached child is admitted."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import traceback
import uuid
from .render_live import verify_frozen
from .render_offline import verify_package,SOURCE_PACKAGE
from .render_worker import boot_id,write_json,RetainedEvidenceError
from .render_live import exclusive
from .resident_recover import file_precheck
from .resident_support import binding,Journal,RotatingLog,publish_control

USB_ROOT='/sys/bus/usb/devices/1-1'
BASE_KEYS=('package','expected_sha','frozen','frozen_sha','addon','bundle_sha')


def digest(value):return hashlib.sha256(json.dumps(value,sort_keys=True).encode('utf-8')).hexdigest()


def check_boot(config):
    actual=boot_id()
    if str(uuid.UUID(actual))!=actual or not uuid.UUID(actual).int:raise ValueError('Invalid current boot UUID')
    if config.get('boot')!=actual:raise ValueError('Boot changed; run prepare again, never reuse an old handle')


def verify_files(config):
    manifest=file_precheck(config['addon'],config['bundle_sha'])
    if (manifest.get('offline_package_sha256')!=config['expected_sha'] or
        manifest.get('source_package_sha256')!=SOURCE_PACKAGE or config.get('frozen_sha')!=SOURCE_PACKAGE or
        manifest.get('duration_limit_s','missing') is not None or manifest.get('production_authorized') is not False or
        'boot_id' in manifest):raise ValueError('Resident contract mismatch')
    variant=manifest.get('runtime_variant','ink-v1')
    if variant not in ('ink-v1','combined-v1'):raise ValueError('Unknown resident runtime variant')
    if 'mode' in config and config['mode']!=('combined' if variant=='combined-v1' else 'resident'):
        raise ValueError('Resident mode does not match pinned manifest')
    verify_frozen(config['frozen']);verify_package(config['package'],config['expected_sha'])
    return manifest


def verify_usb(usb_root):
    root=Path(usb_root)
    values={n:(root/n).read_text().strip() for n in ('idVendor','idProduct','power/control','power/runtime_status')}
    if list(values.values())!=['046d','0825','on','active']:raise ValueError('USB camera must already be046d:0825 on/active; no automatic wake')
    return values


def markers(config):
    boot=config['boot'];package=Path(config['package']).resolve();frozen=Path(config['frozen']).resolve()
    return (package/'offline_results'/('render_resident_once_'+boot+'.json'),
            package/'offline_results'/('render_live_once_'+boot+'.json'),
            frozen/'offline_results'/('skin_retry_demo_once_'+boot+'.json'))


def prepare(base,usb_root=USB_ROOT):
    context={k:base[k] for k in BASE_KEYS};context['boot']=boot_id();check_boot(context);verify_files(context)
    selected=markers(context)
    if selected[0].exists() or selected[0].is_symlink():
        restore(context)  # Recovery must still work after USB failure during this owner.
    else:
        verify_usb(usb_root)
        if any(p.exists() or p.is_symlink() for p in selected[1:]):raise ValueError('An earlier demo is reserved this boot; preserve it, no new session')
    return context


def reservation(config):
    return dict(binding(config),result=config['result'],config_sha256=digest(config))


def verify_run(config):
    check_boot(config)
    package=Path(config['package']).resolve();result=Path(config['result'])
    if result.is_symlink() or result.resolve().parent!=package/'offline_results':raise ValueError('Foreign resident result')
    if not isinstance(config.get('token'),str) or len(config['token'])!=32 or config.get('operator_approved')!='START':raise ValueError('Missing resident approval/token')
    want=reservation(config)
    for marker in markers(config):
        if marker.is_symlink() or json.loads(marker.read_text(encoding='utf-8'))!=want:raise ValueError('Changed resident reservation')
    path=result/'launch.json'
    if path.is_symlink() or json.loads(path.read_text(encoding='utf-8'))!=config:raise ValueError('Changed launch configuration')


def restore(context):
    check_boot(context)
    path=markers(context)[0]
    if path.is_symlink():raise ValueError('Symlink resident reservation')
    record=json.loads(path.read_text(encoding='utf-8'));result=Path(record['result'])
    if result.is_symlink() or result.resolve().parent!=Path(context['package']).resolve()/'offline_results':raise ValueError('Foreign result directory')
    launch=result/'launch.json'
    if launch.is_symlink():raise ValueError('Symlink resident launch')
    config=json.loads(launch.read_text(encoding='utf-8'))
    if any(config.get(k)!=context.get(k) for k in BASE_KEYS+('boot',)):raise ValueError('Foreign resident context')
    verify_run(config)
    return Handle(config)


def require_idle():
    checked=subprocess.run(['fuser','-v','/dev/video0'],stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=5)
    if checked.returncode!=1 or checked.stdout.strip():raise RuntimeError('Camera busy or ownership could not be established')


def start(context,approval,usb_root=USB_ROOT):
    if approval!='START':raise ValueError('Explicit START approval required')
    check_boot(context)
    if markers(context)[0].exists() or markers(context)[0].is_symlink():return restore(context)
    manifest=verify_files(context);verify_usb(usb_root);require_idle()
    if any(p.exists() or p.is_symlink() for p in markers(context)):raise FileExistsError('Existing demo reservation; never overwrite')
    config=dict(context,token=uuid.uuid4().hex,operator_approved='START',
                mode='combined' if manifest.get('runtime_variant')=='combined-v1' else 'resident')
    parent=Path(context['package']).resolve()/'offline_results';parent.mkdir(exist_ok=True)
    result=parent/('pl_ink_resident_'+config['token'][:12]);result.mkdir(exist_ok=False);config['result']=str(result)
    # Partial reservations are deliberately not removed on failure.
    for path in markers(config):
        path.parent.mkdir(exist_ok=True);exclusive(path,reservation(config))
    write_json(result/'launch.json',config)
    Journal(config).emit(state='LAUNCH_REQUESTED',hardware_started=False)
    try:
        subprocess.Popen([sys.executable,'-B','-m','board.resident_launch','supervise',str(result/'launch.json')],
                         cwd=config['addon'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True)
    except BaseException as exc:
        write_json(result/'supervisor_failure.json',dict(binding(config),error=repr(exc)[:1500],child_started=False))
        raise
    return Handle(config)


def claim_child(config):
    verify_run(config)
    exclusive(Path(config['result'])/'claim.json',dict(binding(config),pid=os.getpid()))


class Handle:
    def __init__(self,config):self.config=config;self.result=Path(config['result'])
    def status(self):
        verify_run(self.config);bound=binding(self.config)
        path=self.result/'status.json'
        if path.is_symlink():raise ValueError('Symlink status')
        state=json.loads(path.read_text(encoding='utf-8')) if path.exists() else dict(bound,state='LAUNCH_REQUESTED')
        if any(state.get(k)!=v for k,v in bound.items()):raise ValueError('Foreign resident status')
        state.update(owner_running=True,exit_code=None,exit_evidence_valid=False)
        path=self.result/'exit.json'
        if path.exists() and not path.is_symlink():
            actual=json.loads(path.read_text(encoding='utf-8'))
            claim=self.result/'claim.json'
            claimed=json.loads(claim.read_text(encoding='utf-8')) if claim.is_file() and not claim.is_symlink() else {}
            good=(all(actual.get(k)==v and claimed.get(k)==v for k,v in bound.items()) and
                  type(actual.get('exit_code')) is int and type(claimed.get('pid')) is int and
                  actual.get('pid')==actual.get('waited_pid')==claimed['pid'])
            if good:state.update(owner_running=False,exit_code=actual['exit_code'],exit_evidence_valid=True)
            else:state.update(owner_running=None,notice='Invalid waited-exit evidence; no restart')
        return state
    def view(self):
        state=self.status();path=self.result/'final_report.json';report=None
        if path.is_file() and not path.is_symlink():
            report=json.loads(path.read_text(encoding='utf-8'))
            if any(report.get(k)!=v for k,v in binding(self.config).items()):raise ValueError('Foreign final report')
        effective=bool(state.get('exit_evidence_valid') and state.get('exit_code')==0 and
                       state.get('state')=='FINISHED_SAFELY' and state.get('cleanup_safe') is True and report and
                       report.get('execution_success') is True and report.get('cleanup_safe') is True)
        failure=self.result/'supervisor_failure.json'
        return dict(result=str(self.result),status=state,report=report,effective_success=effective,
                    supervisor_failure=json.loads(failure.read_text()) if failure.is_file() and not failure.is_symlink() else None,
                    notice='Cooperative stop cannot interrupt blocked native calls; retain owner if cleanup is unproven')
    def confirm_empty(self,approval):
        if approval!='EMPTY':raise ValueError('Explicit EMPTY confirmation required')
        state=self.status()
        if state.get('state')!='WAITING_FOR_EMPTY_CONFIRMATION' or state.get('owner_running') is not True:raise ValueError('Not a live waiting task')
        publish_control(self.result/'empty.json',dict(binding(self.config),empty_token=state['empty_token']))
    def request_stop(self):
        verify_run(self.config)
        if self.status().get('owner_running') is False:return
        value=dict(binding(self.config),kind='cooperative_stop')
        path=self.result/'stop_requested'
        if path.exists():
            if path.is_symlink() or json.loads(path.read_text())!=value:raise ValueError('Foreign stop request')
        else:publish_control(path,value)


def observe(handle,seconds=0,waiting=False):
    deadline=time.monotonic()+min(max(seconds,0),30);previous=None
    while True:
        view=handle.view();state=view['status'];key=(state.get('state'),state.get('frames'),state.get('owner_running'))
        if key!=previous:print('状态：',key[0],'帧：',key[1],'进程仍在：',key[2],flush=True);previous=key
        if (time.monotonic()>=deadline or state.get('owner_running') is not True or
            (waiting and state.get('state')=='WAITING_FOR_EMPTY_CONFIRMATION') or state.get('state')=='UNSAFE_REBOOT_REQUIRED'):break
        time.sleep(.5)
    print(json.dumps(view,ensure_ascii=False,indent=2),flush=True)
    return view


def record_exit(process,config):
    code=process.wait()
    record=dict(binding(config),pid=process.pid,waited_pid=process.pid,exit_code=code,timestamp=time.time())
    write_json(Path(config['result'])/'exit.json',record)
    return record


def supervise(config):
    verify_run(config);verify_files(config);result=Path(config['result']);process=None
    log=RotatingLog(result/'supervisor.log')
    try:
        process=subprocess.Popen([sys.executable,'-B','-u','-m','board.resident_launch','worker',str(result/'launch.json')],
                                 cwd=config['addon'],stdout=subprocess.PIPE,stderr=subprocess.STDOUT)
        output=RotatingLog(result/'worker.log')
        while True:
            data=os.read(process.stdout.fileno(),4096)
            if not data:break
            output.write(data)
        record_exit(process,config)
    except BaseException as exc:
        failure=dict(binding(config),error=repr(exc)[:1500],child_started=process is not None)
        write_json(result/'supervisor_failure.json',failure);log.write(json.dumps(failure)+'\n')
        if process is not None:
            try:Handle(config).request_stop()
            except BaseException:pass
            # Never infer termination or replace wait evidence with polling.
            record_exit(process,config)


def worker(config):
    verify_run(config);verify_files(config);claim_child(config);journal=Journal(config);entered_runtime=False
    def stopping(*unused):
        try:Handle(config).request_stop()
        except BaseException:pass
    signal.signal(signal.SIGINT,stopping);signal.signal(signal.SIGTERM,stopping)
    try:
        verify_usb(USB_ROOT)
        if sys.version_info[:2]!=(3,6) or not sys.platform.startswith('linux') or os.geteuid()!=0:raise RuntimeError('Requires board root Python3.6/Linux')
        journal.emit(state='IMPORT_NUMPY_BEGIN');import numpy as np
        journal.emit(state='IMPORT_OPENCV_BEGIN');import cv2
        if np.__version__!='1.13.3' or cv2.__version__!='3.2.0':raise RuntimeError('Pinned board numerical runtime required')
        from .resident_runtime import run_resident
        entered_runtime=True
        report,safe=run_resident(config,journal)
        if not safe:
            from .render_launch import retain_forever
            retain_forever(report)
        return 0 if report['execution_success'] else 1
    except RetainedEvidenceError as exc:
        traceback.print_exc()
        if exc.owner is not None or exc.lease is not None:
            from .render_launch import retain_forever
            retain_forever(exc)
        return 1
    except BaseException as exc:
        report=dict(binding(config),pid=os.getpid(),execution_success=False,cleanup_safe=not entered_runtime,
                    hardware_started=journal.hardware_started,errors=[repr(exc)[:1500]],promotion_allowed=False)
        write_json(Path(config['result'])/'final_report.json',report)
        journal.emit(state='UNSAFE_REBOOT_REQUIRED' if entered_runtime else 'STOPPED_SAFELY',cleanup_safe=not entered_runtime)
        if entered_runtime:
            from .render_launch import retain_forever
            retain_forever(report)
        return 1


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('mode',choices=['supervise','worker']);parser.add_argument('config')
    args=parser.parse_args();config=json.loads(Path(args.config).read_text(encoding='utf-8'))
    if Path(args.config).resolve()!=Path(config['result']).resolve()/'launch.json':raise ValueError('Foreign launch path')
    if args.mode=='supervise':
        try:supervise(config)
        except BaseException as exc:
            write_json(Path(config['result'])/'supervisor_failure.json',dict(binding(config),error=repr(exc)[:1500],child_started=False));raise
    else:sys.exit(worker(config))
