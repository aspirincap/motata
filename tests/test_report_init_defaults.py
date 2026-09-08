from __future__ import annotations

import io
import json
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from motata_cli.__main__ import build_parser
from motata_cli.report.meta import command_meta_report_run
from motata_cli.report.tiktok import command_tiktok_report_run


class ReportInitDefaultsTests(unittest.TestCase):
    def test_meta_report_uses_init_defaults_for_dry_run(self) -> None:
        parser = build_parser()
        args = parser.parse_args(
            [
                "report",
                "meta",
                "run",
                "--period",
                "daily",
                "--depth",
                "fast",
                "--dry-run",
            ]
        )

        with (
            patch("motata_cli.report.meta.resolve_default_account", return_value=("123456", "init_config")),
            patch("motata_cli.report.meta.resolve_default_access_token", return_value=("demo-token", "environment")),
            patch("motata_cli.report.meta.resolve_init_metrics", return_value=["spend", "clicks", "purchase_roas"]),
            patch("motata_cli.report.meta.load_init_profile", return_value={"platform": "meta", "account_id": "123456"}),
        ):
            output = io.StringIO()
            with redirect_stdout(output):
                command_meta_report_run(args)

        payload = json.loads(output.getvalue())
        self.assertEqual(payload["platform"], "meta")
        self.assertEqual(payload["account_id"], "123456")
        self.assertEqual(payload["account_source"], "init_config")
        self.assertEqual(payload["options"]["access_token_source"], "environment")
        self.assertEqual(payload["options"]["analysis_metrics"], ["spend", "clicks", "purchase_roas"])
        self.assertEqual(payload["options"]["analysis_metrics_source"], "init_profile")

    def test_tiktok_report_uses_init_defaults_for_dry_run(self) -> None:
        parser = build_parser()
        args = parser.parse_args(
            [
                "report",
                "tiktok",
                "run",
                "--period",
                "daily",
                "--depth",
                "fast",
                "--dry-run",
            ]
        )

        with (
            patch("motata_cli.report.tiktok.resolve_default_account", return_value=("998877", "init_config")),
            patch("motata_cli.report.tiktok.resolve_default_access_token", return_value=("demo-token", "environment")),
            patch("motata_cli.report.tiktok.resolve_init_metrics", return_value=["spend", "conversion", "total_purchase_value"]),
            patch("motata_cli.report.tiktok.load_init_profile", return_value={"platform": "tiktok", "account_id": "998877"}),
        ):
            output = io.StringIO()
            with redirect_stdout(output):
                command_tiktok_report_run(args)

        payload = json.loads(output.getvalue())
        self.assertEqual(payload["platform"], "tiktok")
        self.assertEqual(payload["advertiser_id"], "998877")
        self.assertEqual(payload["account_source"], "init_config")
        self.assertEqual(payload["options"]["access_token_source"], "environment")
        self.assertEqual(payload["options"]["analysis_metrics"], ["spend", "conversion", "total_purchase_value"])
        self.assertEqual(payload["options"]["analysis_metrics_source"], "init_profile")

    def test_meta_report_can_batch_all_init_accounts(self) -> None:
        parser = build_parser()
        args = parser.parse_args(
            [
                "report",
                "meta",
                "run",
                "--all-init-accounts",
                "--period",
                "daily",
                "--depth",
                "fast",
                "--dry-run",
                "--run-dir",
                "build/meta-batch",
            ]
        )

        with (
            patch("motata_cli.report.meta.resolve_default_accounts", return_value=(["111", "222"], "init_profile_accounts")),
            patch("motata_cli.report.meta.resolve_default_access_token", return_value=("demo-token", "environment")),
            patch("motata_cli.report.meta.resolve_init_metrics", return_value=["spend"]),
            patch("motata_cli.report.meta.load_init_profile", return_value={"platform": "meta", "account_ids": ["111", "222"]}),
        ):
            output = io.StringIO()
            with redirect_stdout(output):
                command_meta_report_run(args)

        payload = json.loads(output.getvalue())
        self.assertEqual(payload["mode"], "batch")
        self.assertEqual(payload["account_ids"], ["111", "222"])
        self.assertEqual(payload["run_count"], 2)
        self.assertEqual(payload["runs"][0]["account_id"], "111")
        self.assertEqual(payload["runs"][1]["account_id"], "222")
        self.assertEqual(Path(payload["runs"][0]["run_dir"]).parts[-3:], ("build", "meta-batch", "account_111"))

    def test_tiktok_report_accepts_comma_separated_accounts(self) -> None:
        parser = build_parser()
        args = parser.parse_args(
            [
                "report",
                "tiktok",
                "run",
                "--advertiser-id",
                "aaa,bbb",
                "--period",
                "daily",
                "--depth",
                "fast",
                "--dry-run",
                "--run-dir",
                "build/tiktok-batch",
            ]
        )

        with (
            patch("motata_cli.report.tiktok.resolve_default_access_token", return_value=("demo-token", "environment")),
            patch("motata_cli.report.tiktok.resolve_init_metrics", return_value=["spend"]),
            patch("motata_cli.report.tiktok.load_init_profile", return_value={"platform": "tiktok"}),
        ):
            output = io.StringIO()
            with redirect_stdout(output):
                command_tiktok_report_run(args)

        payload = json.loads(output.getvalue())
        self.assertEqual(payload["mode"], "batch")
        self.assertEqual(payload["advertiser_ids"], ["aaa", "bbb"])
        self.assertEqual(payload["run_count"], 2)
        self.assertEqual(payload["runs"][0]["advertiser_id"], "aaa")
        self.assertEqual(payload["runs"][1]["advertiser_id"], "bbb")
        self.assertEqual(Path(payload["runs"][1]["run_dir"]).parts[-3:], ("build", "tiktok-batch", "advertiser_bbb"))


if __name__ == "__main__":
    unittest.main()
