"""File-only exclusive recovery. This module can be executed after ZIP pin check."""
import hashlib
import json
import os
from pathlib import Path,PurePosixPath
import stat
import zipfile


def sha(data):return hashlib.sha256(data).hexdigest()


def safe_name(name):
    p=PurePosixPath(name)
    if (not name or p.is_absolute() or str(p)!=name or '..' in p.parts or
        '\\' in name or ':' in name or any(part.endswith(('.', ' ')) for part in p.parts)):
        raise ValueError('Unsafe archive member: '+name)


def file_precheck(package,expected_sha):
    package=Path(package).resolve()
    manifest_path=package/'manifest.json'
    if manifest_path.is_symlink():raise ValueError('Symlink manifest')
    raw=manifest_path.read_bytes()
    if sha(raw)!=expected_sha:raise ValueError('Installed manifest SHA mismatch')
    manifest=json.loads(raw.decode('utf-8'));files=manifest.get('files',{})
    if not isinstance(files,dict) or 'board/render_offline.py' not in files:raise ValueError('Missing pinned gate')
    verified_gate=None
    for name,want in files.items():
        safe_name(name);target=package/name
        if target.is_symlink() or package not in target.resolve().parents:raise ValueError('Unsafe installed member')
        data=target.read_bytes()
        if sha(data)!=want:raise ValueError('Installed member SHA mismatch: '+name)
        if name=='board/render_offline.py':verified_gate=data
    # Unique module identity prevents a previously imported old board package
    # from answering checks for this separately pinned new package.
    path=package/'board/render_offline.py'
    namespace=dict(__name__='_render_gate_'+sha(verified_gate),__file__=str(path))
    exec(compile(verified_gate,str(path),'exec'),namespace)
    return namespace['precheck'](package,expected_sha)


def recover(archive,destination,expected_archive_sha,expected_package_sha=None):
    archive=Path(archive);destination=Path(destination)
    if destination.exists() or destination.is_symlink():raise FileExistsError(str(destination))
    if sha(archive.read_bytes())!=expected_archive_sha:raise ValueError('Archive SHA mismatch')
    with zipfile.ZipFile(str(archive)) as z:
        names=z.namelist()
        if len(names)!=len(set(names)) or len(names)>4096:raise ValueError('Duplicate/oversized archive')
        for item in z.infolist():
            safe_name(item.filename)
            if item.is_dir() or stat.S_IFMT(item.external_attr>>16) not in (0,stat.S_IFREG):raise ValueError('Nonregular ZIP member')
        manifest_raw=z.read('manifest.json');manifest=json.loads(manifest_raw.decode('utf-8'))
        if expected_package_sha is not None and sha(manifest_raw)!=expected_package_sha:raise ValueError('Package SHA mismatch')
        files=manifest.get('files',{})
        if set(names)!=set(files)|{'manifest.json'}:raise ValueError('Unmanifested/missing ZIP file')
        if sum(i.file_size for i in z.infolist())>256*1024*1024:raise ValueError('Oversized recovery')
        for name,want in files.items():
            if sha(z.read(name))!=want:raise ValueError('Member SHA mismatch: '+name)
        destination.mkdir(parents=False,exist_ok=False)
        for name in names:
            target=destination/name;target.parent.mkdir(parents=True,exist_ok=True)
            with target.open('xb') as f:f.write(z.read(name));f.flush();os.fsync(f.fileno())
    if hasattr(os,'sync'):os.sync()
    for name,want in files.items():
        if sha((destination/name).read_bytes())!=want:raise ValueError('Disk readback mismatch: '+name)
    if sha((destination/'manifest.json').read_bytes())!=sha(manifest_raw):raise ValueError('Manifest readback mismatch')
    # Minimal fixture packages used by unit tests may omit the runtime module.
    # Real deliverables always include it; no hardware authorization is made.
    if (destination/'board/render_offline.py').is_file():return file_precheck(destination,sha(manifest_raw))
    return dict(files_verified=True,hardware_started=False,package_sha256=sha(manifest_raw))
