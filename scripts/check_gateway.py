#!/usr/bin/env python3
"""Gateway unit/integration gate; --release fails while full CLI coverage is incomplete."""
from __future__ import annotations
import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--release', action='store_true')
    args = parser.parse_args()
    # CI/provisioning installs dependencies. Tests themselves must not contact the network.
    guard = '''import sys

def reject_network(event, args):
    if event in ('socket.connect', 'socket.getaddrinfo', 'socket.sendto', 'socket.bind'):
        raise RuntimeError('Gateway tests prohibit real network access')
sys.addaudithook(reject_network)
'''
    with tempfile.TemporaryDirectory(prefix='motata-gateway-test-') as directory:
        home = Path(directory)
        (home / 'sitecustomize.py').write_text(guard)
        env = {k: v for k, v in os.environ.items() if not any(x in k.upper() for x in ('TOKEN', 'SECRET', 'API_KEY', 'PASSWORD'))}
        env.update(HOME=str(home), MOTATA_HOME=str(home / '.motata'), MOTATA_SUPPRESS_SKILLS_NOTICE='1',
                   PYTHONPATH=os.pathsep.join((str(home), str(ROOT))), PYTHONDONTWRITEBYTECODE='1')
        subprocess.run([sys.executable, '-m', 'unittest', 'discover', '-s', 'gateway_tests', '-v'], cwd=ROOT, env=env, check=True)
        subprocess.run([sys.executable, 'scripts/gateway_inventory.py'], cwd=ROOT, env=env, check=True)
        subprocess.run([sys.executable, 'scripts/check_gateway_endpoints.py'], cwd=ROOT, env=env, check=True)
        if args.release:
            subprocess.run([sys.executable, 'scripts/gateway_inventory.py', '--release'], cwd=ROOT, env=env, check=True)
    print('Experimental gateway gate passed; this is NOT a complete migration or production security certification.')
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
