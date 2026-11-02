"""One-shot detached supervisor; real waited exit is separate from worker state."""
import argparse
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import traceback
import uuid
from .render_offline import validate_launch,verify_package
from .render_worker import boot_id,write_json,execute_stage,RetainedEvidenceError


def wait_process(process,result,bound):
    code=process.wait()
    record=dict(bound,pid=process.pid,waited_pid=process.pid,exit_code=code,timestamp=time.time())
    write_json(Path(result)/'exit.json',record)
    return record


class Handle:
    def __init__(self,result,boot,expected_sha=None):self.result=Path(result);self.boot=boot;self.expected_sha=expected_sha
    def status(self):
        path=self.result/'status.json'
        state=json.loads(path.read_text(encoding='utf-8')) if path.exists() else dict(state='LAUNCH_REQUESTED',boot_id=self.boot)
        if boot_id()!=self.boot:
            state.update(old_boot=True,owner_running=None,notice='Historical record only; do not restart automatically')
            return state
        exit_path=self.result/'exit.json'
        if exit_path.exists():
            actual=json.loads(exit_path.read_text(encoding='utf-8'))
            keys=('boot_id','stage','package_sha256','run_token','bit_sha256','hwh_sha256')
            matches=all(actual.get(k)==state.get(k) for k in keys if k in state)
            if ('pid' in state and actual.get('waited_pid')!=state['pid']):matches=False
            if self.expected_sha is not None and actual.get('package_sha256')!=self.expected_sha:matches=False
            if not matches or type(actual.get('exit_code')) is not int:
                state.update(owner_running=None,exit_code=None,exit_evidence_valid=False,notice='Exit record mismatch; do not start another stage')
            else:state.update(owner_running=False,exit_code=actual['exit_code'],worker_pid=actual['waited_pid'],exit_evidence_valid=True)
        else:state.update(owner_running=True,exit_code=None)
        return state
    def request_stop(self):
        if boot_id()!=self.boot:raise ValueError('Old-boot handle')
        if (self.result/'exit.json').exists():return
        try:
            with (self.result/'stop_requested').open('x') as f:f.write('cooperative stop only\n');f.flush();os.fsync(f.fileno())
        except FileExistsError:pass


