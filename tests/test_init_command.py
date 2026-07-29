from __future__ import annotations

import io
import json
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from motata_cli.__main__ import build_parser
from motata_cli.init_command import aggregate_user_type_reports, command_init
from motata_cli.meta.commands import CliError


class InitCommandTests(unittest.TestCase):
    def test_aggregate_user_type_reports_merges_scores(self) -> None:
        payload = aggregate_user_type_reports(
            [
                {"all_types": [{"type": "电商", "raw_score": 10}, {"type": "工具", "raw_score": 4}], "accounts": [{"account_id": "1"}], "campaigns": [], "scraped_content": [], "errors": []},
                {"all_types": [{"type": "电商", "raw_score": 6}, {"type": "小说", "raw_score": 8}], "accounts": [{"account_id": "2"}], "campaigns": [], "scraped_content": [], "errors": []},
            ]
        )
        self.assertEqual(payload["top_types"][0]["type"], "电商")
        self.assertEqual(payload["top_types"][0]["raw_score"], 16.0)
        self.assertEqual(payload["account_count"], 2)

    def test_init_requires_token_before_continuing(self) -> None:
        parser = build_parser()
        args = parser.parse_args(
            [
                "init",
                "--platform",
                "meta",
                "--token-source",
                "direct",
                "--yes",
            ]
        )

        with self.assertRaises(CliError) as ctx:
            command_init(args)

        self.assertIn("token is required", str(ctx.exception))

    def test_init_persists_summary_and_returns_report_suggestion(self) -> None:
        parser = build_parser()
        args = parser.parse_args(
            [
                "init",
                "--platform",
                "tiktok",
                "--token-source",
                "direct",
                "--access-token",
                "demo-token",
                "--account-id",
                "998877",
                "--metric",
                "spend",
                "--metric",
                "clicks",
                "--yes",
            ]
        )

        saved_payload: dict = {}

        def fake_save_config(payload: dict) -> None:
            saved_payload.clear()
            saved_payload.update(payload)

        with (
            patch("motata_cli.init_command.discover_accounts", return_value=[{"id": "998877", "name": "Demo Advertiser"}]),
            patch(
                "motata_cli.init_command.classify_user_type_for_init",
                return_value={"top_types": [{"type": "电商", "index": 92.3, "raw_score": 18.2}]},
            ),
            patch("motata_cli.init_command.load_config", return_value={"values": {}, "account_aliases": {}}),
            patch("motata_cli.init_command.save_config", side_effect=fake_save_config),
        ):
            output = io.StringIO()
            with redirect_stdout(output):
                command_init(args)

        payload = json.loads(output.getvalue())
        self.assertEqual(payload["status"], "initialized")
        self.assertEqual(payload["platform"], "tiktok")
        self.assertEqual(payload["account"]["id"], "998877")
        self.assertEqual(payload["user_type"]["selected"], "电商")
        self.assertEqual(payload["metrics"]["selected"], ["spend", "clicks"])
        self.assertIn("motata report tiktok run", payload["next_steps"]["suggested_daily_report"])
        self.assertEqual(saved_payload["values"]["default_platform"], "tiktok")
        self.assertEqual(saved_payload["values"]["default_tiktok_account"], "998877")
        self.assertEqual(saved_payload["init_profiles"]["tiktok"]["metrics"], ["spend", "clicks"])

    def test_init_accepts_multiple_account_ids_and_uses_first_as_default(self) -> None:
        parser = build_parser()
        args = parser.parse_args(
            [
                "init",
                "--platform",
                "meta",
                "--token-source",
                "direct",
                "--access-token",
                "demo-token",
                "--account-id",
                "123,456",
                "--yes",
            ]
        )

        saved_payload: dict = {}

        def fake_save_config(payload: dict) -> None:
            saved_payload.clear()
            saved_payload.update(payload)

        with (
            patch(
                "motata_cli.init_command.discover_accounts",
                return_value=[
                    {"id": "123", "name": "Account A", "status": "ACTIVE"},
                    {"id": "456", "name": "Account B", "status": "ACTIVE"},
                ],
            ),
            patch(
                "motata_cli.init_command.classify_user_type_for_init",
                return_value=(
                    {"top_types": [{"type": "工具", "index": 80.0, "raw_score": 10.0}]},
                    {"mode": "selected_accounts", "primary_account_id": "123", "selected_account_ids": ["123", "456"]},
                ),
            ),
            patch("motata_cli.init_command.load_config", return_value={"values": {}, "account_aliases": {}}),
            patch("motata_cli.init_command.save_config", side_effect=fake_save_config),
        ):
            output = io.StringIO()
            with redirect_stdout(output):
                command_init(args)

        payload = json.loads(output.getvalue())
        self.assertEqual(payload["account"]["id"], "123")
        self.assertEqual(payload["account"]["ids"], ["123", "456"])
        self.assertEqual(payload["user_type"]["scope"]["mode"], "selected_accounts")
        self.assertEqual(saved_payload["values"]["default_account"], "123")
        self.assertEqual(saved_payload["init_profiles"]["meta"]["account_ids"], ["123", "456"])


if __name__ == "__main__":
    unittest.main()
