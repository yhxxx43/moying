"""Global cooperative Linux lease, acquired before any Overlay configuration."""
import os
import stat
from pathlib import Path


class OwnerLease:
    def __init__(self,path='/tmp/pynq_projection_dma_owner.lock'):
        self.path=Path(path); self.fd=None

    def acquire(self):
        if self.fd is not None: raise RuntimeError('Lease already acquired')
        import fcntl
        flags=os.O_RDWR|os.O_CREAT|getattr(os,'O_NOFOLLOW',0)|getattr(os,'O_CLOEXEC',0)
        fd=os.open(str(self.path),flags,0o600)
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode): raise ValueError('Owner lease is not a regular file')
            try: fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError: raise RuntimeError('Another projection DMA owner is running; do not load an overlay')
        except BaseException:
            os.close(fd); raise
        self.fd=fd

    def release(self):
        # Caller may release only after confirmed safe stop (or before allocation).
        if self.fd is not None:
            import fcntl
            fcntl.flock(self.fd,fcntl.LOCK_UN); os.close(self.fd); self.fd=None
