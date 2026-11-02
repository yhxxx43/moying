"""File-only resident ZIP recovery. Never overwrite a prior installation."""
import hashlib
import json
import os
from pathlib import Path,PurePosixPath
import stat
import zipfile


def safe_name(name):
    path=PurePosixPath(name)
    if (not name or path.is_absolute() or str(path)!=name or '..' in path.parts or '\\' in name or ':' in name or
        any(part.endswith(('.', ' ')) for part in path.parts)):raise ValueError('Unsafe resident archive member')


def digest(raw):return hashlib.sha256(raw).hexdigest()


def file_precheck(root,expected_sha):
    root=Path(root)
    if root.is_symlink():raise ValueError('Symlink resident installation')
    root=root.resolve();path=root/'manifest.json'
    if path.is_symlink():raise ValueError('Symlink manifest')
    raw=path.read_bytes()
    if digest(raw)!=expected_sha:raise ValueError('Resident manifest SHA mismatch')
    manifest=json.loads(raw.decode('utf-8'))
    if manifest.get('format')!='pl-render-resident-addon-v1' or not manifest.get('files'):raise ValueError('Wrong resident format')
    for name,want in manifest['files'].items():
        safe_name(name);target=root/name
        if target.is_symlink() or root not in target.resolve().parents:raise ValueError('Unsafe installed member')
        if digest(target.read_bytes())!=want:raise ValueError('Changed resident member: '+name)
    return manifest


def recover(archive,destination,archive_sha,manifest_sha):
    destination=Path(destination);archive=Path(archive)
    if destination.exists() or destination.is_symlink():raise FileExistsError('Never overwrite resident installation')
    if digest(archive.read_bytes())!=archive_sha:raise ValueError('Resident ZIP SHA mismatch')
    with zipfile.ZipFile(str(archive)) as z:
        names=z.namelist()
        if len(names)!=len(set(names)) or len(names)>4096 or sum(i.file_size for i in z.infolist())>64*1024*1024:raise ValueError('Duplicate/oversized ZIP')
        for item in z.infolist():
            safe_name(item.filename)
            if item.is_dir() or stat.S_IFMT(item.external_attr>>16) not in (0,stat.S_IFREG):raise ValueError('Nonregular ZIP member')
        raw=z.read('manifest.json')
        if digest(raw)!=manifest_sha:raise ValueError('Resident manifest SHA mismatch')
        manifest=json.loads(raw.decode('utf-8'));files=manifest.get('files',{})
        if manifest.get('format')!='pl-render-resident-addon-v1' or set(names)!=set(files)|{'manifest.json'}:raise ValueError('Unmanifested resident member')
        for name,want in files.items():
            if digest(z.read(name))!=want:raise ValueError('Changed ZIP member: '+name)
        destination.mkdir(exist_ok=False)
        for name in names:
            target=destination/name;target.parent.mkdir(parents=True,exist_ok=True)
            with target.open('xb') as f:f.write(z.read(name));f.flush();os.fsync(f.fileno())
    return file_precheck(destination,manifest_sha)
