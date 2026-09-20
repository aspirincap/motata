"""Use the bundled SDK's request builders, never its socket/thread-pool client.

Only a typed, non-secret placeholder may occupy the generated access-token slot.
The public call_api seam preserves SDK parameter validation without monkeypatches.
"""
from __future__ import annotations
from contextlib import ExitStack
from pathlib import Path
from typing import Any
from .gateway import GatewayAuthRef
from motata_cli.common.errors import CliError


class SDKCredentialRef:
    """Marker used solely to satisfy generated SDK 'not None' validation."""
    def __repr__(self):
        return '<gateway-auth-reference>'


SDK_AUTH = SDKCredentialRef()


def plain(value: Any):
    if hasattr(value, 'to_dict'):
        return plain(value.to_dict())
    if isinstance(value, dict):
        return {k: plain(v) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [plain(v) for v in value]
    if value is None or type(value) in (str, int, float, bool):
        return value
    raise CliError('Unsupported SDK request value in gateway mode.')


class GatewaySDKClient:
    def __init__(self, transport, auth: GatewayAuthRef):
        self.transport, self.auth = transport, auth

    @staticmethod
    def select_header_accept(values):
        return 'application/json'

    @staticmethod
    def select_header_content_type(values):
        return values[0] if values else 'application/json'

    @staticmethod
    def _pairs(values):
        result = {}
        for key, value in values or ():
            if key in result:
                raise CliError('Duplicate SDK request parameter is not supported.')
            if value is not None:
                result[key] = plain(value)
        return result

    def call_api(self, resource_path, method, path_params=None, query_params=None,
                 header_params=None, body=None, post_params=None, files=None,
                 response_type=None, auth_settings=None, async_req=None,
                 _return_http_data_only=True, _preload_content=True,
                 _request_timeout=None, collection_formats=None):
        prefix = '/open_api/v1.3/'
        if not resource_path.startswith(prefix) or path_params or async_req or not _preload_content:
            raise CliError('Unsupported SDK transport options in gateway mode.')
        for key, value in (header_params or {}).items():
            if key.lower() == 'access-token':
                if value is not SDK_AUTH:
                    raise CliError('Real platform tokens are forbidden in the gateway SDK adapter.')
            elif key.lower() not in ('accept', 'content-type'):
                raise CliError('SDK custom headers are forbidden in gateway mode.')
        if auth_settings:
            raise CliError('SDK authentication settings cannot override gateway policy.')
        query, form = self._pairs(query_params), self._pairs(post_params)
        if form and body is not None:
            raise CliError('Ambiguous SDK form/JSON body.')
        # Keep array/dict query values structured. The gateway owns their wire encoding.
        # TikTok's generated multi format used Python repr for nested lists; JSON is
        # deliberate here and covered by gateway fixtures, not a secret-bearing SDK path.
        with ExitStack() as stack:
            streams = {}
            for field, paths in (files or {}).items():
                if isinstance(paths, list):
                    if len(paths) != 1:
                        raise CliError('One file per SDK upload field is supported.')
                    paths = paths[0]
                path = Path(paths).expanduser()
                streams[field] = (path.name, stack.enter_context(path.open('rb')))
            result = self.transport.request(
                auth=self.auth, method=method, path=resource_path[len(prefix):],
                query=query, body=form or plain(body), body_encoding='form' if form or streams else 'json',
                files=streams or None)
        data = result['data']
        return data if _return_http_data_only else (data, result['status'], {})
