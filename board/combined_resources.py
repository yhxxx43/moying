"""Approved CPU warm shadow plus original PL ink, one device owner."""
from collections import deque
import hashlib
from pathlib import Path
import time
from .render_live_resources import make_resources as ink_resources

WARM_SOURCE_SHA='2ebef1e57d5be220df82fd2c44d279d55efe2ffce910d1f22f8c627717da9b68'
WARM_STAGE_SHA='8153d65ca9eb664473bd71381724b3f215b44381ea68f9c211ed7ffe6f321dab'


def make_resources(base,addon):
    class CombinedResources(ink_resources(base)):
        def __init__(self,*args):
            super().__init__(*args)
            self.active_style='shadow';self.warm=None;self.shadow_calls=0
            self.shadow_timings=deque(maxlen=120);self.warm_init_ms=0.

        def set_style(self,style,reset=False):
            if style not in ('shadow','ink') or self.closed or self.close_failed:raise ValueError('Invalid style or closed owner')
            if self.ledger.release_blocks or self.ledger.release_failed:raise RuntimeError('Owner fenced; cannot switch')
            if self.render_engine is not None:self.render_engine.check_ready_for_cpu()
            if reset and self.renderer is not None:self.renderer.history.reset()
            self.active_style=style

        def _warm(self):
            import cv2
            import numpy as np
            begin=time.perf_counter();root=Path(addon)
            source=(root/'visual/warm_shadow.py').read_bytes();raw=(root/'assets/warm_stage.png').read_bytes()
            if hashlib.sha256(source).hexdigest()!=WARM_SOURCE_SHA or hashlib.sha256(raw).hexdigest()!=WARM_STAGE_SHA:
                raise ValueError('Changed approved warm artwork/renderer')
            namespace={};exec(compile(source,'approved/warm_shadow.py','exec'),namespace)
            background=cv2.imdecode(np.frombuffer(raw,np.uint8),1)
            self.warm=namespace['WarmShadowRenderer'](background)
            self.warm_init_ms=(time.perf_counter()-begin)*1000

        def render(self,mask):
            if self.active_style=='ink':return super().render(mask)
            if (not self.report.get('camera_started') or not self.report.get('hdmi_started') or
                self.engine is None or self.reader is None or self.closed or self.close_failed):
                raise RuntimeError('Live camera/seg/display/reader owner required')
            if self.ledger.release_blocks or self.ledger.release_failed:raise RuntimeError('Owner fenced')
            if self.warm is None:self._warm()
            image=self.warm(mask);self.shadow_calls+=1
            self.shadow_timings.append(dict(self.warm.last_timings_ms))
            return image

        def snapshot(self):
            return dict(super().snapshot(),active_style=self.active_style,
                        style_backends=dict(shadow='cpu',ink='pl'),shadow_frames=self.shadow_calls,
                        ink_frames=self.render_calls,warm_initialization_ms=self.warm_init_ms,
                        shadow_render_components_ms=list(self.shadow_timings))
    return CombinedResources
