"""Resident bindings and bounded logs; import is stdlib-only."""
import json
import os
from pathlib import Path
import time
import uuid
from .render_worker import write_json


def binding(config):
    return dict(stage='resident_combined' if config.get('mode')=='combined' else 'resident_ink',boot_id=config['boot'],run_token=config['token'],
                package_sha256=config['expected_sha'],bundle_sha256=config['bundle_sha'])


def publish_control(path,value):
    """Publish one complete JSON inode without ever replacing a prior command.

    A same-directory hard link is atomic and fails if destination already exists;
    unlike exclusive open, it never exposes an empty/partially written record.
    """
    path=Path(path);temporary=path.with_name('.resident_control_'+uuid.uuid4().hex+'.tmp');created=False
    try:
        with temporary.open('x',encoding='utf-8') as stream:
            created=True;json.dump(value,stream,sort_keys=True);stream.flush();os.fsync(stream.fileno())
        os.link(str(temporary),str(path))
    finally:
        if created:temporary.unlink()
        if hasattr(os,'O_DIRECTORY'):
            fd=os.open(str(path.parent),os.O_RDONLY|os.O_DIRECTORY)
            try:os.fsync(fd)
            finally:os.close(fd)


class RotatingLog:
    def __init__(self,path,limit=262144):self.path=Path(path);self.limit=limit
    def write(self,data):
        if isinstance(data,str):data=data.encode('utf-8','replace')
        while data:
            size=self.path.stat().st_size if self.path.exists() else 0
            if size>=self.limit:
                for index in (3,2,1):
                    src=self.path if index==1 else self.path.with_name(self.path.name+'.'+str(index-1))
                    dest=self.path.with_name(self.path.name+'.'+str(index))
                    if src.exists():os.replace(str(src),str(dest))
                size=0
            part=data[:self.limit-size];data=data[len(part):]
            with self.path.open('ab') as f:f.write(part);f.flush();os.fsync(f.fileno())


class Journal:
    def __init__(self,config):
        self.config=config;self.result=Path(config['result']);self.hardware_started=False
        self.log=RotatingLog(self.result/'events.jsonl')
    def emit(self,**values):
        self.hardware_started=self.hardware_started or bool(values.get('hardware_started'))
        row=dict(values,**binding(self.config));row.update(pid=os.getpid(),hardware_started=self.hardware_started,timestamp=time.time())
        self.log.write((json.dumps(row,allow_nan=False)+'\n').encode())
        write_json(self.result/'status.json',row)


class StopRequested(Exception):pass
