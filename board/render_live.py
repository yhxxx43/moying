"""Authenticated live admission/one-shot evidence. Import is stdlib-only."""
import hashlib
import json
import os
import importlib
import importlib.util
import sys
import time
import types
from pathlib import Path
import uuid
from .render_offline import SOURCE_PACKAGE,file_member,verify_package,validate_offline_evidence
from .render_worker import write_json,boot_id,RetainedEvidenceError,empty_ledger

USB_ROOT='/sys/bus/usb/devices/1-1'


def sha(raw):return hashlib.sha256(raw).hexdigest()


def verify_frozen(root):
    root=Path(root).resolve();raw=(root/'manifest.json').read_bytes()
    if sha(raw)!=SOURCE_PACKAGE:raise ValueError('Wrong frozen source package')
    manifest=json.loads(raw.decode('utf-8'))
    if len(manifest.get('files',{}))!=240:raise ValueError('Expected240 frozen source files')
    for name,want in manifest['files'].items():
        if sha(file_member(root,name).read_bytes())!=want:raise ValueError('Changed frozen source: '+name)
    return manifest


def paths(config):
    package=Path(config['package']).resolve();result=Path(config['result'])
    if result.is_symlink() or result.resolve().parent!=package/'offline_results':raise ValueError('Foreign live result')
    return package,result.resolve(),Path(config['frozen']).resolve()


def verify_context(config,current_boot,usb_root=USB_ROOT,check_available=True):
    boot=config.get('boot')
    if (not isinstance(boot,str) or str(uuid.UUID(boot))!=boot or not uuid.UUID(boot).int or boot!=current_boot):
        raise ValueError('Current boot does not match this live entry')
    if type(config.get('approved_live_seconds')) is not int or config['approved_live_seconds']!=30:
        raise ValueError('Explicit30s operator approval required')
    if config.get('skip_standalone_hdmi') is not True:raise ValueError('Explicit standalone-HDMI waiver required')
    addon=Path(config['addon']).resolve();raw=(addon/'manifest.json').read_bytes()
    if sha(raw)!=config['addon_sha']:raise ValueError('Changed live add-on manifest')
    manifest=json.loads(raw.decode('utf-8'));package,result,frozen=paths(config)
    fixed=dict(format='pl-render-live-addon-v1',offline_package_sha256=config['expected_sha'],
               source_package_sha256=SOURCE_PACKAGE,limited_trial_seconds=30,production_authorized=False,
               boot_id=boot,frozen_directory=str(frozen),offline_directory=str(package))
    if any(type(manifest.get(k)) is not type(v) or manifest[k]!=v for k,v in fixed.items()):raise ValueError('Live add-on contract mismatch')
    files=manifest.get('files')
    if not isinstance(files,dict) or not files:raise ValueError('Missing live member pins')
    for name,want in files.items():
        if sha(file_member(addon,name).read_bytes())!=want:raise ValueError('Changed live member: '+name)
    if config.get('frozen_sha')!=SOURCE_PACKAGE:raise ValueError('Wrong frozen source pin')
    verify_frozen(frozen)
    checked=verify_package(package,config['expected_sha'])
    qualified=validate_offline_evidence(package,config['offline'],boot,config['expected_sha'])
    power=Path(usb_root)
    usb={n:(power/n).read_text().strip() for n in ('idVendor','idProduct','power/control','power/runtime_status')}
    if list(usb.values())!=['046d','0825','on','active']:raise ValueError('USB camera must already be046d:0825 on/active; no automatic wake')
    if check_available:
        for marker in markers(config):
            if marker.exists() or marker.is_symlink():raise ValueError('Existing one-shot demo reservation; never restart')
    return dict(offline_qualified=qualified['offline_qualified'],files_verified=True,hardware_started=False,
                package_sha256=checked['package_sha256'],live_bundle_sha256=config['addon_sha'],boot_id=boot,
                usb=usb,production_authorized=False,notice='File-only live precheck; no device imported or started')


