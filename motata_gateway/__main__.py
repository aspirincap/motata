"""Run the experimental gateway with TLS and an external JWT issuer's public JWKS."""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
from .errors import GatewayError


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=8443)
    parser.add_argument('--tls-cert', type=Path)
    parser.add_argument('--tls-key', type=Path)
    parser.add_argument('--development-http', action='store_true')
    args = parser.parse_args(argv)
    if args.development_http:
        if args.host not in ('127.0.0.1', '::1'):
            parser.error('Development HTTP can only bind a loopback address')
    elif not (args.tls_cert and args.tls_key):
        parser.error('Production requires --tls-cert and --tls-key')
    try:
        import uvicorn
        from .jwt_auth import JWTVerifier
        from .state import GatewayState
        from .auth_center import AuthCenterProvider, load_profiles, private_bytes
        from .credentials import CredentialResolver
        from .service import GatewayService
        from .server import GatewayApp
        from .limits import Limits, RateBudget
        config = json.loads(private_bytes(args.config, 65536))
        if set(config) - {'state_dir', 'issuer', 'audience', 'jwks_path', 'algorithm', 'meta_version', 'limits', 'auth_center_profiles', 'jwks_url', 'jwks_cache_ttl', 'rates'}:
            raise ValueError('Unknown configuration field')
        if bool(config.get('jwks_path')) == bool(config.get('jwks_url')):
            raise ValueError('Configure exactly one of jwks_path or jwks_url')
        if config.get('jwks_url'):
            from .jwks import RemoteJWTVerifier
            verifier = RemoteJWTVerifier(issuer=config['issuer'], audience=config['audience'],
                jwks_url=config['jwks_url'], algorithm=config.get('algorithm', 'ES256'),
                cache_ttl=config.get('jwks_cache_ttl', 300))
        else:
            verifier = JWTVerifier(issuer=config['issuer'], audience=config['audience'],
                                   jwks_path=Path(config['jwks_path']), algorithm=config.get('algorithm', 'ES256'))
        state = GatewayState(Path(config['state_dir']))
        provider = None
        if 'auth_center_profiles' in config:
            profile_path = Path(config['auth_center_profiles'])
            if not profile_path.is_absolute():
                raise ValueError('Auth Center profile path must be absolute')
            provider = AuthCenterProvider(load_profiles(profile_path))
        service = GatewayService(verifier=verifier, state=state, meta_version=config['meta_version'],
                                 limits=Limits(**config.get('limits', {})),
                                 credentials=CredentialResolver(state, provider),
                                 rates=RateBudget(**config.get('rates', {'global_rate': 20, 'account_rate': 5})))
        app = GatewayApp(service, allow_local_http=args.development_http)
        # One worker owns the global limits. Multiple workers would multiply quotas.
        uvicorn.run(app, host=args.host, port=args.port, workers=1, proxy_headers=False,
                    access_log=False, server_header=False,
                    ssl_certfile=str(args.tls_cert) if args.tls_cert else None,
                    ssl_keyfile=str(args.tls_key) if args.tls_key else None)
        return 0
    except ImportError:
        parser.exit(1, 'Install the gateway extra on the server: python -m pip install ".[gateway]"\n')
    except (OSError, ValueError, KeyError, TypeError, GatewayError):
        parser.exit(1, 'Gateway configuration or protected state is invalid. No credential details were printed.\n')

if __name__ == '__main__':
    raise SystemExit(main())
