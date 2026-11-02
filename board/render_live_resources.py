"""Extend verified legacy resources; no numerical/device imports at import time."""
import hashlib
import json
from pathlib import Path
import time
import zipfile


def make_resources(base):
    class LiveRenderResources(base):
        def __init__(self,frozen,package,ledger,event):
            super().__init__(frozen,ledger,event)
            self.render_package=Path(package);self.render_engine=None;self.render_buffers=None
            self.renderer=None;self.render_init_ms=0.;self.render_calls=0;self.render_timings=[]

        def open_overlay(self):
            from .owner_lease import OwnerLease
            import pynq
            if str(pynq.__version__)!='2.5':raise RuntimeError('Requires PYNQ2.5')
            self.lease=OwnerLease();self.lease.acquire()
            manifest=json.loads((self.render_package/'manifest.json').read_text(encoding='utf-8'))
            self.event(state='BEFORE_OVERLAY_LOAD',hardware_started=True,stem=manifest['stem'])
            self.overlay_attempted=True
            try:self.overlay=pynq.Overlay(str(self.render_package/(manifest['stem']+'.bit')),download=True)
            except BaseException:
                self.ledger.block_releases('Overlay load attempted but not proven complete');raise
            self.report.update(ips=sorted(self.overlay.ip_dict),overlay_stem=manifest['stem'],overlay_loaded=True)
            self.event(state='AFTER_OVERLAY_LOAD')

        def _initialize_render(self):
            import cv2
            import numpy as np
            from pynq import allocate,MMIO
            from pl_render.reference import assets_from_approved
            from pl_render.buffers import RenderBuffers
            from pl_render.engine import RenderEngine
            from pl_render.renderer import HardwareInkRenderer
            from pl_render.history import AlphaHistory
            begin=time.perf_counter();self.event(state='RENDER_INITIALIZING',hardware_started=True)
            manifest=json.loads((self.render_package/'manifest.json').read_text(encoding='utf-8'))
            name='inputs/snapshot/approved_visual.zip';raw=(self.render_package/name).read_bytes()
            if hashlib.sha256(raw).hexdigest()!=manifest['files'][name]:raise ValueError('Changed qualified render artwork')
            import io
            with zipfile.ZipFile(io.BytesIO(raw)) as z:
                paper=cv2.imdecode(np.frombuffer(z.read('assets/ink_paper.png'),np.uint8),1)
            assets=assets_from_approved(paper)
            desc=self.overlay.ip_dict['render_accel_0/s_axi']
            if int(desc['phys_addr'])!=0x43c10000 or int(desc['addr_range'])!=65536:raise ValueError('Unexpected render MMIO range')
            self.render_buffers=RenderBuffers(allocate,self.ledger,assets)
            # Register all partial allocations before constructing/reading MMIO.
            # RenderEngine itself fences an uncertain MMIO read; no recovery reads.
            try:mmio=MMIO(int(desc['phys_addr']),int(desc['addr_range']))
            except BaseException:
                self.ledger.block_releases('Render MMIO construction failed');raise
            self.render_engine=RenderEngine(mmio,self.render_buffers,self.ledger)
            self.renderer=HardwareInkRenderer(self.render_engine,self.render_buffers,AlphaHistory())
            self.render_init_ms=(time.perf_counter()-begin)*1000
            self.report.update(render_id=0x504c5231,render_version=0x10000,render_initialization_ms=self.render_init_ms)

        def render(self,mask):
            if (not self.report.get('camera_started') or not self.report.get('hdmi_started') or
                self.engine is None or self.reader is None):raise RuntimeError('Camera/background/seg/display/reader must precede render initialization')
            if self.renderer is None:self._initialize_render()
            # Intentionally mask-only. Camera RGB is never an argument here.
            image=self.renderer(mask)
            self.render_calls+=1;self.render_timings.append(dict(self.renderer.last_timings_ms))
            return image

        def close(self):
            if self.closed:return self.ledger.snapshot()
            if self.close_failed:raise RuntimeError('Previous cleanup failed; no retry')
            errors=[]
            def attempt(name,operation):
                try:operation()
                except BaseException as exc:errors.append(dict(operation=name,error=repr(exc)))
            if self.engine is not None:attempt('seg_stop',self.engine.close)
            if self.render_engine is not None:attempt('render_stop',self.render_engine.close)
            if self.display is not None:attempt('vdma_stop',self.display.close)
            if self.vtc is not None:attempt('vtc_stop',lambda:self.vtc.write(0,0))
            reader_stopped=True
            if self.reader is not None:
                try:self.reader.close()
                except BaseException as exc:
                    reader_stopped=False;errors.append(dict(operation='reader_join',error=repr(exc)))
            self.report['reader_stopped']=reader_stopped
            if reader_stopped:
                if self.exposure is not None:attempt('exposure_restore',self.exposure.restore)
                if self.cap is not None:attempt('camera_release',self.cap.release)
            if self.exposure is not None:self.report['exposure_lock']=self.exposure.report
            if self.overlay_attempted and self.overlay is None:errors.append(dict(operation='overlay',error='Configuration unknown'))
            if self.allocation_uncertain:errors.append(dict(operation='allocation',error='Legacy allocation rollback unknown'))
            # Crucial difference from old two-group cleanup: reject ALL frees
            # before commit if ANY device/reader/restore/initialization failed.
            if errors:self.ledger.block_releases('Live cleanup failed: '+repr(errors))
            for name in self.ledger.entries:self.ledger.request_release(name)
            attempt('joint_release',self.ledger.commit_releases)
            self.cleanup_errors=errors;self.report['buffers']=self.ledger.snapshot()
            if errors:
                self.close_failed=True;raise RuntimeError('Joint cleanup not proven safe: '+repr(errors))
            self.closed=True
            return self.ledger.snapshot()
    return LiveRenderResources
