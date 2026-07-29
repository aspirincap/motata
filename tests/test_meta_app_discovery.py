from __future__ import annotations

import unittest
from unittest.mock import patch

from motata_cli.__main__ import build_parser
from motata_cli.meta import commands as meta_commands
from motata_cli.meta.app_discovery import build_meta_app_report


class FakeMetaAppClient:
    def get(self, path, *, params=None):
        if path == "act_123":
            return {"id": "act_123", "account_id": "123", "name": "Demo Account"}
        if path == "act_123/insights":
            return {"data": [{"spend": "20", "impressions": "200", "clicks": "10"}]}
        if path == "cmp-1":
            return {"id": "cmp-1", "name": "app campaign", "objective": "OUTCOME_APP_PROMOTION", "effective_status": "ACTIVE"}
        if path == "app-1":
            return {"id": "app-1", "name": "Meta Demo App", "category": "Utilities"}
        raise AssertionError(f"unexpected get path: {path}")

    def paginate(self, path, *, params=None):
        if path == "act_123/insights" and (params or {}).get("level") == "campaign":
            return [
                {
                    "campaign_id": "cmp-1",
                    "campaign_name": "app campaign",
                    "spend": "20",
                    "impressions": "200",
                    "clicks": "10",
                }
            ]
        if path == "cmp-1/adsets":
            return [
                {
                    "id": "adset-1",
                    "campaign_id": "cmp-1",
                    "promoted_object": {
                        "application_id": "app-1",
                        "object_store_url": "https://play.google.com/store/apps/details?id=meta.demo",
                    },
                }
            ]
        if path == "cmp-1/ads":
            return [{"id": "ad-1", "campaign_id": "cmp-1", "adset_id": "adset-1"}]
        raise AssertionError(f"unexpected paginate path: {path}")


class MetaAppDiscoveryTests(unittest.TestCase):
    def test_apps_analyze_command_is_exposed(self) -> None:
        parser = build_parser()

        args = parser.parse_args(
            [
                "meta",
                "apps",
                "analyze",
                "--access-token",
                "demo-token",
                "--account",
                "123",
                "--account-limit",
                "2",
                "--campaign-limit",
                "20",
            ]
        )

        self.assertIs(args.func, meta_commands.command_apps_analyze)
        self.assertEqual(args.account_limit, 2)
        self.assertEqual(args.campaign_limit, 20)
        self.assertEqual(args.profile, "full")

    def test_report_groups_spend_by_promoted_app(self) -> None:
        report = build_meta_app_report(
            FakeMetaAppClient(),
            account_id="123",
            date_preset="last_14d",
            account_limit=2,
            campaign_limit=20,
        )

        self.assertEqual(report["app_count"], 1)
        self.assertEqual(report["rows"][0]["app_ids"], ["app-1"])
        self.assertEqual(report["rows"][0]["app_names"], ["Meta Demo App"])
        self.assertEqual(report["rows"][0]["platforms"], ["android"])
        self.assertEqual(report["rows"][0]["spend"], 20.0)

    def test_batch_profile_caps_campaign_probe_count(self) -> None:
        report = build_meta_app_report(
            FakeMetaAppClient(),
            account_id="123",
            date_preset="last_14d",
            account_limit=2,
            campaign_limit=20,
            profile="batch",
        )

        self.assertEqual(report["profile"], "batch")
        self.assertEqual(report["campaign_limit"], 10)

    @patch("motata_cli.meta.app_discovery.requests.get")
    def test_report_detects_w2a_redirect_links_as_apps(self, mock_get) -> None:
        class RedirectResponse:
            url = "https://apps.apple.com/app/id1269972832?mt=8"
            history = []
            headers = {}
            text = ""

        class W2AMeta(FakeMetaAppClient):
            def get(self, path, *, params=None):
                if path == "cmp-1":
                    return {"id": "cmp-1", "name": "W2A campaign", "objective": "OUTCOME_SALES"}
                return super().get(path, params=params)

            def paginate(self, path, *, params=None):
                if path == "act_123/insights" and (params or {}).get("level") == "campaign":
                    return [
                        {
                            "campaign_id": "cmp-1",
                            "campaign_name": "W2A_A_US_AEO_Purchase",
                            "objective": "OUTCOME_SALES",
                            "spend": "20",
                            "impressions": "200",
                            "clicks": "10",
                        }
                    ]
                if path == "cmp-1/adsets":
                    return [{"id": "adset-1", "name": "iOS14_W2A_US", "campaign_id": "cmp-1"}]
                if path == "cmp-1/ads":
                    return [
                        {
                            "id": "ad-1",
                            "name": "app ad",
                            "campaign_id": "cmp-1",
                            "adset_id": "adset-1",
                            "creative": {"link_url": "https://app.adjust.com/1lwap8c2"},
                        }
                    ]
                return super().paginate(path, params=params)

        mock_get.return_value = RedirectResponse()
        report = build_meta_app_report(
            W2AMeta(),
            account_id="123",
            date_preset="last_14d",
            account_limit=2,
            campaign_limit=20,
        )

        row = report["rows"][0]
        self.assertEqual(row["platforms"], ["ios"])
        self.assertEqual(row["store_urls"], ["https://apps.apple.com/app/id1269972832?mt=8"])
        self.assertEqual(row["w2a_urls"], ["https://app.adjust.com/1lwap8c2"])
        self.assertEqual(row["redirect_probe_count"], 1)


if __name__ == "__main__":
    unittest.main()
