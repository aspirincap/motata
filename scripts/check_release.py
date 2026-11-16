#!/usr/bin/env python3
"""Offline artifact gate. Never runs npm install/postinstall or reads local credentials."""
from __future__ import annotations

import argparse
import ast
from email.parser import BytesParser
import json
import hashlib
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import tomllib
import venv
import zipfile

ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN = {'.npmrc', 'token.txt', '.runtime', 'outputs', '.env', '.git', '.codex_tmp', '.firecrawl', '__pycache__', 'node_modules'}
SECRET = re.compile(rb'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----|(?:ghp_|github_pat_)[A-Za-z0-9_]{30,}|AKIA[0-9A-Z]{16}')


def audit(entries: dict[str, bytes]) -> None:
    for name, content in entries.items():
        if 'tiktok_business_api_sdk/' in name or 'business_api_client/' in name or name.endswith('/transport/tiktok_sdk.py'):
            raise ValueError(f'Unexpected TikTok SDK in artifact: {name}')
        parts = PurePosixPath(name).parts
        if name.startswith('/') or '..' in parts or any(p in FORBIDDEN or p.startswith('.env.') or p.endswith(('.pyc', '.pem', '.key', '.p12', '.pfx')) for p in parts):
            raise ValueError(f'Forbidden artifact path: {name}')
        if SECRET.search(content):
            raise ValueError(f'Potential credential in artifact: {name}')


def run(args: list[str], cwd: Path, env: dict[str, str]) -> str:
    result = subprocess.run(args, cwd=cwd, env=env, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=180)
    if result.returncode:
        raise RuntimeError(f'{args[0]} failed:\n{result.stdout}')
    return result.stdout


def archive_entries(file: Path) -> dict[str, bytes]:
    if file.suffix == '.whl':
        with zipfile.ZipFile(file) as archive:
            return {n: archive.read(n) for n in archive.namelist() if not n.endswith('/')}
    with tarfile.open(file) as archive:
        if any(m.issym() or m.islnk() for m in archive.getmembers()):
            raise ValueError('Unexpected archive link')
        return {m.name: archive.extractfile(m).read() for m in archive.getmembers() if m.isfile()}