def binding(config):
    manifest=json.loads((Path(config['package'])/'manifest.json').read_text(encoding='utf-8'))
    return dict(stage='full_demo',boot_id=config['boot'],package_sha256=config['expected_sha'],
                live_bundle_sha256=config['addon_sha'],run_token=config['run_token'],
                bit_sha256=manifest['bit_sha256'],hwh_sha256=manifest['hwh_sha256'])


def markers(config):
    package,result,frozen=paths(config)
    return (frozen/'offline_results'/('skin_retry_demo_once_'+config['boot']+'.json'),
            package/'offline_results'/('render_live_once_'+config['boot']+'.json'))


def reservation(config):
    return dict(binding(config),result=str(Path(config['result']).resolve()),
                source_package_sha256=SOURCE_PACKAGE,config_sha256=sha(json.dumps(config,sort_keys=True).encode('utf-8')))


def exclusive(path,value):
    path=Path(path)
    with path.open('x',encoding='utf-8') as f:json.dump(value,f,sort_keys=True);f.flush();os.fsync(f.fileno())
    if hasattr(os,'O_DIRECTORY'):
        fd=os.open(str(path.parent),os.O_RDONLY|os.O_DIRECTORY)
        try:os.fsync(fd)
        finally:os.close(fd)


def reserve(config):
    package,result,frozen=paths(config)
    selected=markers(config)
    if any(p.exists() or p.is_symlink() for p in selected):raise FileExistsError('A demo is already reserved')
    result.parent.mkdir(exist_ok=True);result.mkdir(exist_ok=False)
    # Never roll back a partial reservation: failure is not permission to retry.
    for marker in selected:exclusive(marker,reservation(config))
    write_json(result/'launch.json',config)
    return result


def verify_reservation(config):
    want=reservation(config)
    for path in markers(config):
        if path.is_symlink() or json.loads(path.read_text(encoding='utf-8'))!=want:raise ValueError('Foreign live reservation')
    stored=Path(config['result'])/'launch.json'
    if stored.is_symlink() or json.loads(stored.read_text(encoding='utf-8'))!=config:raise ValueError('Changed live launch configuration')


def claim_child(config,current_boot):
    if current_boot!=config['boot']:raise ValueError('Boot changed before claiming worker')
    verify_reservation(config)
    exclusive(Path(config['result'])/'claim.json',dict(binding(config),pid=os.getpid()))


def confirm_empty(config,token,current_boot):
    if current_boot!=config['boot']:raise ValueError('Old-boot confirmation')
    verify_reservation(config);result=Path(config['result'])
    state=json.loads((result/'status.json').read_text(encoding='utf-8'))
    if ((result/'exit.json').exists() or state.get('state')!='WAITING_FOR_EMPTY_CONFIRMATION' or
        not isinstance(token,str) or not token or state.get('empty_token')!=token or
        any(state.get(k)!=v for k,v in binding(config).items())):raise ValueError('Not this waiting live run/token')
    value=dict(binding(config),empty_token=token,kind='demo_empty')
    exclusive(result/'demo_empty_confirmed.json',value)
    return value


def valid_empty(config,token):
    path=Path(config['result'])/'demo_empty_confirmed.json'
    if not path.exists():return False
    if path.is_symlink():return False
    return json.loads(path.read_text(encoding='utf-8'))==dict(binding(config),empty_token=token,kind='demo_empty')


def load_runtime(config):
    """Execute unchanged authenticated legacy modules in a private namespace."""
    frozen=Path(config['frozen']).resolve();verify_frozen(frozen)
    if str(frozen) not in sys.path:sys.path.insert(0,str(frozen))
    name='_render_frozen_'+sha(str(frozen).encode())[:16]
    if name not in sys.modules:
        spec=importlib.util.spec_from_file_location(name,str(frozen/'board/__init__.py'),submodule_search_locations=[str(frozen/'board')])
        module=importlib.util.module_from_spec(spec);sys.modules[name]=module;spec.loader.exec_module(module)
    resources=importlib.import_module(name+'.live_resources')
    loop=importlib.import_module(name+'.live_loop')
    saving=importlib.import_module(name+'.live_worker')
    source=(Path(config['addon'])/'person_skin.py').read_bytes()
    if sha(source)!='5371bc7a8d2cfd02c73b367a41d3e3aaa727f32ebf7e9188b9cc877f694f82c5':raise ValueError('Changed approved skin/person segmentation')
    algorithm=types.ModuleType('pinned_person_skin');exec(compile(source,'pinned/person_skin.py','exec'),algorithm.__dict__)
    return types.SimpleNamespace(resources=resources,loop=loop,saving=saving,algorithm=algorithm)


