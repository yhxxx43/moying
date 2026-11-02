"""Detached live supervisor, waited-exit evidence and non-restarting views."""
import argparse
import faulthandler
import json
import math
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import traceback
from .render_live import (verify_context,verify_reservation,reserve,claim_child,binding,run_render_demo,USB_ROOT)
from .render_worker import boot_id,write_json,Journal,RetainedEvidenceError
from .render_launch import wait_process,retain_forever


class LiveHandle:
    def __init__(self,config):self.config=config;self.result=Path(config['result'])
    def status(self):
        verify_reservation(self.config);bound=binding(self.config)
        path=self.result/'status.json'
        state=json.loads(path.read_text(encoding='utf-8')) if path.exists() else dict(bound,state='LAUNCH_REQUESTED',hardware_started=False)
        if any(state.get(k)!=v for k,v in bound.items()):raise ValueError('Foreign live status binding')
        if boot_id()!=self.config['boot']:
            state.update(old_boot=True,owner_running=None,exit_code=None,notice='Historical only; never restart from this handle')
            return state
        exit_path=self.result/'exit.json'
        if exit_path.exists():
            record=json.loads(exit_path.read_text(encoding='utf-8'))
            good=(all(record.get(k)==v for k,v in bound.items()) and type(record.get('exit_code')) is int and
                  type(record.get('waited_pid')) is int and record['waited_pid']>0 and
                  ('pid' not in state or record['waited_pid']==state['pid']))
            state.update(owner_running=False if good else None,exit_code=record.get('exit_code') if good else None,
                         exit_evidence_valid=good,worker_pid=record.get('waited_pid'))
        elif (self.result/'launch_failure.json').exists():
            record=json.loads((self.result/'launch_failure.json').read_text(encoding='utf-8'))
            if any(record.get(k)!=v for k,v in bound.items()):raise ValueError('Foreign launch-failure binding')
            state.update(owner_running=False,exit_code=None,launch_failed=True,error=record['error'],
                         notice='Supervisor failed before worker spawn; reservation retained, no retry')
        elif (self.result/'supervisor_failure.json').exists():
            state.update(owner_running=None,exit_code=None,notice='Supervisor lost wait/exit proof; do not restart')
        else:state.update(owner_running=True,exit_code=None)
        return state
    def request_stop(self):
        if boot_id()!=self.config['boot']:raise ValueError('Old-boot stop rejected')
        verify_reservation(self.config)
        if (self.result/'exit.json').exists():return
        try:
            with (self.result/'stop_requested').open('x') as f:f.write('cooperative stop only\n');f.flush();os.fsync(f.fileno())
        except FileExistsError:pass


def start_once(config,usb_root=USB_ROOT):
    verify_context(config,boot_id(),usb_root=usb_root,check_available=True)
    result=reserve(config)
    Journal(result,binding(config)).emit(state='LAUNCH_REQUESTED',hardware_started=False,requested_live_seconds=30)
    with (result/'supervisor.log').open('xb') as log:
        process=subprocess.Popen([sys.executable,'-B','-m','board.render_live_launch','supervise',str(result/'launch.json')],
                                 cwd=config['addon'],stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
    print('结果目录：',str(result),'监督进程PID：',process.pid,flush=True)
    return LiveHandle(config)


def restore(config):
    verify_reservation(config)
    return LiveHandle(config)


def supervise(config_path,usb_root=USB_ROOT):
    config=json.loads(Path(config_path).read_text(encoding='utf-8'));result=Path(config['result']);process=None
    try:
        verify_context(config,boot_id(),usb_root=usb_root,check_available=False);verify_reservation(config)
        with (result/'worker.log').open('xb') as log:
            process=subprocess.Popen([sys.executable,'-B','-u','-m','board.render_live_launch','worker',str(config_path)],
                                     cwd=config['addon'],stdout=log,stderr=subprocess.STDOUT)
        wait_process(process,result,binding(config));return 0
    except BaseException as exc:
        # No fabricated wait() evidence when a child never existed or its wait
        # failed. Unknown existing child must not be advertised as stopped.
        write_json(result/('launch_failure.json' if process is None else 'supervisor_failure.json'),
                   dict(binding(config),error=repr(exc),hardware_started=False if process is None else None))
        traceback.print_exc();return 1


def worker(config_path):
    config=json.loads(Path(config_path).read_text(encoding='utf-8'));result=Path(config['result'])
    # Elect child BEFORE any journal can overwrite another owner's evidence.
    claim_child(config,boot_id());faulthandler.enable(file=sys.stderr,all_threads=True)
    journal=Journal(result,dict(binding(config),pid=os.getpid()))
    journal.emit(state='BOOTSTRAP_STARTED',hardware_started=False)
    def cooperative_signal(*unused):
        try:
            with (result/'stop_requested').open('x') as f:f.write('signal requests cooperative stop\n')
        except FileExistsError:pass
    signal.signal(signal.SIGINT,cooperative_signal);signal.signal(signal.SIGTERM,cooperative_signal)
    try:
        report,safe=run_render_demo(config,journal)
        if not safe:retain_forever(report.get('_retained_owner'))
        return 0 if report['execution_success'] else 1
    except RetainedEvidenceError as exc:
        traceback.print_exc()
        if exc.owner is not None or exc.lease is not None:retain_forever(exc)
        return 1


def observe(handle,wait_s=0,stop_at_waiting=False):
    if type(wait_s) not in (int,float) or not math.isfinite(wait_s) or not 0<=wait_s<=60:raise ValueError('Read-only observation budget must be0..60s')
    deadline=time.monotonic()+wait_s;last=None
    messages={'leave':'请离开镜头，保持背景和灯光不动。','enter':'现在可以进入镜头，缓慢抬手。','saving':'设备已停止，正在审计和保存。'}
    while True:
        state=handle.status();signature=(state.get('state'),state.get('prompt'),state.get('frames'),state.get('remaining'),state.get('owner_running'))
        if signature!=last:
            print('状态：',state.get('state'),'帧：',state.get('frames'),'剩余：',state.get('remaining'),flush=True)
            if state.get('prompt') in messages:print(messages[state['prompt']],flush=True)
            last=signature
        if state.get('owner_running') is not True or (stop_at_waiting and state.get('state')=='WAITING_FOR_EMPTY_CONFIRMATION') or time.monotonic()>=deadline:break
        time.sleep(.5)
    print('结果目录：',str(handle.result),flush=True)
    print(json.dumps(state,ensure_ascii=False,indent=2),flush=True)
    report=handle.result/'final_report.json'
    if state.get('owner_running') is False:
        if report.is_file():print(report.read_text(encoding='utf-8'),flush=True)
        if state.get('exit_code')!=0:print('未成功完成或异常退出：保留记录，不重启本阶段，不运行其他硬件。',flush=True)
        elif not report.is_file():print('缺少最终报告，不能判为通过。',flush=True)
    else:print('未确认真实退出：仅重复查看，不重复启动、不强杀。',flush=True)
    return state


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('mode',choices=['supervise','worker']);parser.add_argument('config');args=parser.parse_args()
    sys.exit(supervise(args.config) if args.mode=='supervise' else worker(args.config))
