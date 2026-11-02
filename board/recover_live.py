"""Standalone authenticated file-only recovery for the separate live add-on."""
import hashlib
import json
import os
from pathlib import Path,PurePosixPath
import stat
import zipfile


def digest(raw):return hashlib.sha256(raw).hexdigest()


def safe_name(name):
    p=PurePosixPath(name)
    if (not name or p.is_absolute() or str(p)!=name or '..' in p.parts or '\\' in name or ':' in name or
        any(part.endswith(('.', ' ')) for part in p.parts)):raise ValueError('Unsafe live archive member')


def file_precheck(root,expected_sha):
    root=Path(root).resolve();path=root/'manifest.json'
    if path.is_symlink():raise ValueError('Symlink manifest')
    raw=path.read_bytes()
    if digest(raw)!=expected_sha:raise ValueError('Live manifest SHA mismatch')
    manifest=json.loads(raw.decode('utf-8'))
    if manifest.get('format')!='pl-render-live-addon-v1' or not manifest.get('files'):raise ValueError('Wrong live add-on format')
    for name,want in manifest['files'].items():
        safe_name(name);target=root/name
        if target.is_symlink() or root not in target.resolve().parents:raise ValueError('Unsafe installed member')
        if digest(target.read_bytes())!=want:raise ValueError('Changed live installed member: '+name)
    return dict(files_verified=True,hardware_started=False,live_bundle_sha256=expected_sha)


def recover(archive,destination,archive_sha,manifest_sha):
    destination=Path(destination);archive=Path(archive)
    if destination.exists() or destination.is_symlink():raise FileExistsError('Refusing to overwrite live directory')
    if digest(archive.read_bytes())!=archive_sha:raise ValueError('Live archive SHA mismatch')
    with zipfile.ZipFile(str(archive)) as z:
        names=z.namelist()
        if len(names)!=len(set(names)) or len(names)>4096 or sum(i.file_size for i in z.infolist())>64*1024*1024:raise ValueError('Duplicate/oversized live ZIP')
        for item in z.infolist():
            safe_name(item.filename)
            if item.is_dir() or stat.S_IFMT(item.external_attr>>16) not in (0,stat.S_IFREG):raise ValueError('Nonregular live member')
        raw=z.read('manifest.json')
        if digest(raw)!=manifest_sha:raise ValueError('Live manifest SHA mismatch')
        manifest=json.loads(raw.decode('utf-8'));files=manifest.get('files',{})
        if manifest.get('format')!='pl-render-live-addon-v1' or set(names)!=set(files)|{'manifest.json'}:raise ValueError('Unmanifested live file')
        for name,want in files.items():
            if digest(z.read(name))!=want:raise ValueError('Changed live archive member: '+name)
        destination.mkdir(exist_ok=False)
        for name in names:
            target=destination/name;target.parent.mkdir(parents=True,exist_ok=True)
            with target.open('xb') as f:f.write(z.read(name));f.flush();os.fsync(f.fileno())
    if hasattr(os,'sync'):os.sync()
    return file_precheck(destination,manifest_sha)
