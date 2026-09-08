from __future__ import annotations

import argparse
import contextlib
import io
import unittest
from unittest.mock import Mock, patch

import requests
from motata_cli import __main__ as cli
from motata_cli.common.errors import CliError


class MainContractTests(unittest.TestCase):
    def run_handler(self, handler, **flags):
        parser = Mock()
        parser.parse_args.return_value = argparse.Namespace(func=handler, command="test", **flags)
        stderr = io.StringIO()
        with patch.object(cli, "build_parser", return_value=parser), patch.object(cli, "ensure_dirs"), patch.object(
            cli, "maybe_emit_skills_drift_notice"
        ), contextlib.redirect_stderr(stderr):
            result = cli.main([])
        return result, stderr.getvalue()

    def test_legacy_success_and_explicit_exit_codes(self):
        self.assertEqual(self.run_handler(lambda _: None)[0], 0)
        self.assertEqual(self.run_handler(lambda _: {"status": "partial_success"})[0], 3)
        self.assertEqual(self.run_handler(lambda _: {"status": "failed"})[0], 1)
        self.assertEqual(self.run_handler(lambda _: {"exit_code": 3})[0], 3)
        self.assertEqual(self.run_handler(lambda _: 1)[0], 1)

    def test_cli_error_redacts_and_preserves_exit_code(self):
        def handler(_):
            raise CliError("request?access_token=FAKE_TEST_SECRET", exit_code=3)
        code, error = self.run_handler(handler)
        self.assertEqual(code, 3)
        self.assertNotIn("FAKE_TEST_SECRET", error)

    def test_network_error_does_not_echo_arbitrary_response(self):
        def handler(_):
            raise requests.ConnectionError("arbitrary sensitive body")
        code, error = self.run_handler(handler)
        self.assertEqual(code, 1)
        self.assertNotIn("arbitrary sensitive body", error)

    def test_live_probe_requires_explicit_opt_in_before_handler(self):
        handler = Mock()
        code, _ = self.run_handler(handler, requires_live_probe=True, allow_live_probe=False)
        self.assertEqual(code, 1)
        handler.assert_not_called()
        handler.return_value = None
        self.assertEqual(self.run_handler(handler, requires_live_probe=True, allow_live_probe=True)[0], 0)
        handler.assert_called_once()

    def test_help_does_not_create_state_or_check_network(self):
        with patch.object(cli, "ensure_dirs") as ensure, patch.object(cli, "maybe_emit_skills_drift_notice") as notice:
            with contextlib.redirect_stdout(io.StringIO()), self.assertRaises(SystemExit) as exit_:
                cli.main(["--help"])
        self.assertEqual(exit_.exception.code, 0)
        ensure.assert_not_called()
        notice.assert_not_called()

    def test_validation_parsers_describe_live_side_effects(self):
        parser = cli.build_parser()
        args = parser.parse_args(["meta", "validate", "ad-link", "--account-id", "123", "--adset-id", "1", "--creative-id", "2"])
        self.assertTrue(args.requires_live_probe)
        self.assertFalse(args.allow_live_probe)


if __name__ == "__main__":
    unittest.main()
