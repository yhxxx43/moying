"""Pure-stdlib file/qualification gates. Device code is imported only on request."""
import hashlib
import json
import math
from pathlib import Path,PurePosixPath
import xml.etree.ElementTree as ET

STAGES=('precheck','load','identity','render_synthetic','render_saved_masks')
SOURCE_PACKAGE='9f50fbd2b3daaadb548b72dfadc16be8b22a06960192a2c71c4fdf245b75492d'


def validate_launch(package,expected_sha,stage,boot,current_boot,approved_stage,previous):
    import uuid
    if stage not in STAGES or approved_stage!=stage:raise ValueError('Explicit approval for this one diagnostic stage required')
    if boot!=current_boot:raise ValueError('Boot changed; no automatic reuse')
    try:
        if str(uuid.UUID(boot))!=boot:raise ValueError('Noncanonical boot UUID')
    except (ValueError,AttributeError,TypeError):raise ValueError('Explicit current boot UUID required')
    checked=verify_package(package,expected_sha)
    index=STAGES.index(stage)
    if index==0:
        if previous is not None:raise ValueError('Precheck has no predecessor')
    else:
        if previous is None:raise ValueError('Explicit previous successful stage required')
        _completed(package,previous,expected_sha,boot,STAGES[index-1])
    return checked


