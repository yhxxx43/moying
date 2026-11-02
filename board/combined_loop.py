"""One camera owner, frame-boundary style selection, healthy pause draining."""
from collections import deque
import time
import numpy as np
from .resident_support import StopRequested


def run_frames(stream,display,processor,render,event,check_controls,check_stop,
               clock=time.monotonic,stats=None,samples=None,controller=None,set_style=None):
    if controller is None or set_style is None:raise ValueError('Combined controller and style setter required')
    stats={} if stats is None else stats;samples=[] if samples is None else samples
    rows=deque(maxlen=120)
    stats.update(displayed_frames=0,paused_camera_frames=0,style_frames=dict(shadow=0,ink=0))
    start=clock();previous=0;next_check=start;next_event=start;next_sample=start
    def announce(now):
        event(state='PAUSED' if controller.paused else 'LIVE',frames=stats['displayed_frames'],
              elapsed_s=now-start,fps=stats['displayed_frames']/max(1e-6,now-start),
              prompt='paused_camera_still_running' if controller.paused else 'running',**controller.snapshot())
    try:
        while True:
            check_stop()
            ack=controller.poll(set_style)
            if ack is not None:announce(clock())
            begin=clock();seq,received,frame=stream.take(after=previous,timeout=2.)
            taken=clock();check_stop()
            if seq<=previous or not 0<=taken-received<=1:raise RuntimeError('Duplicate/stale/future camera frame')
            if frame.shape!=(240,320,3) or frame.dtype!=np.uint8:raise ValueError('Camera format changed')
            previous=seq
            if taken>=next_check:check_controls();next_check=taken+1
            if controller.paused:
                stats['paused_camera_frames']+=1
                if taken>=next_event:announce(taken);next_event=taken+1
                continue
            mask,timing=processor.segment(frame);segmented=clock();check_stop()
            image=render(mask);rendered=clock();check_stop()
            display.show(image);submitted=clock()
            row=dict(timing,index=stats['displayed_frames'],frame_seq=seq,active_style=controller.style,
                     elapsed_s=submitted-start,take_wait_ms=(taken-begin)*1000,
                     render_ms=(rendered-segmented)*1000,hdmi_ms=(submitted-rendered)*1000,
                     application_ms=(submitted-begin)*1000)
            rows.append(row);stats['displayed_frames']+=1;stats['style_frames'][controller.style]+=1
            if submitted>=next_sample and len(samples)<12:
                samples.append(dict(index=row['index'],frame=np.array(frame,copy=True),mask=np.array(mask,copy=True)))
                next_sample=submitted+3
            if submitted>=next_event:announce(submitted);next_event=submitted+1
    except StopRequested:stats['operator_stopped']=True
    finally:
        stats.update(controller.snapshot());stats['live_elapsed_s']=max(0.,clock()-start)
        stats['recent_measurements']=list(rows)
    return stats,samples
