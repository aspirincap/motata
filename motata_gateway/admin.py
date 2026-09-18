"""Trusted offline administration; never exposed on the gateway HTTP surface."""
from __future__ import annotations
import argparse
import getpass
import json
from pathlib import Path
from .state import GatewayState
from .errors import GatewayError


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state-dir', type=Path, required=True)
    commands = parser.add_subparsers(dest='operation', required=True)
    commands.add_parser('init')
    commands.add_parser('status')
    p = commands.add_parser('bind-auth-center')
    p.add_argument('--ref', required=True)
    p.add_argument('--profile', required=True)
    p.add_argument('--tenant', required=True)
    p.add_argument('--account', required=True)
    p.add_argument('--platform', choices=['meta', 'tiktok'], required=True)
    p.add_argument('--channel', choices=['meta', 'facebook', 'tiktok'], required=True)
    p.add_argument('--oauth-agent-id', required=True)
    p.add_argument('--verified-binding', action='store_true', required=True,
                   help='Trusted administrator has verified this OAuth identity can use this account')
    p = commands.add_parser('import')
    p.add_argument('--ref', required=True)
    p.add_argument('--platform', choices=['meta', 'tiktok'], required=True)
    p = commands.add_parser('disable-credential')
    p.add_argument('--ref', required=True)
    for name in ('grant', 'remove-grant'):
        p = commands.add_parser(name)
        p.add_argument('--subject', required=True)
        p.add_argument('--client', required=True)
        p.add_argument('--workspace', required=True)
        p.add_argument('--platform', choices=['meta', 'tiktok'], required=True)
        p.add_argument('--account', required=True)
        if name == 'grant':
            p.add_argument('--ref', required=True)
    p = commands.add_parser('revoke')
    p.add_argument('--kind', choices=['sub', 'sid', 'jti', 'kid'], required=True)
    p.add_argument('--value', required=True)
    args = parser.parse_args(argv)
    try:
        state = GatewayState(args.state_dir, create=args.operation == 'init')
        if args.operation == 'import':
            value = getpass.getpass('Platform access token (hidden; trusted administrator only): ')
            state.put_credential(args.ref, args.platform, value)
        elif args.operation == 'bind-auth-center':
            from .auth_center import AuthCenterBinding
            binding = AuthCenterBinding(profile=args.profile, tenant_id=args.tenant, account_id=args.account,
                                        platform=args.platform, channel=args.channel, oauth_agent_id=args.oauth_agent_id)
            state.put_auth_center_binding(args.ref, binding)
        elif args.operation in ('grant', 'remove-grant'):
            kwargs = dict(subject=args.subject, client=args.client, workspace=args.workspace,
                          platform=args.platform, account=args.account)
            if args.operation == 'grant':
                state.grant(**kwargs, reference=args.ref)
            else:
                state.remove_grant(**kwargs)
        elif args.operation == 'revoke':
            state.revoke(args.kind, args.value)
        elif args.operation == 'disable-credential':
            state.disable_credential(args.ref)
        elif args.operation == 'status':
            print(json.dumps(state.safe_status(), indent=2))
            return 0
        print(json.dumps({'ok': True, 'operation': args.operation}))
        return 0
    except (OSError, ValueError, GatewayError):
        print(json.dumps({'ok': False, 'error': 'Administration failed. Check private state and references.'}))
        return 1

if __name__ == '__main__':
    raise SystemExit(main())
