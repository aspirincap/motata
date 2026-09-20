#!/usr/bin/env python3
"""Audit reachable TikTok SDK/raw endpoints against reviewed gateway policies.

This is a source-routing gate, not a platform-acceptance or full CLI parity gate.
The OAuth inventory builder is deliberately replaced by server grant discovery;
it is never enabled as a business credential endpoint.
"""
from __future__ import annotations

import argparse
import ast
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def string_options(node: ast.AST) -> list[str]:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return [node.value]
    if isinstance(node, ast.IfExp):
        return string_options(node.body) + string_options(node.orelse)
    return []


def inventory() -> dict:
    from motata_gateway.policy import TIKTOK_COLLECTIONS, TIKTOK_POST_READS, TIKTOK_WRITES

    source = ast.parse((ROOT / 'motata_cli/tiktok/client.py').read_text())
    methods = set()
    raw = set()
    unresolved = []
    for function in (n for n in ast.walk(source) if isinstance(n, ast.FunctionDef)):
        for node in ast.walk(function):
            if (isinstance(node, ast.Attribute) and isinstance(node.value, ast.Attribute)
                    and isinstance(node.value.value, ast.Name) and node.value.value.id == 'self'
                    and node.value.attr.endswith('_api')):
                methods.add(node.attr)
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            if node.func.attr == '_raw_request' and len(node.args) >= 2:
                verbs, paths = string_options(node.args[0]), string_options(node.args[1])
                if not paths and function.name == '_list_entities_raw':
                    continue  # generic helper's concrete callers are covered below
                if not paths or not verbs:
                    unresolved.append(f'{function.name}:{node.lineno}')
                raw.update((v, p) for v in verbs for p in paths)
            if node.func.attr == '_list_entities_raw' and node.args:
                paths = string_options(node.args[0])
                if not paths:
                    unresolved.append(f'{function.name}:{node.lineno}')
                raw.update(('GET', p) for p in paths)
    routes = {}
    folder = ROOT / 'motata_cli/_vendor/tiktok_business_api_sdk/python_sdk/business_api_client/api'
    for path in sorted(folder.glob('*.py')):
        for function in ast.walk(ast.parse(path.read_text())):
            if not isinstance(function, ast.FunctionDef) or not function.name.endswith('_with_http_info'):
                continue
            for call in ast.walk(function):
                if (isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)
                        and call.func.attr == 'call_api' and len(call.args) >= 2
                        and string_options(call.args[0]) and string_options(call.args[1])):
                    routes[function.name.removesuffix('_with_http_info')] = (
                        string_options(call.args[1])[0],
                        string_options(call.args[0])[0].removeprefix('/open_api/v1.3/'))
    rows = []
    for name in sorted(methods):
        if name not in routes:
            raise ValueError(f'SDK builder not found: {name}')
        method, path = routes[name]
        if name == 'oauth2_advertiser_get':
            if path in TIKTOK_COLLECTIONS:
                raise ValueError('OAuth inventory must not be exposed as an upstream business endpoint')
            status = 'replaced_by_authorized_account_discovery'
        else:
            allowed = TIKTOK_COLLECTIONS if method == 'GET' else set(TIKTOK_POST_READS) | set(TIKTOK_WRITES)
            if path not in allowed:
                raise ValueError(f'Unreviewed SDK route: {name} {method} {path}')
            status = 'reviewed_route'
        rows.append({'builder': name, 'method': method, 'path': path, 'status': status})
    raw_rows = []
    for method, path in sorted(raw):
        allowed = TIKTOK_COLLECTIONS if method == 'GET' else set(TIKTOK_POST_READS) | set(TIKTOK_WRITES)
        if path not in allowed:
            raise ValueError(f'Unreviewed raw route: {method} {path}')
        raw_rows.append({'method': method, 'path': path, 'status': 'reviewed_route'})
    if unresolved:
        raise ValueError('Unclassified dynamic raw routes: ' + ', '.join(unresolved))
    return {
        'schema_version': 1,
        'scope': 'SDK/raw builders reachable from the current TikTokClient; not all hypothetical APIs',
        'evidence': 'Source policy checks plus gateway_tests/test_completion_tiktok.py and workflow tests',
        'sdk': rows,
        'raw': raw_rows,
        'unresolved': unresolved,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--write', action='store_true', help='Regenerate the reviewed routing inventory')
    args = parser.parse_args()
    result = inventory()
    path = ROOT / 'docs/gateway-endpoint-coverage.json'
    if args.write:
        path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    elif not path.exists() or json.loads(path.read_text()) != result:
        raise SystemExit('Gateway endpoint inventory drifted; review before regenerating')
    print(f"Reviewed SDK builders: {sum(r['status'] == 'reviewed_route' for r in result['sdk'])}; "
          f"OAuth inventory replacements: {sum(r['status'] != 'reviewed_route' for r in result['sdk'])}; "
          f"raw method/path pairs: {len(result['raw'])}; unresolved: {len(result['unresolved'])}")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