def digest(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def file_member(root,name):
    p=PurePosixPath(name)
    if not name or '\\' in name or ':' in name or p.is_absolute() or '..' in p.parts or str(p)!=name:
        raise ValueError('Unsafe package member')
    target=Path(root)/name
    if target.is_symlink() or Path(root).resolve() not in target.resolve().parents:raise ValueError('Member escapes package')
    return target


def verify_package(package,expected_sha=None):
    package=Path(package).resolve();actual=digest(package/'manifest.json')
    if expected_sha is not None and actual!=expected_sha:raise ValueError('New package hash mismatch')
    m=json.loads((package/'manifest.json').read_text(encoding='utf-8'))
    if (m.get('format')!='pl-render-offline-v1' or m.get('source_package_sha256')!=SOURCE_PACKAGE or
        m.get('live_authorized') is not False or m.get('production_authorized') is not False or
        m.get('allowed_stages')!=list(STAGES) or m.get('render_id')!=0x504c5231 or
        m.get('render_version')!=0x10000 or m.get('register_address')!=0x43c10000):raise ValueError('Wrong package contract')
    stem=m.get('stem')
    if not isinstance(stem,str) or not stem or stem=='projection_1791971168479877' or '/' in stem or '\\' in stem:
        raise ValueError('New hardware stem required')
    files=m.get('files')
    if not isinstance(files,dict) or not files:raise ValueError('Missing new file manifest')
    for name,want in files.items():
        if digest(file_member(package,name))!=want:raise ValueError('Changed package file: '+name)
    for suffix,key in (('.bit','bit_sha256'),('.hwh','hwh_sha256')):
        if files.get(stem+suffix)!=m.get(key):raise ValueError('Hardware/manifest pin disagreement')
    tree=ET.fromstring(file_member(package,stem+'.hwh').read_bytes())
    if not any(n.attrib.get('INSTANCE')=='render_accel_0' for n in tree.iter('MODULE')):raise ValueError('Old HWH missing render')
    ranges=[n.attrib for n in tree.iter('MEMRANGE') if n.attrib.get('INSTANCE')=='render_accel_0']
    if not any(int(n.get('BASEVALUE','-1'),0)==0x43c10000 and int(n.get('HIGHVALUE','-1'),0)==0x43c1ffff for n in ranges):
        raise ValueError('New register range missing')
    return dict(manifest=m,package_sha256=actual,files_verified=True,hardware_started=False)


def precheck(package,expected_sha=None):
    checked=verify_package(package,expected_sha)
    return dict(files_verified=True,hardware_started=False,precheck_only=True,
                package_sha256=checked['package_sha256'],notice='File-only check; no imports of PYNQ or numerical/device APIs')


def _completed(package,result,expected_sha,boot,stage):
    checked=verify_package(package,expected_sha);m=checked['manifest']
    package=Path(package).resolve();result=Path(result).resolve()
    if package/'offline_results' not in result.parents:raise ValueError('Foreign result directory')
    records=[]
    for name in ('status.json','final_report.json','exit.json'):
        try:records.append(json.loads((result/name).read_text(encoding='utf-8')))
        except (ValueError,OSError):raise ValueError('Missing completed/actual waited-exit evidence')
    state,report,exit_record=records
    for record in records:
        if (record.get('stage')!=stage or record.get('boot_id')!=boot or record.get('package_sha256')!=checked['package_sha256']
            or record.get('bit_sha256')!=m['bit_sha256'] or record.get('hwh_sha256')!=m['hwh_sha256']):raise ValueError('Foreign stage/package/hardware/boot')
    if (state.get('state')!='FINISHED_SAFELY' or report.get('execution_success') is not True or report.get('errors')!=[] or
        type(exit_record.get('exit_code')) is not int or exit_record['exit_code']!=0 or
        type(state.get('pid')) is not int or state['pid']<=0 or state['pid']!=report.get('pid') or state['pid']!=exit_record.get('waited_pid')):
        raise ValueError('Not a successfully exited worker')
    if not state.get('run_token') or any(r.get('run_token')!=state['run_token'] for r in records):raise ValueError('Worker claim mismatch')
    ledger=report.get('release_ledger',{})
    if (report.get('cleanup_safe') is not True or ledger.get('all_released') is not True or
        ledger.get('release_failed') is not False or ledger.get('release_blocks')!=[]):raise ValueError('Cleanup proof missing')
    groups=ledger.get('groups',{})
    if set(groups)!={'seg','video','render'} or any(g.get('submitted') and not g.get('halted') for g in groups.values()):
        raise ValueError('Unhalted/missing resource group')
    if report.get('camera_started') is not False or report.get('hdmi_started') is not False:raise ValueError('Unexpected live devices')
    if report.get('segmentation_started') is not False:raise ValueError('Unexpected segmentation DMA')
    if any(groups[g].get('submitted') is not False or groups[g].get('halted') is not False for g in ('seg','video')):
        raise ValueError('Offline render diagnostics may not submit seg/video')
    if stage=='precheck':
        if (state.get('hardware_started') is not False or report.get('hardware_started') is not False or
            report.get('backend')!='file-only' or report.get('precheck_only') is not True or report.get('files_verified') is not True):
            raise ValueError('Missing file-only precheck observations')
    else:
        if (state.get('hardware_started') is not True or report.get('hardware_started') is not True or report.get('backend')!='pynq-mmio' or
            report.get('overlay_loaded') is not True or report.get('overlay_stem')!=m['stem']):
            raise ValueError('No proven new overlay configuration')
    if stage in ('identity','render_synthetic','render_saved_masks'):
        gpio=report.get('gpio_identity')
        if (report.get('render_id')!=0x504c5231 or report.get('render_version')!=0x10000 or
            type(gpio) is not int or gpio&0xfffffffe!=0x50524a00):raise ValueError('Missing actual identity observations')
    if not stage.startswith('render_'):
        if (groups['render'].get('submitted') is not False or groups['render'].get('halted') is not False or ledger.get('buffers')!={}):
            raise ValueError('Unexpected render activity before render stage')
    if stage.startswith('render_'):
        if report.get('different_pixels')!=0 or report.get('board_math_verified') is not True or report.get('backend')!='pynq-mmio':
            raise ValueError('Missing real render/math evidence')
        if groups['render'].get('submitted') is not True or groups['render'].get('halted') is not True:raise ValueError('No render halt proof')
        buffers=ledger.get('buffers',{});ranges=[]
        for name,size in zip(('input','paper','ink','edge','output'),(153600,1228800,1228800,1228800,921600)):
            b=buffers.get('render_'+name,{})
            if (b.get('valid') is not True or b.get('released') is not True or b.get('group')!='render' or b.get('nbytes')!=size
                or type(b.get('address')) is not int or b['address']<0 or b['address']%8 or b['address']+size>0x20000000):raise ValueError('Invalid released buffer evidence')
            ranges.append((b['address'],b['address']+size))
        ranges.sort()
        if any(a[1]>b[0] for a,b in zip(ranges,ranges[1:])):raise ValueError('Overlapping render allocations')
        expected_samples=5 if stage=='render_synthetic' else 14
        rounds=report.get('rounds');count=report.get('measurement_count');rows=report.get('measurements',[])
        if (report.get('sample_count')!=expected_samples or type(rounds) is not int or rounds<3 or
            type(count) is not int or count!=expected_samples*rounds or not isinstance(rows,list) or len(rows)!=count):
            raise ValueError('Incomplete render coverage')
        if stage=='render_synthetic' and report.get('synthetic_cases')!=['empty','full','single','hole','random']:
            raise ValueError('Missing five synthetic input classes')
        if ({(r.get('round'),r.get('index')) for r in rows}!={(r,i) for r in range(rounds) for i in range(expected_samples)} or
            any(type(r.get('different_pixels')) is not int or r['different_pixels']!=0 for r in rows)):
            raise ValueError('Missing/duplicate/incorrect full frame')
        warmups=report.get('warmups',[])
        if (report.get('warmup_count')!=2 or report.get('warmup_different_pixels')!=0 or not isinstance(warmups,list) or
            len(warmups)!=2 or [r.get('index') for r in warmups]!=[0,1] or
            any(type(r.get('different_pixels')) is not int or r['different_pixels']!=0 for r in warmups)):
            raise ValueError('Missing first-frame/warmup comparisons')
    return report


def validate_offline_evidence(package,result,boot,expected_sha=None):
    checked=verify_package(package,expected_sha);expected_sha=checked['package_sha256']
    current=Path(result);seen=set();final=None
    for stage in reversed(STAGES):
        if current.resolve() in seen:raise ValueError('Cyclic previous result')
        seen.add(current.resolve());report=_completed(package,current,expected_sha,boot,stage)
        if final is None:final=report
        previous=report.get('previous_result')
        if stage!='precheck':
            if not previous:raise ValueError('Missing current-boot staged chain')
            current=Path(previous)
        elif previous is not None:raise ValueError('Unexpected predecessor of precheck')
    count=final.get('measurement_count');samples=final.get('sample_count');rounds=final.get('rounds')
    if (type(count) is not int or type(samples) is not int or type(rounds) is not int or samples<14 or rounds<3 or count!=samples*rounds or
        final.get('same_input_timestamp') is not True or final.get('same_assets') is not True or final.get('cpu_oracle')!='approved-source' or
        final.get('timing_boundary')!='history_pack_flush_pl_wait_invalidate_copy'):raise ValueError('Incomplete matched benchmark')
    times=final.get('timings',{})
    for name in ('cpu_render','pl_render'):
        values=times.get(name,{})
        if values.get('count')!=count:raise ValueError('Incomplete timing samples')
        for stat in ('median_ms','p95_ms'):
            value=values.get(stat)
            if type(value) not in (float,int) or not math.isfinite(value) or value<=0:raise ValueError('Invalid timing')
    if (final.get('performance_gate_passed') is not True or times['pl_render']['median_ms']>=times['cpu_render']['median_ms'] or
        times['pl_render']['p95_ms']>=times['cpu_render']['p95_ms']):raise ValueError('No demonstrated full-boundary speedup')
    return dict(offline_qualified=True,live_authorized=False,production_authorized=False,
                package_sha256=expected_sha,boot_id=boot,result=str(Path(result).resolve()),
                notice='Actual offline qualification only; separate operator live approval still required')