def start_once(package,expected_sha,stage,boot,previous,approved_stage):
    package=Path(package).resolve()
    checked=validate_launch(package,expected_sha,stage,boot,boot_id(),approved_stage,previous)
    results=package/'offline_results';results.mkdir(exist_ok=True)
    token=uuid.uuid4().hex
    once=results/('render_stage_once_'+boot+'_'+stage+'.json')
    # Any existing file, including a dangling link, permanently refuses reuse.
    result=results/(stage+'_'+token[:12])
    with once.open('x',encoding='utf-8') as f:json.dump(dict(boot_id=boot,stage=stage,run_token=token,result=str(result),package_sha256=expected_sha),f);f.flush();os.fsync(f.fileno())
    result.mkdir(exist_ok=False)
    config=dict(package=str(package),result=str(result),expected_sha=expected_sha,stage=stage,boot=boot,
                previous=str(Path(previous).resolve()) if previous else None,run_token=token,
                approved_stage=approved_stage,bit_sha256=checked['manifest']['bit_sha256'],hwh_sha256=checked['manifest']['hwh_sha256'])
    write_json(result/'launch.json',config)
    # The supervisor owns child wait(), independent of browser/kernel lifetime.
    with (result/'supervisor.log').open('xb') as log:
        process=subprocess.Popen([sys.executable,'-B','-m','board.render_launch','supervise',str(result/'launch.json')],
                                 cwd=str(package),stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
    print('结果目录：',str(result),'监督进程PID：',process.pid,flush=True)
    return Handle(result,boot,expected_sha)


def validate_reservation(config,config_path):
    package=Path(config['package']).resolve();result=Path(config['result']).resolve()
    if result.parent!=package/'offline_results' or result.is_symlink():raise ValueError('Foreign result directory')
    if Path(config_path).resolve()!=result/'launch.json':raise ValueError('Foreign launch path')
    once=package/'offline_results'/('render_stage_once_'+config['boot']+'_'+config['stage']+'.json')
    if once.is_symlink():raise ValueError('Symlink reservation')
    record=json.loads(once.read_text(encoding='utf-8'))
    record['result']=str(Path(record['result']).resolve())
    expected=dict(boot_id=config['boot'],stage=config['stage'],run_token=config['run_token'],
                  result=str(result),package_sha256=config['expected_sha'])
    if record!=expected:raise ValueError('Reservation/launch mismatch')


def restore_handle(package,expected_sha,stage,boot):
    if boot!=boot_id():raise ValueError('Boot changed; historical view only')
    if stage not in ('precheck','load','identity','render_synthetic','render_saved_masks'):raise ValueError('Unknown stage')
    package=Path(package).resolve();verify_package(package,expected_sha)
    once=package/'offline_results'/('render_stage_once_'+boot+'_'+stage+'.json')
    if once.is_symlink():raise ValueError('Symlink reservation')
    record=json.loads(once.read_text(encoding='utf-8'));result=Path(record['result']).resolve()
    if result.parent!=package/'offline_results':raise ValueError('Foreign result directory')
    config=json.loads((result/'launch.json').read_text(encoding='utf-8'))
    if (Path(config.get('package','')).resolve()!=package or config.get('expected_sha')!=expected_sha or
        config.get('boot')!=boot or config.get('stage')!=stage):raise ValueError('Foreign launch configuration')
    validate_reservation(config,result/'launch.json')
    return Handle(result,boot,expected_sha)


def supervise(config_path):
    config=json.loads(Path(config_path).read_text(encoding='utf-8'))
    validate_launch(config['package'],config['expected_sha'],config['stage'],config['boot'],boot_id(),config['approved_stage'],config['previous'])
    validate_reservation(config,config_path)
    result=Path(config['result'])
    with (result/'worker.log').open('xb') as log:
        process=subprocess.Popen([sys.executable,'-B','-m','board.render_launch','worker',str(config_path)],
                                 cwd=config['package'],stdout=log,stderr=subprocess.STDOUT)
    bound=dict(stage=config['stage'],boot_id=config['boot'],package_sha256=config['expected_sha'],run_token=config['run_token'],
               bit_sha256=config['bit_sha256'],hwh_sha256=config['hwh_sha256'])
    wait_process(process,result,bound)


def retain_forever(owner):
    # No process kill/reset/free may stand in for an AXI drain proof. Keep this
    # object (and its CMA references/lease) live until an operator resets board.
    print('UNSAFE_REBOOT_REQUIRED：保留所有者和内存，不再运行硬件；请人工处理。',flush=True)
    signal.signal(signal.SIGINT,lambda *_:None)
    signal.signal(signal.SIGTERM,lambda *_:None)
    while owner is not None:time.sleep(10)


def worker(config_path):
    config=json.loads(Path(config_path).read_text(encoding='utf-8'));result=Path(config['result'])
    validate_launch(config['package'],config['expected_sha'],config['stage'],config['boot'],boot_id(),config['approved_stage'],config['previous'])
    validate_reservation(config,config_path)
    def cooperative_signal(*unused):
        try:
            with (result/'stop_requested').open('x') as f:f.write('signal requests cooperative stop\n')
        except FileExistsError:pass
    signal.signal(signal.SIGINT,cooperative_signal);signal.signal(signal.SIGTERM,cooperative_signal)
    try:
        report,safe=execute_stage(config['package'],result,config['expected_sha'],config['stage'],config['boot'],config['previous'],config['run_token'])
        if not safe:retain_forever(report)
        return 0 if report['execution_success'] else 1
    except RetainedEvidenceError as exc:
        traceback.print_exc()
        if exc.owner is not None or exc.lease is not None:retain_forever(exc)
        return 1


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('mode',choices=['supervise','worker']);parser.add_argument('config')
    args=parser.parse_args()
    if args.mode=='supervise':supervise(args.config)
    else:sys.exit(worker(args.config))
