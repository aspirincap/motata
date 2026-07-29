from __future__ import annotations

import unittest

from motata_cli.__main__ import build_parser
from motata_cli.init_command import command_init
from motata_cli.meta import commands as meta_commands
from motata_cli.product import commands as product_commands
from motata_cli.report.meta import command_meta_report_run


class CliNamespaceTests(unittest.TestCase):
    def test_meta_commands_live_under_meta_prefix(self) -> None:
        parser = build_parser()

        args = parser.parse_args(
            [
                "meta",
                "accounts",
                "list",
                "--access-token",
                "demo-token",
            ]
        )

        self.assertIs(args.func, meta_commands.command_accounts_list)

    def test_meta_singular_aliases_route_to_same_handlers(self) -> None:
        parser = build_parser()

        args = parser.parse_args(
            [
                "meta",
                "campaign",
                "list",
                "--account-id",
                "123",
                "--access-token",
                "demo-token",
            ]
        )

        self.assertIs(args.func, meta_commands.command_campaigns_list)

    def test_meta_auth_namespace_is_not_exposed(self) -> None:
        parser = build_parser()

        with self.assertRaises(SystemExit):
            parser.parse_args(
                [
                    "meta",
                    "auth",
                    "fetch-token",
                    "--account-id",
                    "123",
                ]
            )

    def test_legacy_top_level_meta_command_is_not_exposed(self) -> None:
        parser = build_parser()

        with self.assertRaises(SystemExit):
            parser.parse_args(
                [
                    "accounts",
                    "list",
                    "--access-token",
                    "demo-token",
                ]
            )

    def test_product_intake_command_is_exposed_under_product_namespace(self) -> None:
        parser = build_parser()

        args = parser.parse_args(
            [
                "product",
                "intake",
                "https://example.com/product",
            ]
        )

        self.assertIs(args.func, product_commands.command_product_intake)

    def test_init_command_is_exposed_at_top_level(self) -> None:
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
                "123",
            ]
        )

        self.assertIs(args.func, command_init)
        self.assertEqual(args.platform, "meta")
        self.assertEqual(args.token_source, "direct")

    def test_report_meta_run_command_is_exposed_with_period_and_depth(self) -> None:
        parser = build_parser()

        args = parser.parse_args(
            [
                "report",
                "meta",
                "run",
                "--account-id",
                "123",
                "--access-token",
                "demo-token",
                "--period",
                "weekly",
                "--depth",
                "standard",
                "--dry-run",
            ]
        )

        self.assertIs(args.func, command_meta_report_run)
        self.assertEqual(args.period, "weekly")
        self.assertEqual(args.depth, "standard")


if __name__ == "__main__":
    unittest.main()
