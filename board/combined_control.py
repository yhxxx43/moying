"""Bounded one-pending-command mailbox; stdlib only, no device access."""
import json
from pathlib import Path
import uuid
from .resident_support import binding,publish_control
from .render_worker import write_json

ACTIONS=('shadow','ink','pause','resume')


def send(handle,action):
    if handle.config.get('mode')!='combined' or action not in ACTIONS:raise ValueError('Invalid combined action/session')
    state=handle.status()
    if (state.get('state') not in ('LIVE','PAUSED') or state.get('owner_running') is not True or
        (handle.result/'stop_requested').exists()):raise ValueError('Only the active LIVE/PAUSED owner accepts controls')
    value=dict(binding(handle.config),kind='combined_control',command_id=uuid.uuid4().hex,action=action)
    publish_control(handle.result/'control_pending.json',value)
    return value


class Controller:
    def __init__(self,config):
        self.config=config;self.result=Path(config['result']);self.style='shadow';self.paused=False
        self.command_id=None;self.command_count=0

    def snapshot(self):
        return dict(active_style=self.style,paused=self.paused,command_id=self.command_id,command_count=self.command_count)

    def poll(self,apply):
        path=self.result/'control_pending.json'
        if path.is_symlink():raise ValueError('Symlink control command')
        if not path.exists():return None
        value=json.loads(path.read_text(encoding='utf-8'))
        action=value.get('action');identifier=value.get('command_id')
        if (any(value.get(k)!=v for k,v in binding(self.config).items()) or
            value.get('kind')!='combined_control' or action not in ACTIONS or
            not isinstance(identifier,str) or len(identifier)!=32 or
            any(c not in '0123456789abcdef' for c in identifier)):
            raise ValueError('Foreign or invalid combined command')
        style=action if action in ('shadow','ink') else self.style
        paused=action=='pause'
        if action!='pause':apply(style,True)
        self.style=style;self.paused=paused;self.command_id=identifier;self.command_count+=1
        ack=dict(binding(self.config),kind='combined_ack',action=action,**self.snapshot())
        write_json(self.result/'control_ack.json',ack)
        # Only this owner consumes the immutable pending inode. A controller
        # cannot publish its next request until this one is acknowledged.
        if path.is_symlink() or json.loads(path.read_text(encoding='utf-8'))!=value:
            raise ValueError('Control changed while acknowledging')
        path.unlink()
        return ack
