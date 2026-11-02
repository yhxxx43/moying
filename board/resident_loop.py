"""Continuous mask-only loop; bounded telemetry, no 30-second prompt/exit."""
from collections import deque
import time
import numpy as np
from .resident_support import StopRequested


def run_frames(stream,display,processor,render,event,check_controls,check_stop,
               clock=time.monotonic,stats=None,samples=None):
    stats={} if stats is None else stats;samples=[] if samples is None else samples
    rows=deque(maxlen=120);stats.update(displayed_frames=0,recent_measurements=rows)
    start=clock();previous=0;next_check=start;next_event=start;next_sample=start
    try:
        while True:
            check_stop();begin=clock()
            seq,received,frame=stream.take(after=previous,timeout=2.)
            taken=clock();check_stop()
            if seq<=previous or not 0<=taken-received<=1:raise RuntimeError('Duplicate/stale/future camera frame')
            if frame.shape!=(240,320,3) or frame.dtype!=np.uint8:raise ValueError('Camera format changed')
            if taken>=next_check:check_controls();next_check=taken+1
            mask,timing=processor.segment(frame);segmented=clock();check_stop()
            shown=render(mask);rendered=clock();check_stop()
            display.show(shown);submitted=clock()
            row=dict(timing,index=stats['displayed_frames'],frame_seq=seq,elapsed_s=submitted-start,
                     take_wait_ms=(taken-begin)*1000,render_ms=(rendered-segmented)*1000,
                     hdmi_ms=(submitted-rendered)*1000,application_ms=(submitted-begin)*1000)
            rows.append(row);stats['displayed_frames']+=1;previous=seq
            if submitted>=next_sample and len(samples)<12:
                samples.append(dict(index=row['index'],frame=np.array(frame,copy=True),mask=np.array(mask,copy=True)))
                next_sample=submitted+3
            if submitted>=next_event:
                event(state='LIVE',frames=stats['displayed_frames'],elapsed_s=submitted-start,
                      fps=stats['displayed_frames']/max(1e-6,submitted-start),prompt='running')
                next_event=submitted+1
    except StopRequested:
        stats['operator_stopped']=True
    finally:
        stats['live_elapsed_s']=max(0.,clock()-start)
        stats['recent_measurements']=list(rows)
    return stats,samples
