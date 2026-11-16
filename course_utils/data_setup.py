"""Versioned teaching-data installer for BIOL-510/610. Python >=3.9; stdlib only."""
from __future__ import annotations
from pathlib import Path, PurePosixPath
import hashlib, json, os, shutil, tempfile, urllib.request, zipfile


def sha256(path: str | Path) -> str:
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def load_registry(path: str | Path) -> dict:
    with open(path, encoding='utf-8') as f:
        result = json.load(f)
    if result.get('schema_version') != 1:
        raise ValueError('Unsupported registry version')
    return result


def _safe_member(name: str) -> Path:
    posix = PurePosixPath(name)
    if (not name or name.startswith('/') or '\\' in name or
            any(part in ('', '.', '..') for part in posix.parts) or ':' in name):
        raise ValueError(f'Unsafe archive member: {name!r}')
    return Path(*posix.parts)


def install_dataset(name: str, project_dir: str | Path, *,
                    registry_path: str | Path | None = None,
                    archive_path: str | Path | None = None,
                    url: str | None = None, overwrite: bool = False) -> dict:
    """Install one archive, verifying its SHA-256 and every expected file.

    No archive is fetched unless a URL has been configured or passed explicitly.
    Existing files are not overwritten unless overwrite=True. Source archives
    must be trusted course releases. Uses staging to avoid partially written files.
    """
    root = Path(project_dir).expanduser().resolve()
    reg_path = Path(registry_path) if registry_path else root/'data/dataset_registry.json'
    spec = load_registry(reg_path)['datasets'][name]
    root.mkdir(parents=True, exist_ok=True)
    expected = spec['files']
    if all((root/rel).is_file() and sha256(root/rel)==digest for rel,digest in expected.items()):
        return {'dataset': name, 'status':'already_verified','files':len(expected)}
    with tempfile.TemporaryDirectory(prefix='biol_course_') as tmp:
        tmp = Path(tmp)
        if archive_path is not None:
            archive = Path(archive_path).expanduser().resolve()
            if not archive.is_file():raise FileNotFoundError(archive)
        else:
            source = url or spec.get('url')
            if not source:
                raise ValueError('No published URL yet. Supply archive_path=... or configure registry URL.')
            if not source.startswith('https://'):
                raise ValueError('Download URL must use HTTPS')
            archive = tmp/'download.zip'
            with urllib.request.urlopen(source, timeout=120) as response, open(archive,'wb') as f:
                shutil.copyfileobj(response,f, length=1024*1024)
        if sha256(archive) != spec['archive_sha256']:
            raise ValueError(f'Archive checksum mismatch for {name}')
        staging = tmp/'staging';staging.mkdir()
        with zipfile.ZipFile(archive) as z:
            members = [m for m in z.infolist() if not m.is_dir()]
            names = [m.filename for m in members]
            if len(names) != len(set(names)) or set(names) != set(expected):
                raise ValueError('Archive contents do not match the registry')
            for m in members:
                relative = _safe_member(m.filename)
                # Reject symlinks and special Unix file types.
                unix_type = (m.external_attr >> 16) & 0o170000
                if unix_type not in (0, 0o100000):
                    raise ValueError(f'Archive contains a nonregular file: {m.filename}')
                dest = staging/relative;dest.parent.mkdir(parents=True,exist_ok=True)
                with z.open(m) as source, open(dest,'wb') as target:
                    shutil.copyfileobj(source,target)
                if sha256(dest) != expected[m.filename]:
                    raise ValueError(f'File checksum mismatch: {m.filename}')
        conflicts = [rel for rel in expected if (root/rel).exists() and sha256(root/rel)!=expected[rel]]
        if conflicts and not overwrite:
            raise FileExistsError('Different files already exist (use overwrite=True only after backup): '+', '.join(conflicts))
        for rel in expected:
            dest=root/rel;dest.parent.mkdir(parents=True,exist_ok=True)
            if dest.is_file() and sha256(dest)==expected[rel]:continue
            os.replace(staging/rel,dest)
    return {'dataset':name,'status':'installed','files':len(expected)}


def verify_dataset(name: str, project_dir: str | Path, *, registry_path=None) -> dict:
    root=Path(project_dir)
    reg=load_registry(registry_path or root/'data/dataset_registry.json')
    expected=reg['datasets'][name]['files']
    missing=[rel for rel in expected if not (root/rel).is_file()]
    changed=[rel for rel,digest in expected.items() if (root/rel).is_file() and sha256(root/rel)!=digest]
    return {'dataset':name,'ok':not(missing or changed),'missing':missing,'checksum_mismatch':changed}