def stage_source(target: Path) -> None:
    # Explicit input list means local credentials and generated artifacts are never opened.
    for name in ('pyproject.toml', 'setup.cfg', 'README.md', 'package.json', '.npmignore', 'scripts/npm-postinstall.js'):
        src = ROOT / name
        if src.is_file():
            dst = target / name
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, dst)
    for directory, suffixes in (('motata_cli', {'.py', '.json'}), ('motata_gateway', {'.py', '.json'}), ('bin', {'.js'}), ('lib', {'.js'})):
        for src in (ROOT / directory).rglob('*'):
            if src.is_symlink() or any(p in FORBIDDEN for p in src.relative_to(ROOT).parts):
                continue
            if src.is_file() and (src.suffix in suffixes or src.name in {'LICENSE', 'NOTICE.md'}):
                dst = target / src.relative_to(ROOT)
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(src, dst)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--wheelhouse', type=Path, help='Offline dependency wheels; enables fully isolated help smoke')
    parser.add_argument('--pack-only', action='store_true', help='Audit and rebuild artifacts without installing anything')
    parser.add_argument('--artifact-dir', type=Path, help='Export audited artifacts and SHA256SUMS after the gate passes')
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='motata-release-') as tmp:
        work = Path(tmp)
        source = work / 'source'
        source.mkdir()
        stage_source(source)
        env = {**os.environ, 'PIP_NO_INDEX': '1', 'PIP_DISABLE_PIP_VERSION_CHECK': '1', 'npm_config_offline': 'true', 'npm_config_ignore_scripts': 'true', 'NPM_CONFIG_USERCONFIG': str(work / 'empty-npm-config'), 'NPM_CONFIG_GLOBALCONFIG': str(work / 'empty-global-config'), 'MOTATA_SUPPRESS_SKILLS_NOTICE': '1'}
        env['npm_config_cache'] = str(work / 'npm-cache')
        env['PIP_CACHE_DIR'] = str(work / 'pip-cache')
        env.pop('PYTHONPATH', None)
        env.pop('MOTATA_INSTALL_METHOD', None)
        version = tomllib.loads((source / 'pyproject.toml').read_text())['project']['version']
        package = json.loads((source / 'package.json').read_text())
        assert package['version'] == version
        assert package['repository'] == {'type': 'git', 'url': 'git+https://github.com/aspirincap/motata.git'}
        assert tomllib.loads((source / 'pyproject.toml').read_text())['project']['requires-python'] == '>=3.11'
        tree = ast.parse((source / 'motata_cli/__init__.py').read_text())
        py_version = next(ast.literal_eval(n.value) for n in tree.body if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == '__version__' for t in n.targets))
        assert py_version == version
        manifest = json.loads((source / 'motata_cli/skills_compatibility.json').read_text())
        assert any(r['cli_version'] == version for r in manifest['releases'])
        run([sys.executable, '-c', "import setuptools.build_meta as b; b.build_sdist('dist'); b.build_wheel('dist')"], source, env)
        run(['npm', 'pack', '--offline', '--ignore-scripts', '--json'], source, env)
        artifacts = sorted((source / 'dist').iterdir()) + list(source.glob('*.tgz'))
        for artifact in artifacts:
            entries = archive_entries(artifact)
            audit(entries)
            prefix = '' if artifact.suffix == '.whl' else ('package/' if artifact.suffix == '.tgz' else f'motata_cli-{version}/')
            for src in (p for name in ('motata_cli', 'motata_gateway') for p in (source / name).rglob('*')):
                if src.is_file() and (src.suffix in {'.py', '.json'} or src.name in {'LICENSE', 'NOTICE.md'}):
                    name = prefix + src.relative_to(source).as_posix()
                    assert entries.get(name) == src.read_bytes(), (artifact, name)
            if artifact.suffix == '.whl' or artifact.name.endswith('.tar.gz'):
                metadata_name = next(n for n in entries if n.endswith('.dist-info/METADATA')) if artifact.suffix == '.whl' else prefix + 'PKG-INFO'
                metadata = BytesParser().parsebytes(entries[metadata_name])
                assert metadata['Version'] == version
                assert metadata['Requires-Python'] == '>=3.11'
            print(f'[{artifact.name}]')
            print(f'  {len(entries)} files audited; source/resource bytes and metadata verified')
            if artifact.suffix == '.tgz':
                assert 'package/lib/runtime.js' in entries
                assert 'package/scripts/npm-postinstall.js' in entries
                assert json.loads(entries['package/package.json'])['version'] == version
        # Rebuild wheel from sdist rather than trusting the working tree build.
        sdist = next(p for p in artifacts if p.name.endswith('.tar.gz'))
        unpacked = work / 'sdist'
        unpacked.mkdir()
        with tarfile.open(sdist) as archive:
            archive.extractall(unpacked, filter='data')
        rebuilt = work / 'rebuilt'
        rebuilt.mkdir()
        run([sys.executable, '-c', f'import setuptools.build_meta as b; b.build_wheel({str(rebuilt)!r})'], next(unpacked.iterdir()), env)
        wheel = next(rebuilt.glob('*.whl'))
        rebuilt_entries = archive_entries(wheel)
        audit(rebuilt_entries)
        assert rebuilt_entries['motata_cli/skills_compatibility.json'] == (source / 'motata_cli/skills_compatibility.json').read_bytes()
        if args.pack_only:
            export_artifacts(artifacts, args.artifact_dir)
            print('Offline artifact gate passed; installation smoke explicitly skipped (--pack-only).')
            return
        runtime = work / 'venv'
        venv.EnvBuilder(with_pip=True).create(runtime)
        python = runtime / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
        command = [str(python), '-m', 'pip', 'install', '--no-index']
        if args.wheelhouse:
            command += ['--find-links', str(args.wheelhouse.resolve())]
        else:
            command += ['--no-deps']
        run(command + [str(wheel)], work, env)
        smoke = f"from importlib.resources import files; import json, motata_cli; assert motata_cli.__version__ == {version!r}; assert json.loads(files('motata_cli').joinpath('skills_compatibility.json').read_text())['releases']"
        run([str(python), '-I', '-c', smoke], work, env)
        if args.wheelhouse:
            run([str(python), '-I', '-m', 'motata_cli', '--help'], work, env)
            http_smoke = "import sys; from unittest.mock import Mock; from motata_cli.tiktok.client import TikTokClient; t=Mock(); t.request.return_value={'code':0,'data':{}}; c=TikTokClient('offline-fixture',transport=t); c.list_campaigns('1',smart_plus=True); c.create_adgroup({'advertiser_id':'1','native_series_id':'2'},smart_plus=True); assert t.request.call_args.args[0].path=='smart_plus/adgroup/create/'; assert not any(n.startswith(('business_api_client','jwt')) for n in sys.modules)"
            run([str(python), '-I', '-c', http_smoke], work, env)
            npm_dir = work / 'npm'
            npm_dir.mkdir()
            with tarfile.open(next(p for p in artifacts if p.suffix == '.tgz')) as archive:
                archive.extractall(npm_dir, filter='data')
            npm_env = {**env, 'PIP_FIND_LINKS': str(args.wheelhouse.resolve())}
            run(['node', str(npm_dir / 'package/bin/motata.js'), '--help'], work, npm_env)
            npm_python = npm_dir / 'package/.runtime/venv' / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
            run([str(npm_python), '-I', '-c', "from motata_cli.update import detect_install_method; assert detect_install_method() == 'npm'"], work, {**npm_env, 'MOTATA_INSTALL_METHOD': 'npm'})
        else:
            print('NOTE: dependency-free isolated smoke only; supply --wheelhouse for CLI help smoke.')
        export_artifacts(artifacts, args.artifact_dir)
        print('Offline release gate passed (temporary isolated installs only; no publish executed).')


def export_artifacts(artifacts: list[Path], destination: Path | None) -> None:
    if destination is None:
        return
    destination.mkdir(parents=True, exist_ok=True)
    if any(destination.iterdir()):
        raise ValueError('Artifact directory must be empty; refusing to mix release builds')
    checksums = []
    for artifact in artifacts:
        shutil.copyfile(artifact, destination / artifact.name)
        checksums.append(f'{hashlib.sha256(artifact.read_bytes()).hexdigest()}  {artifact.name}\n')
    (destination / 'SHA256SUMS').write_text(''.join(checksums))
    print(f'Audited artifacts exported to {destination}')


if __name__ == '__main__':
    main()
