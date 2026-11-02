"""Manual-stop resident lifecycle. No hardware imports until invoked by child."""
from collections import deque
import json
import os
from pathlib import Path
import time
import uuid
from .render_live import load_runtime,audit_result
from .render_worker import write_json,RetainedEvidenceError,empty_ledger
from .resident_support import binding,StopRequested


def check_identity(owner):
    desc=owner.overlay.ip_dict['render_accel_0/s_axi']
    if int(desc['phys_addr'])!=0x43c10000 or int(desc['addr_range'])!=65536:raise ValueError('Wrong render address')
    try:
        mmio=owner.mmio('render_accel_0/s_axi')
        expected=((0,0x504c5231),(4,0x10000),(12,33),(0x34,0x028001e0),(0x38,0x014000f0),(0x3c,0),(0x40,0))
        values=[int(mmio.read(offset)) for offset,want in expected]
        gpio=int(owner.mmio('ctrl_gpio').read(8))
    except BaseException:
        owner.ledger.block_releases('Uncertain startup identity MMIO');raise
    if values!=[want for offset,want in expected] or gpio&0xfffffffe!=0x50524a00:raise ValueError('Wrong identity or non-idle render core')
    owner.report.update(render_id=values[0],render_version=values[1],gpio_identity=gpio)


def run_resident(config,journal,factory=None,clock=time.monotonic,sleep=time.sleep,stop=None):
    result=Path(config['result']);owner=None;runtime=None;safe=True;stats={};samples=[];background=None;preparing=False
    report=dict(binding(config),pid=os.getpid(),execution_success=False,cleanup_safe=False,errors=[],operator_stopped=False,
                demonstration_only=True,promotion_allowed=False,production_authorized=False,
                duration_limit_s=None,style='xuan-ink-trail20-pl-v1',trail_duration_s=1.5,trail_opacity_cap=.20,
                display_uses_camera_rgb=False,ps_time_history_retained=True,offline_chain_required=False,
                startup_checks_are_not_offline_qualification=True,static_hdmi_visual_prerequisite_waived=True,
                camera_started=False,hdmi_started=False,segmentation_accelerated_in_pl=False,render_accelerated_in_pl=False,
                completion_requires_waited_exit=True)
    combined=config.get('mode')=='combined'
    if combined:report.update(style='shadow-and-ink-v1',style_backends=dict(shadow='cpu',ink='pl'),
                              pause_keeps_camera_running=True,camera_recovery_implemented=False)
    def check_stop():
        path=result/'stop_requested'
        if path.is_symlink():raise ValueError('Symlink stop request')
        if path.exists():
            if json.loads(path.read_text())!=dict(binding(config),kind='cooperative_stop'):raise ValueError('Foreign stop request')
            raise StopRequested('Operator requested stop')
        if stop is not None and stop():raise StopRequested('Operator requested stop')
    def event(**values):check_stop();journal.emit(**values)
    try:
        if json.loads((result/'claim.json').read_text())!=dict(binding(config),pid=os.getpid()):raise ValueError('Missing current-process claim')
        runtime=load_runtime(config)
        import cv2
        cv2.setNumThreads(1)
        from projection_dma.pl_prior import PlPrior
        from pl_render.ownership import JointLedger
        from .resident_loop import run_frames
        if factory is None:
            ready,reasons=runtime.saving.environment_ready()
            if not ready:raise RuntimeError('; '.join(reasons))
            if combined:
                from .combined_resources import make_resources
                factory=make_resources(runtime.resources.LiveResources,config['addon'])
            else:
                from .render_live_resources import make_resources
                factory=make_resources(runtime.resources.LiveResources)
        owner=factory(config['frozen'],config['package'],JointLedger(),event)
        owner.render_timings=deque(maxlen=120)
        owner.open_overlay();check_stop();check_identity(owner);check_stop()
        preparing=True;owner.prepare_camera();check_stop()
        token=uuid.uuid4().hex;deadline=clock()+60
        while True:
            check_stop();journal.emit(state='WAITING_FOR_EMPTY_CONFIRMATION',empty_token=token,prompt='leave')
            confirmation=result/'empty.json'
            if confirmation.is_symlink():raise ValueError('Symlink empty confirmation')
            if confirmation.exists():
                if json.loads(confirmation.read_text())!=dict(binding(config),empty_token=token):raise ValueError('Foreign empty confirmation')
                break
            if clock()>=deadline:raise TimeoutError('No empty-background confirmation within60seconds')
            sleep(.5)
        background=owner.collect_background();check_stop();owner.prepare_segmentation()
        processor=runtime.algorithm.PersonSegmenter(background,PlPrior(background,owner.engine))
        report['segmentation_accelerated_in_pl']=True
        display=owner.start_display();check_stop();stream=owner.start_reader();check_stop()
        journal.emit(state='LIVE',frames=0,prompt='running')
        if combined:
            from .combined_control import Controller
            from .combined_loop import run_frames as run_combined
            run_combined(stream,display,processor,owner.render,event,owner.check_controls,check_stop,
                         clock,stats,samples,controller=Controller(config),set_style=owner.set_style)
        else:
            run_frames(stream,display,processor,owner.render,event,owner.check_controls,check_stop,clock,stats,samples)
        report['operator_stopped']=bool(stats.get('operator_stopped'))
        report['execution_success']=report['operator_stopped'] and stats.get('displayed_frames',0)>0
    except StopRequested:report['operator_stopped']=True
    except BaseException as exc:report['errors'].append(repr(exc)[:1500])
    finally:
        if owner is not None:
            if preparing and owner.cap is None:
                owner.ledger.block_releases('Camera constructor ownership uncertain')
            try:report['release_ledger']=owner.close();report['cleanup_safe']=True
            except BaseException as exc:
                safe=False;report['execution_success']=False;report['errors'].append('cleanup: '+repr(exc)[:1500])
                report['release_ledger']=owner.ledger.snapshot()
            report.update(camera_started=bool(owner.report.get('camera_started')),hdmi_started=bool(owner.report.get('hdmi_started')),
                          render_accelerated_in_pl=owner.render_calls>0,render_initialization_ms=owner.render_init_ms,
                          render_components_ms=list(owner.render_timings),hardware=owner.snapshot())
        else:report.update(cleanup_safe=True,release_ledger=empty_ledger())
        report.update(stats)
        report['hardware_started']=journal.hardware_started
    try:
        if safe and samples and background is not None:
            report['sample_audit']=audit_result(runtime,background,samples)
            if report['sample_audit']['different_pixels']!=0:raise ValueError('CPU sample audit mismatch')
        write_json(result/'final_report.json',report)
        journal.emit(state=('FINISHED_SAFELY' if report['execution_success'] else 'STOPPED_SAFELY') if safe else 'UNSAFE_REBOOT_REQUIRED',
                     report=str(result/'final_report.json'),cleanup_safe=report['cleanup_safe'],frames=stats.get('displayed_frames',0))
        if safe and owner is not None:owner.release_lease()
    except BaseException as exc:
        report['execution_success']=False;report['errors'].append('audit/evidence: '+repr(exc)[:1500])
        try:write_json(result/'final_report.json',report);journal.emit(state='UNSAFE_REBOOT_REQUIRED',cleanup_safe=report['cleanup_safe'])
        except BaseException:pass
        raise RetainedEvidenceError(owner,getattr(owner,'lease',None),report['errors']) from exc
    if not safe:report['_retained_owner']=owner
    return report,safe
