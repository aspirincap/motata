from __future__ import annotations
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from motata_cli.common.errors import CliError
from motata_cli.transport.gateway import RemoteGatewayTransport, GatewayAuthRef
from motata_cli.common.auth import resolve_auth

class TransportTests(unittest.TestCase):
    def test_endpoint_policy(self):
        for endpoint in ('http://evil.example', 'https://user:password@gw.example', 'https://gw.example/?q=x',
                         'https://gw.example/#x', 'file:///tmp/secret', ''):
            with self.subTest(endpoint=endpoint), self.assertRaises(CliError):
                RemoteGatewayTransport(endpoint)

    def test_development_http_only_on_numeric_loopback(self):
        with self.assertRaises(CliError):
            RemoteGatewayTransport('http://localhost:8080', allow_loopback_http=True)
        transport = RemoteGatewayTransport('http://127.0.0.1:8080', allow_loopback_http=True)
        transport.close()

    def test_reference_context_not_token(self):
        with patch.dict(os.environ, {'MOTATA_AUTH_MODE': 'gateway'}, clear=True):
            auth = resolve_auth(account_id='123', media_code='facebook')
            self.assertIsInstance(auth.access_token, GatewayAuthRef)
            self.assertEqual(auth.access_token.account_id, '123')
            with self.assertRaises(CliError):
                resolve_auth(account_id='123', access_token='explicit-secret')

    def test_token_file_reloaded_for_each_request(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'gateway.jwt'
            path.write_text('test.jwt.one'); path.chmod(0o600)
            transport = RemoteGatewayTransport('https://gateway.example', token_file=str(path))
            self.assertEqual(transport._jwt(), 'test.jwt.one')
            path.write_text('test.jwt.two')
            self.assertEqual(transport._jwt(), 'test.jwt.two')
            transport.close()

    def test_token_file_permissions(self):
        if os.name == 'nt':
            self.skipTest('POSIX mode check')
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'gateway.jwt'
            path.write_text('not-a-real-token'); path.chmod(0o644)
            transport = RemoteGatewayTransport('https://gateway.example', token_file=str(path))
            with self.assertRaises(CliError):
                transport._jwt()
            transport.close()

    def test_token_helper_disabled_in_gateway_mode(self):
        import io
        from contextlib import redirect_stderr
        from motata_cli.auth_center.fetch_token import main
        output = io.StringIO()
        with patch.dict(os.environ, {'MOTATA_AUTH_MODE': 'gateway'}, clear=True), redirect_stderr(output):
            self.assertEqual(main([]), 2)
        self.assertIn('disabled', output.getvalue())
