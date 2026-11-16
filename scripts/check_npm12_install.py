#!/usr/bin/env python3
"""Accept a tarball or registry release using npm 12's default script policy.

Creates an isolated global install. Never reads npm credentials or invokes an
advertising API. A wheelhouse makes Python bootstrap fully offline.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time


def run(args: list[str], cwd: Path, env: dict[str, str]) -> str:
    result = subprocess.run(args, cwd=cwd, env=env, text=True,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            timeout=240)
    if result.returncode:
        raise RuntimeError(f'{args[:3]} failed:\n{result.stdout}')
    return result.stdout.strip()


def install_package(command: list[str], root: Path, env: dict[str, str], *, registry: bool) -> None:
    # A successful publish can precede propagation to npm's install metadata.
    # Retry only a missing registry version, never bootstrap or other failures.
    attempts = 16 if registry else 1
    for attempt in range(attempts):
        try:
            run(command, root, env)
            return
        except RuntimeError as error:
            unavailable = any(code in str(error) for code in ('npm error code ETARGET', 'npm error code E404'))
            if not unavailable or attempt + 1 == attempts:
                raise
            print(f'Registry version is still propagating; retry {attempt + 1}/{attempts - 1} in 20s', flush=True)
            time.sleep(20)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument('--tarball', type=Path)
    source.add_argument('--package', help='Exact registry spec, e.g. motata@0.3.0')
    parser.add_argument('--version', required=True)
    parser.add_argument('--wheelhouse', required=True, type=Path)
    args = parser.parse_args()
    if args.package and args.package != f'motata@{args.version}':
        parser.error('--package must be the exact motata version under test')
    spec = str(args.tarball.resolve()) if args.tarball else args.package
    with tempfile.TemporaryDirectory(prefix='motata-npm12-') as directory:
        root = Path(directory)
        # Explicit environment: no inherited npm settings, credentials or Motata profile.
        env = {key: value for key, value in os.environ.items()
               if key in {'PATH', 'SystemRoot', 'SYSTEMROOT', 'COMSPEC', 'PATHEXT',
                          'TMPDIR', 'TEMP', 'TMP', 'SSL_CERT_FILE', 'SSL_CERT_DIR',
                          'NODE_EXTRA_CA_CERTS'}}
        for name in ('npmrc', 'global-npmrc'):
            (root / name).touch()
        env.update(HOME=directory, USERPROFILE=directory,
                   NPM_CONFIG_USERCONFIG=str(root / 'npmrc'),
                   NPM_CONFIG_GLOBALCONFIG=str(root / 'global-npmrc'),
                   NPM_CONFIG_CACHE=str(root / 'cache'),
                   NPM_CONFIG_REGISTRY='https://registry.npmjs.org/',
                   PIP_NO_INDEX='1', PIP_FIND_LINKS=str(args.wheelhouse.resolve()),
                   PIP_CONFIG_FILE=os.devnull, PIP_DISABLE_PIP_VERSION_CHECK='1',
                   MOTATA_HOME=str(root / 'motata-home'),
                   MOTATA_SUPPRESS_SKILLS_NOTICE='1', PYTHONUTF8='1')
        npm_version = run(['npm', '--version'], root, env)
        assert npm_version.split('.')[0] == '12', npm_version
        assert run(['npm', 'config', 'get', 'ignore-scripts'], root, env) == 'false'
        # Do not pass --ignore-scripts or --allow-scripts: exercise npm 12 defaults.
        prefix = root / 'prefix'
        command = ['npm', 'install', '--global', '--prefix', str(prefix),
                   '--no-audit', '--no-fund']
        if args.tarball:
            command.append('--offline')
        else:
            command.append('--prefer-online')
        install_package(command + [spec], root, env, registry=not args.tarball)
        installed = prefix / ('node_modules/motata' if os.name == 'nt' else 'lib/node_modules/motata')
        assert json.loads((installed / 'package.json').read_text())['version'] == args.version
        assert not (installed / '.runtime').exists(), 'postinstall unexpectedly ran'
        print(f'npm {npm_version}: clean global install passed; postinstall was skipped by default')
        executable = installed / 'bin/motata.js'
        launcher = ['node', str(executable)] if os.name == 'nt' else [str(prefix / 'bin/motata')]
        output = run(launcher + ['--version'], root, env)
        assert args.version in output, output
        marker = installed / '.runtime/venv/.motata-ready'
        assert marker.read_text() == args.version
        timestamp = marker.stat().st_mtime_ns
        for command in (['--help'], ['tiktok', '--help'], ['tiktok', 'drama-series', '--help'],
                        ['tiktok', 'smartplus-adgroups', '--help'],
                        ['report', 'tiktok', '--help'], ['capabilities', 'show', 'tiktok']):
            run(launcher + command, root, env)
        python = installed / '.runtime/venv' / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
        run([str(python), '-I', '-c',
             "from motata_cli.update import detect_install_method; assert detect_install_method() == 'npm'"],
            root, {**env, 'MOTATA_INSTALL_METHOD': 'npm'})
        assert marker.stat().st_mtime_ns == timestamp, 'runtime was unnecessarily rebuilt'
        print(f'motata {args.version}: first-run bootstrap, repeat launches, npm update channel and read-only help passed')


if __name__ == '__main__':
    main()
