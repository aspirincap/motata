#!/usr/bin/env python3
"""Deterministic coverage inventory; --release additionally requires every gate."""
from __future__ import annotations
import argparse
import ast
import json
import hashlib
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def commands() -> list[dict]:
    from motata_cli.__main__ import build_parser
    rows = []
    def walk(parser, path):
        groups = [a for a in parser._actions if isinstance(a, argparse._SubParsersAction)]
        if not groups:
            if path:
                local = path[0] in {'product', 'metrics', 'update'} or path == ['gateway', 'status']
                rows.append({'command': 'motata ' + ' '.join(path),
                             'classification': ('GATEWAY_SESSION_OPERATION' if path[0] == 'gateway' else
                                                'LOCAL_NO_CREDENTIAL' if local else 'GATEWAY_PLATFORM_OPERATION'),
                             'handler': getattr(parser.get_default('func'), '__name__', None),
                             'options_sha256': hashlib.sha256(json.dumps(sorted({s for a in parser._actions for s in a.option_strings}), separators=(',', ':')).encode()).hexdigest(),
                             'coverage': 'not_migrated'})
            return
        for group in groups:
            for name, child in sorted(group.choices.items()):
                walk(child, path + [name])
    walk(build_parser(), [])
    # Separate executable entry points are part of the classification too.
    for name in ('motata-token', 'motata-auth-center-token'):
        rows.append({'command': name, 'classification': 'TRUSTED_ADMIN_OPERATION',
                     'handler': 'auth_center.fetch_token.main', 'options_sha256': hashlib.sha256(b'[]').hexdigest(), 'coverage': 'not_migrated'})
    rows.append({'command': 'motata-handoff', 'classification': 'LOCAL_NO_CREDENTIAL',
                 'handler': 'handoff.main', 'options_sha256': hashlib.sha256(b'[]').hexdigest(), 'coverage': 'not_migrated'})
    return sorted(rows, key=lambda x: x['command'])


def network_exits() -> list[dict]:
    rows = []
    for path in sorted((ROOT / 'motata_cli').rglob('*.py')):
        if '_vendor' in path.parts:
            continue  # SDK call sites listed separately; vendor boundary is tracked below.
        tree = ast.parse(path.read_text())
        parents = {}
        for parent in ast.walk(tree):
            for child in ast.iter_child_nodes(parent):
                parents[child] = parent
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = ast.unparse(node.func)
            network = (name.startswith(('requests.', 'urllib.request.', 'httpx.'))
                       or name in ('getattr(requests, method)', 'self._invoke')
                       or name.startswith('self.api_client.') and name.endswith(('request', 'call_api')))
            if not network or name.endswith(('RequestException', 'Timeout', 'ConnectionError', 'Request')):
                continue
            parent = node
            while parent in parents and not isinstance(parent, (ast.FunctionDef, ast.AsyncFunctionDef)):
                parent = parents[parent]
            function = getattr(parent, 'name', '<module>')
            rel = str(path.relative_to(ROOT))
            kind = 'credential_admin' if '/auth_center/' in rel else 'requires_review'
            if '/product/' in rel or rel.endswith('/update.py'):
                kind = 'public_no_platform_credential'
            rows.append({'file': rel, 'line': node.lineno, 'function': function,
                         'call': name, 'classification': kind, 'coverage': 'not_migrated'})
    rows.append({'file': 'motata_cli/_vendor/tiktok_business_api_sdk/python_sdk/business_api_client/api_client.py',
                 'line': 1, 'function': 'ApiClient.request', 'call': 'vendored_sdk_transport',
                 'classification': 'platform_authenticated', 'coverage': 'not_migrated'})
    return sorted(rows, key=lambda r: (r['file'], r['line'], r['call']))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--write', action='store_true')
    parser.add_argument('--release', action='store_true')
    args = parser.parse_args()
    pairs = [('gateway-command-matrix.json', commands()), ('gateway-network-exits.json', network_exits())]
    for name, data in pairs:
        path = ROOT / 'docs' / name
        if args.write:
            # Preserve evidence only for the exact unchanged source identity;
            # new/changed commands or network sites still require review.
            identity = lambda row: json.dumps({k: v for k, v in row.items()
                if k not in ('coverage', 'tests', 'notes')}, sort_keys=True)
            previous = json.loads(path.read_text())['entries'] if path.exists() else []
            evidence = {identity(row): row for row in previous}
            for row in data:
                old = evidence.get(identity(row), {})
                row.update({k: old[k] for k in ('coverage', 'tests', 'notes') if k in old})
            path.write_text(json.dumps({'schema_version': 1, 'baseline': '55f8fd452063001b2b608ca9e1e664fed3269b32',
                                        'entries': data}, indent=2) + '\n')
        else:
            saved = json.loads(path.read_text())['entries']
            # Mutable coverage evidence does not change source inventory identity.
            strip = lambda rows: [{k: v for k, v in r.items() if k not in ('coverage', 'tests', 'notes')} for r in rows]
            if strip(saved) != strip(data):
                raise SystemExit(f'{name} has drifted; regenerate and review every new entry')
            if args.release and any(r['coverage'] != 'complete' for r in saved):
                raise SystemExit(f'{name}: full gateway migration is NOT complete')
        print(f'{name}: {len(data)} entries')
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
