"""CPU-only device-independent test, also runnable on Python3.6/OpenCV3.2."""
import itertools
import platform
import cv2
import numpy as np
from pl_render.reference import classify,blend_u8
from pl_render.history import AlphaHistory


def run():
    different=0
    for alpha in range(52):
        values=np.arange(256,dtype=np.uint8).reshape(16,16)
        want=cv2.multiply(values,np.full_like(values,255-alpha),scale=1./255.,dtype=cv2.CV_8U)
        got=np.array([blend_u8(v,alpha) for v in range(256)],np.uint8).reshape(16,16)
        different+=int(np.count_nonzero(got!=want))
    for bits in itertools.product((0,255),repeat=4):
        for y,x in ((0,0),(0,318),(238,0),(238,318),(100,100)):
            m=np.zeros((240,320),np.uint8); m[y:y+2,x:x+2]=np.array(bits,np.uint8).reshape(2,2)
            support,inside=classify(m)
            nearest=cv2.resize(m,(640,480),interpolation=cv2.INTER_NEAREST)
            linear=cv2.resize(m,(640,480),interpolation=cv2.INTER_LINEAR)
            want=cv2.bitwise_and(cv2.inRange(linear,192,255),nearest)
            different+=int(np.count_nonzero(support!=nearest)+np.count_nonzero(inside!=want))
    history=AlphaHistory(); m=np.zeros((240,320),np.uint8); m[1,1]=255
    out=np.empty(m.shape,'<u2'); history.encode(m,0.,out); m.fill(0)
    history.encode(m,.1,out)
    if out[1,1]!=12032: raise AssertionError('alpha47 encoding failed')
    if different: raise AssertionError('CPU math difference: '+str(different))
    return dict(execution_success=True,renderer_only=True,hardware_started=False,
                different_pixels=different,python=platform.python_version(),
                numpy=np.__version__,opencv=cv2.__version__,
                notice='CPU math only; no board hardware qualification')
