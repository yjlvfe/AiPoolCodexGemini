#!/usr/bin/env python3
"""Prepare and verify a non-root AiPool deployment.

This script is intentionally bounded: without --apply it only reports what
would be copied. --apply copies only deployable source/configuration files and
creates a verification manifest; it never changes systemd units or deletes the
source.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DEST = Path(os.environ.get('AIPOOL_APP_ROOT', '/var/lib/aipool/app'))
RUNTIME_FILES = {
    Path('dashboard/auth.db'),
    Path('dashboard/auth.db-journal'),
    Path('dashboard/client-identities.json'),
}
PRIVATE_FILES = {
    Path('bridges/oauth-client.private.json'),
    Path('dashboard/oauth-client.private.json'),
}


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def files_to_copy() -> list[tuple[Path, Path]]:
    out = []
    for rel in ('bridges', 'cli', 'dashboard', 'scripts', 'version.json', 'config.env.example'):
        src = ROOT / rel
        if src.is_dir():
            for item in src.rglob('*'):
                relative = Path(rel) / item.relative_to(src)
                if (item.is_file() and not item.is_symlink()
                        and '__pycache__' not in item.parts
                        and relative not in RUNTIME_FILES
                        and relative not in PRIVATE_FILES
                        and not (relative.parts[:2] == ('dashboard', 'artifacts'))):
                    out.append((item, relative))
        elif src.is_file():
            out.append((src, Path(rel)))
    return out


def verify_manifest(manifest_path: Path) -> tuple[bool, list[str]]:
    manifest = json.loads(manifest_path.read_text())
    root = Path(manifest['destination']).expanduser().absolute()
    errors = []
    for item in manifest.get('files', []):
        try:
            relative = Path(item['path'])
            if relative.is_absolute() or '..' in relative.parts:
                raise ValueError('unsafe manifest path')
            raw_path = root / relative
            if raw_path.is_symlink():
                raise ValueError('symlink manifest path')
            path = raw_path.resolve()
            if root not in path.parents:
                raise ValueError('manifest path escapes destination')
        except (TypeError, ValueError):
            errors.append(f'unsafe:{item.get("path", "") if isinstance(item, dict) else item}')
            continue
        if path.is_symlink() or not path.is_file():
            errors.append(f'missing:{item["path"]}')
            continue
        if sha256(path) != item['sha256']:
            errors.append(f'hash:{item["path"]}')
    return not errors, errors


def _copy_one(source: Path, destination: Path, owner: str) -> None:
    if destination.is_symlink():
        raise ValueError(f'Unsafe migration destination: {destination}')
    ancestor = destination.parent
    while ancestor != ancestor.parent:
        if ancestor.is_symlink():
            raise ValueError(f'Unsafe migration destination ancestor: {ancestor}')
        ancestor = ancestor.parent
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f'.{destination.name}.', dir=destination.parent)
    os.close(fd)
    temporary_path = Path(temporary)
    try:
        shutil.copy2(source, temporary_path)
        mode = stat.S_IRUSR | stat.S_IWUSR
        if destination.name not in {'client-identities.json', 'oauth-client.private.json'}:
            mode |= stat.S_IRGRP
        if source.stat().st_mode & stat.S_IXUSR:
            mode |= stat.S_IXUSR | stat.S_IXGRP
        os.chmod(temporary_path, mode)
        os.replace(temporary_path, destination)
        if owner:
            shutil.chown(destination, user=owner)
    finally:
        temporary_path.unlink(missing_ok=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--dest', type=Path, default=DEFAULT_DEST)
    ap.add_argument('--apply', action='store_true')
    ap.add_argument('--verify', action='store_true')
    ap.add_argument('--owner', default='aipool')
    args = ap.parse_args()
    args.dest = args.dest.expanduser().absolute()
    manifest_path = args.dest / 'migration-manifest.json'
    if args.verify:
        if not manifest_path.is_file():
            print(f'missing manifest: {manifest_path}', file=sys.stderr)
            return 2
        ok, errors = verify_manifest(manifest_path)
        if ok:
            print(f'verified {manifest_path}')
            return 0
        for error in errors:
            print(error, file=sys.stderr)
        return 1
    if args.apply and os.geteuid() != 0:
        ap.error('--apply requires root to create the destination')
    pairs = files_to_copy()
    manifest = {'source': str(ROOT), 'destination': str(args.dest), 'files': []}
    for src, rel in pairs:
        dst = args.dest / rel
        manifest['files'].append({'path': str(rel), 'sha256': sha256(src), 'bytes': src.stat().st_size})
        if args.apply:
            _copy_one(src, dst, args.owner)
    if args.apply:
        manifest_path = args.dest / 'migration-manifest.json'
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix='.migration-manifest-', dir=manifest_path.parent)
        temporary_path = Path(temporary)
        try:
            with os.fdopen(fd, 'w') as handle:
                json.dump(manifest, handle, indent=2)
                handle.write('\n')
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temporary_path, 0o600)
            os.replace(temporary_path, manifest_path)
            if args.owner:
                shutil.chown(manifest_path, user=args.owner)
        finally:
            temporary_path.unlink(missing_ok=True)
        print(f'prepared {len(pairs)} files at {args.dest}; manifest={manifest_path}')
    else:
        print(f'dry-run: {len(pairs)} files would be copied to {args.dest}')
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
