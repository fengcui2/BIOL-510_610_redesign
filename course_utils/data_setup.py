"""Versioned teaching-data installer for BIOL-510/610. Python >=3.10; stdlib only.

Includes v1.2 full-cohort convenience functions; existing demo APIs unchanged.
"""
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


# Full-cohort TCGA-BLCA v1.2 convenience API.
COURSE_MATRICES_DATASET = 'course_matrices_v1_2'
COURSE_MATRICES_RELATIVE_DIR = Path('data/processed/course_matrices')
COURSE_MATRICES_FILES = {
    'clinical': 'clinical_course.csv',
    'counts': 'RNA_counts_course.csv',
    'tpm': 'RNA_TPM_course.csv',
    'gene_annotation': 'gene_annotation_course.csv',
    'sample_mapping': 'rna_sample_mapping_course.csv',
    'qc': 'reconstruction_qc.json',
}


def install_course_matrices(project_dir: str | Path, *, registry_path=None,
                            archive_path=None, overwrite=False) -> dict:
    """Install full-cohort v1.2 from the registry's verified GitHub Release.

    The registry must already be at data/dataset_registry.json (or supplied
    via registry_path). No Google Drive access is required.
    """
    return install_dataset(COURSE_MATRICES_DATASET, project_dir,
                           registry_path=registry_path,
                           archive_path=archive_path, overwrite=overwrite)


def course_matrix_paths(project_dir: str | Path, *, verify=True,
                        registry_path=None) -> dict[str, Path]:
    """Return canonical paths after optional checksum verification.

    Does not load the 60,660-column matrices into memory. Includes the
    frozen manifest; legacy reference matrices are never used.
    """
    root = Path(project_dir).expanduser().resolve()
    if verify:
        result = verify_dataset(COURSE_MATRICES_DATASET, root,
                                registry_path=registry_path)
        if not result['ok']:
            raise RuntimeError('Course matrices missing or modified: '
                               + str(result))
    base = root / COURSE_MATRICES_RELATIVE_DIR
    paths = {key: base / filename for key, filename in COURSE_MATRICES_FILES.items()}
    paths['manifest'] = root / 'data/course_patient_manifest.csv'
    return paths


def fetch_course_registry(project_dir: str | Path, *,
                          repo='fengcui2/BIOL-510_610_redesign',
                          overwrite=False) -> Path:
    """Download the official registry, without requiring Google Drive.

    Will not replace an existing registry unless overwrite=True. The
    downloaded registry is parsed and checked for the v1.2 dataset entry.
    Individual archives and files are verified during install_dataset().
    """
    root = Path(project_dir).expanduser().resolve()
    destination = root / 'data/dataset_registry.json'
    if destination.exists() and not overwrite:
        registry = load_registry(destination)
        if COURSE_MATRICES_DATASET not in registry.get('datasets', {}):
            raise ValueError('Existing registry has no course_matrices_v1_2; '
                             'set overwrite=True to refresh it')
        return destination
    url = ('https://raw.githubusercontent.com/' + repo
           + '/main/data/dataset_registry.json')
    with urllib.request.urlopen(url, timeout=60) as response:
        payload = response.read()
    registry = json.loads(payload.decode('utf-8'))
    if registry.get('schema_version') != 1 or COURSE_MATRICES_DATASET not in registry.get('datasets', {}):
        raise ValueError('Downloaded registry is invalid or lacks v1.2')
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode='wb', dir=destination.parent,
                                     prefix='.registry_', delete=False) as tmp:
        tmp.write(payload)
        temporary = Path(tmp.name)
    try:
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)
    return destination