def audit_result(runtime,background,samples):return runtime.algorithm.audit_samples(background,samples)


def save_result(runtime,result,report,rows,samples,background,journal):
    durable=types.SimpleNamespace(report=lambda value:write_json(Path(result)/'final_report.json',value),emit=journal.emit)
    runtime.saving.save_results(result,report,rows,samples,background,durable)


def run_render_demo(config,journal,factory=None,clock=None,sleep=None):
    """Only an admitted child calls this. No auto-retry or CPU-render fallback."""
    from pl_render.ownership import JointLedger
    clock=clock or time.monotonic;sleep=sleep or time.sleep
    result=Path(config['result']);owner=None;rows=[];samples=[];background=None;runtime=None
    report=dict(binding(config),pid=os.getpid(),execution_success=False,errors=[],cleanup_safe=False,
                demonstration_only=True,promotion_allowed=False,production_promotion_allowed=False,
                requested_live_seconds=30,operator_approved_full_demo=True,
                standalone_hdmi_test_performed=False,static_hdmi_visual_prerequisite_waived=True,
                display_confirmed=False,camera_started=False,hdmi_started=False,
                segmentation_accelerated_in_pl=False,render_accelerated_in_pl=False,
                style='xuan-ink-trail20-pl-v1',background_animated=False,display_uses_camera_rgb=False,
                temporal_ink_trail=True,trail_duration_s=1.5,trail_opacity_cap=.20,ps_time_history_retained=True,
                offline_result=config['offline'],source_package_sha256=SOURCE_PACKAGE)
    def check_stop():
        if (result/'stop_requested').exists():raise KeyboardInterrupt('Cooperative demo stop')
    def emit(**values):check_stop();journal.emit(**values)
    safe=True
    try:
        verify_context(config,boot_id(),usb_root=USB_ROOT,check_available=False)
        verify_reservation(config);check_stop()
        claim=result/'claim.json'
        if (claim.is_symlink() or not claim.is_file() or
            json.loads(claim.read_text(encoding='utf-8'))!=dict(binding(config),pid=os.getpid())):
            raise ValueError('Missing current-process child claim')
        emit(state='IMPORT_RUNTIME_BEGIN');runtime=load_runtime(config);emit(state='IMPORT_RUNTIME_END')
        import cv2
        cv2.setNumThreads(1)
        from projection_dma.pl_prior import PlPrior
        if factory is None:
            ready,reasons=runtime.saving.environment_ready()
            if not ready:raise RuntimeError('; '.join(reasons))
            from .render_live_resources import make_resources
            factory=make_resources(runtime.resources.LiveResources)
        owner=factory(config['frozen'],config['package'],JointLedger(),emit)
        owner.open_overlay();check_stop();owner.prepare_camera();check_stop()
        token=uuid.uuid4().hex;deadline=clock()+60
        while True:
            check_stop()
            emit(state='WAITING_FOR_EMPTY_CONFIRMATION',empty_token=token,prompt='leave',remaining=max(0.,deadline-clock()))
            if clock()>=deadline:raise TimeoutError('No empty-background confirmation within60seconds')
            if valid_empty(config,token):break
            sleep(.5)
        background=owner.collect_background();check_stop();owner.prepare_segmentation()
        processor=runtime.algorithm.PersonSegmenter(background,PlPrior(background,owner.engine))
        report['segmentation_accelerated_in_pl']=True
        check_stop();display=owner.start_display();check_stop();stream=owner.start_reader()
        loop=runtime.loop
        # Bind a fresh private function namespace for THIS run. No legacy file
        # is changed; run_live_frames/audit/prompt bytes remain exact frozen bytes.
        scope=dict(loop.run_live_frames.__globals__)
        def render_mask(mask,fps,remaining_s=30.):
            image=owner.render(mask)
            return loop.overlay_prompt(image,remaining_s)
        scope['render_mask']=render_mask
        run_frames=types.FunctionType(loop.run_live_frames.__code__,scope,loop.run_live_frames.__name__,loop.run_live_frames.__defaults__)
        emit(state='LIVE',remaining=30.,prompt='enter',frames=0)
        rows,samples,elapsed=run_frames(stream,display,processor,lambda value:emit(**value),
                                      owner.check_controls,check_stop,clock=clock,rows=rows,samples=samples)
        if elapsed<30:raise RuntimeError('Full30s scheduling budget incomplete')
        report.update(loop.summarize_live(rows,elapsed));report['execution_success']=True
    except BaseException as exc:
        report['errors'].append(repr(exc))
        report['primary_failure_snapshot']=getattr(exc,'primary_failure_snapshot',None) or getattr(exc,'snapshot',None)
    finally:
        if owner is not None:
            report.update(camera_started=bool(owner.report.get('camera_started')),hdmi_started=bool(owner.report.get('hdmi_started')),
                          render_accelerated_in_pl=owner.render_calls>0,render_initialization_ms=owner.render_init_ms,
                          render_components_ms=list(owner.render_timings))
            try:report['release_ledger']=owner.close();report['cleanup_safe']=True
            except BaseException as exc:
                safe=False;report['execution_success']=False;report['errors'].append('cleanup: '+repr(exc))
                report['release_ledger']=owner.ledger.snapshot()
            report['hardware']=owner.snapshot()
        else:report.update(cleanup_safe=True,release_ledger=empty_ledger())
    audit_failure=None
    if safe and report['execution_success']:
        try:
            report['sample_audit']=audit_result(runtime,background,samples)
            if report['sample_audit']['sample_count']<3 or report['sample_audit']['different_pixels']!=0:raise ValueError('Sample audit needs>=3zero-difference frames')
        except BaseException as exc:
            audit_failure=exc;report['execution_success']=False;report['errors'].append('audit: '+repr(exc))
    if rows and not report['execution_success']:report.update(partial_measurements=True,displayed_frames=len(rows))
    report['hardware_started']=journal.hardware_started
    try:
        # Device cleanup is already proven, but a failed verification must not
        # release the global owner lease or publish a normal stopped outcome.
        if audit_failure is not None:raise audit_failure
        if safe and runtime is not None:
            journal.emit(state='AUDITING_AND_SAVING',prompt='saving')
            save_result(runtime,result,report,rows,samples,background,journal)
        else:write_json(result/'final_report.json',report)
        journal.emit(state=('FINISHED_SAFELY' if report['execution_success'] else 'STOPPED_SAFELY') if safe else 'UNSAFE_REBOOT_REQUIRED',
                     report=str(result/'final_report.json'),cleanup_safe=safe,promotion_allowed=False)
        if safe and owner is not None:owner.release_lease()
    except BaseException as exc:
        report['execution_success']=False
        if exc is not audit_failure:report['errors'].append('save/evidence: '+repr(exc))
        if runtime is not None:
            try:runtime.saving.quarantine_archive(result)
            except BaseException:pass
        if owner is not None:
            owner.ledger.block_releases('Live audit/save/evidence transaction failed')
            report['release_ledger']=owner.ledger.snapshot();report['hardware']=owner.snapshot()
        try:write_json(result/'final_report.json',report);journal.emit(state='UNSAFE_REBOOT_REQUIRED',error=repr(exc),cleanup_safe=report['cleanup_safe'])
        except BaseException:pass
        raise RetainedEvidenceError(owner,getattr(owner,'lease',None),report['errors']) from exc
    if not safe:report['_retained_owner']=owner
    return report,safe
