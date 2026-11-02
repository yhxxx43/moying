"""Matched saved-mask benchmark, not a camera/live workflow."""
import hashlib
import time
import numpy as np
from pl_render.contract import validate_mask


def benchmark(renderer,reference,masks,set_timestamp,rounds=3,event=None):
    if type(rounds) is not int or rounds<3 or not masks:raise ValueError('At least three rounds and one mask required')
    for mask in masks:validate_mask(mask)
    if event is None:event=lambda **unused:None
    # Warm-up is explicit and reported, not mixed into measured samples.
    warmups=[]
    for i in range(2):
        mask=masks[i%len(masks)];before=hashlib.sha256(mask.tobytes()).hexdigest();set_timestamp(i*.2)
        event(state='WARMUP',index=i,total=2)
        start=time.perf_counter();cpu=reference(mask);cpu_ms=(time.perf_counter()-start)*1000
        start=time.perf_counter();pl=renderer(mask);pl_ms=(time.perf_counter()-start)*1000
        if (not isinstance(cpu,np.ndarray) or not isinstance(pl,np.ndarray) or cpu.dtype!=np.uint8 or pl.dtype!=np.uint8 or
            cpu.shape!=(480,640,3) or pl.shape!=(480,640,3)):raise ValueError('Invalid warm-up output')
        diff=int(np.count_nonzero(np.any(cpu!=pl,axis=2)))
        if diff or hashlib.sha256(mask.tobytes()).hexdigest()!=before:raise ValueError('First/warm-up frame differs; stop')
        warmups.append(dict(index=i,timestamp=i*.2,mask_sha256=before,different_pixels=diff,
                            cpu_ms=cpu_ms,pl_ms=pl_ms,output_sha256=hashlib.sha256(pl.tobytes()).hexdigest()))
    rows=[];differences=0
    for iteration in range(rounds):
        renderer.history.reset();reference.reset()
        for index,mask in enumerate(masks):
            timestamp=index*.2;set_timestamp(timestamp)
            before=hashlib.sha256(mask.tobytes()).hexdigest();outputs={};timings={}
            order=('cpu','pl') if (iteration+index)%2==0 else ('pl','cpu')
            for which in order:
                event(state='RUNNING',round=iteration,index=index,phase=which,total_measurements=len(masks)*rounds)
                start=time.perf_counter();outputs[which]=(reference if which=='cpu' else renderer)(mask)
                timings[which]=(time.perf_counter()-start)*1000.
            if hashlib.sha256(mask.tobytes()).hexdigest()!=before:raise ValueError('Input mask changed')
            if outputs['cpu'].shape!=(480,640,3) or outputs['pl'].shape!=(480,640,3):raise ValueError('Bad output shape')
            diff=int(np.count_nonzero(np.any(outputs['cpu']!=outputs['pl'],axis=2)));differences+=diff
            rows.append(dict(round=iteration,index=index,timestamp=timestamp,mask_sha256=before,
                             different_pixels=diff,cpu_ms=timings['cpu'],pl_ms=timings['pl'],
                             output_sha256=hashlib.sha256(outputs['pl'].tobytes()).hexdigest(),
                             pl_components_ms=dict(renderer.last_timings_ms)))
            if diff:raise ValueError('CPU/PL pixel difference; no retry or fallback')
    def stats(values):
        return dict(count=len(values),mean_ms=float(np.mean(values)),median_ms=float(np.median(values)),p95_ms=float(np.percentile(values,95)))
    cpu=stats([r['cpu_ms'] for r in rows]);pl=stats([r['pl_ms'] for r in rows])
    return dict(different_pixels=differences,rounds=rounds,sample_count=len(masks),measurement_count=len(rows),warmup_count=2,
                warmup_different_pixels=0,warmups=warmups,first_frame_pl_ms=warmups[0]['pl_ms'],
                same_input_timestamp=True,same_assets=True,cpu_oracle='approved-source',
                timing_boundary='history_pack_flush_pl_wait_invalidate_copy',timings=dict(cpu_render=cpu,pl_render=pl),
                performance_gate_passed=pl['median_ms']<cpu['median_ms'] and pl['p95_ms']<cpu['p95_ms'],
                measurements=rows,camera_started=False,hdmi_started=False,promotion_allowed=False)
