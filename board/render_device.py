"""Explicitly invoked PYNQ render diagnostics. No camera or HDMI APIs."""
import json
from pathlib import Path
import time
import zipfile


class PynqRenderDevice:
    backend='pynq-mmio'
    def __init__(self,package,journal):
        from pl_render.ownership import JointLedger
        self.package=Path(package);self.journal=journal;self.ledger=JointLedger()
        self.manifest=json.loads((self.package/'manifest.json').read_text(encoding='utf-8'))
        self.overlay=None;self.mmio=None;self.engine=None;self.buffers=None;self.mmio_failed=False
        self.overlay_attempted=False;self.overlay_loaded=False;self.overlay_uncertain=False

    def load(self):
        from pynq import Overlay
        self.overlay_attempted=True
        try:self.overlay=Overlay(str(self.package/(self.manifest['stem']+'.bit')),download=True)
        except BaseException:
            self.overlay_uncertain=True;self.ledger.block_releases('Overlay configuration attempted but not proven complete');raise
        self.overlay_loaded=True
        return dict(overlay_loaded=True,overlay_stem=self.manifest['stem'])

    def identity(self):
        from pynq import MMIO
        # PYNQ2.5 names module_ref register entries <instance>/<slave interface>.
        # Vendor IPs such as ctrl_gpio retain their bare instance names.
        render=self.overlay.ip_dict['render_accel_0/s_axi'];gpio=self.overlay.ip_dict['ctrl_gpio']
        if int(render['phys_addr'])!=0x43c10000 or int(render['addr_range'])!=65536:raise ValueError('Unexpected render MMIO map')
        self.mmio=MMIO(int(render['phys_addr']),int(render['addr_range']))
        try:
            identity=int(self.mmio.read(0));version=int(self.mmio.read(4))
            gpio_identity=int(MMIO(int(gpio['phys_addr']),int(gpio['addr_range'])).read(8))
        except BaseException:
            self.mmio_failed=True;self.ledger.block_releases('Identity MMIO could not be proven safe');raise
        if identity!=0x504c5231 or version!=0x10000 or gpio_identity&0xfffffffe!=0x50524a00:
            raise ValueError('Wrong new render/prior identity')
        return dict(render_id=identity,render_version=version,gpio_identity=gpio_identity)

    def run_benchmark(self,stage,event):
        import cv2
        import numpy as np
        from pynq import allocate
        from pl_render.reference import assets_from_approved
        from pl_render.buffers import RenderBuffers
        from pl_render.engine import RenderEngine
        from pl_render.renderer import HardwareInkRenderer
        from pl_render.history import AlphaHistory
        from .render_benchmark import benchmark
        from .math_probe import run as check_math
        event(state='CPU_MATH_CHECK_BEGIN')
        math_report=check_math();event(state='CPU_MATH_CHECK_PASSED')
        begin=time.perf_counter()
        archive=self.package/'inputs/snapshot/approved_visual.zip'
        with zipfile.ZipFile(str(archive)) as z:
            base_scope={};trail_scope={}
            exec(compile(z.read('ink_renderer.py'),'approved/ink_renderer.py','exec'),base_scope)
            exec(compile(z.read('ink_trail.py'),'approved/ink_trail.py','exec'),trail_scope)
            paper=cv2.imdecode(np.frombuffer(z.read('assets/ink_paper.png'),np.uint8),1)
        assets=assets_from_approved(paper);base=base_scope['InkRenderer'](paper)
        for actual,want in zip(assets,(base.background,base._ink,base._edge)):
            if not np.array_equal(actual,want):raise ValueError('CPU/PL assets not identical')
        self.buffers=RenderBuffers(allocate,self.ledger,assets)
        self.engine=RenderEngine(self.mmio,self.buffers,self.ledger)
        now=[0.];renderer=HardwareInkRenderer(self.engine,self.buffers,AlphaHistory(),lambda:now[0])
        reference=trail_scope['TrailInkRenderer'](base,clock=lambda:now[0])
        if stage=='render_synthetic':
            empty=np.zeros((240,320),np.uint8);full=np.full_like(empty,255);single=empty.copy();single[120,160]=255
            hole=full.copy();hole[120,160]=0
            random=(np.random.RandomState(11722).randint(0,2,(240,320))*255).astype(np.uint8)
            masks=[empty,full,single,hole,random]
            cases=['empty','full','single','hole','random']
        else:
            masks=[cv2.imdecode(np.frombuffer((self.package/name).read_bytes(),np.uint8),0) for name in self.manifest['mask_files']]
            if len(masks)!=14:raise ValueError('Exactly14 pinned saved masks required')
        initialized=time.perf_counter()
        details=benchmark(renderer,reference,masks,lambda t:now.__setitem__(0,t),rounds=3,event=event)
        if stage=='render_synthetic':details['synthetic_cases']=cases
        details.update(board_math_verified=True,math_report=math_report,initialization_ms=(initialized-begin)*1000,
                       ps_time_history_retained=True,tx_cacheable=True,output_cacheable=True)
        return details

    def close(self):
        if self.overlay_uncertain:raise RuntimeError('UNSAFE_REBOOT_REQUIRED: overlay configuration unknown')
        if self.mmio_failed:raise RuntimeError('UNSAFE_REBOOT_REQUIRED: identity MMIO unknown')
        if self.engine is not None:self.engine.close()
        # All allocations, including ones acquired before a constructor failed,
        # remain in the ledger. No destructor frees them behind the proof gate.
        for name in self.ledger.entries:self.ledger.request_release(name)
        self.ledger.commit_releases()
