from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from motata_cli.__main__ import build_parser
from motata_cli.meta import commands as meta_commands
from motata_cli.meta.metrics import build_meta_metric_probe
from motata_cli.metrics.catalog import MetricGroup, MetricSpec, TIKTOK_METRIC_MAP
from motata_cli.metrics.core import build_core_metric_coverage
from motata_cli.metrics.presets import recommend_metric_presets
from motata_cli.metrics.probe import is_active_value, probe_grouped_metrics, sanitize_error_message
from motata_cli.tiktok import commands as tiktok_commands
from motata_cli.tiktok.metrics import build_tiktok_metric_probe


class MetricsProbeCoreTests(unittest.TestCase):
    def test_active_value_definition_treats_zero_dash_and_empty_as_empty(self) -> None:
        for value in (None, "", " ", "-", "0", "0.0", 0, 0.0, [], [{"action_type": "purchase", "value": "0"}]):
            self.assertFalse(is_active_value(value), value)
        self.assertTrue(is_active_value("1"))
        self.assertTrue(is_active_value([{"action_type": "purchase", "value": "2"}]))

    def test_group_failure_is_bisected_to_single_unsupported_metric(self) -> None:
        groups = [MetricGroup("meta", "demo", ("spend", "bad_metric", "clicks"), "core")]
        specs = {
            name: MetricSpec("meta", name, name, "core")
            for name in ("spend", "bad_metric", "clicks")
        }

        def request_fn(group: MetricGroup, metrics: tuple[str, ...]):
            if "bad_metric" in metrics:
                raise RuntimeError("unsupported field")
            return [{"spend": "10", "clicks": "0"}]

        result = probe_grouped_metrics(platform="meta", groups=groups, specs=specs, request_fn=request_fn)

        self.assertEqual([item["metric"] for item in result["active_metrics"]], ["spend"])
        self.assertEqual([item["metric"] for item in result["supported_empty_metrics"]], ["clicks"])
        self.assertEqual([item["metric"] for item in result["unsupported_metrics"]], ["bad_metric"])
        self.assertGreater(result["request_stats"]["split_count"], 0)

    def test_probe_error_redacts_tokens(self) -> None:
        message = (
            "HTTPSConnectionPool(host='graph.facebook.com'): "
            "/insights?access_token=EAABsecret123&fields=spend "
            "Authorization: Bearer EAAanothersecret "
            "'access_token': 'EAAreprsecret' Access-Token: tiksecret"
        )

        redacted = sanitize_error_message(message)

        self.assertNotIn("EAABsecret123", redacted)
        self.assertNotIn("EAAanothersecret", redacted)
        self.assertNotIn("EAAreprsecret", redacted)
        self.assertNotIn("tiksecret", redacted)
        self.assertIn("access_token=<redacted>", redacted)
        self.assertIn("Bearer <redacted>", redacted)


class FakeMetaMetricsClient:
    def get(self, path: str, *, params: dict | None = None) -> dict:
        if path == "act_123/insights":
            return {"data": [{"spend": "99", "impressions": "1000", "clicks": "30"}]}
        raise AssertionError(f"Unexpected get path: {path}")

    def paginate_insights(self, path: str, *, params: dict | None = None, prefer_async: bool = False, auto_async: bool = True):
        fields = set(str((params or {}).get("fields") or "").split(","))
        row = {field: "0" for field in fields if field}
        row.update(
            {
                "spend": "99",
                "impressions": "1000",
                "clicks": "30",
                "actions": [{"action_type": "purchase", "value": "2"}],
                "action_values": [{"action_type": "purchase", "value": "123.45"}],
                "purchase_roas": [{"action_type": "purchase", "value": "1.7"}],
                "video_play_actions": [{"action_type": "video_view", "value": "0"}],
            }
        )
        return [row]


class MetaMetricProbeTests(unittest.TestCase):
    def test_meta_metrics_probe_command_is_exposed(self) -> None:
        args = build_parser().parse_args(
            [
                "meta",
                "metrics",
                "probe",
                "--account",
                "123",
                "--access-token",
                "demo-token",
                "--since",
                "2026-05-01",
                "--until",
                "2026-05-09",
            ]
        )

        self.assertIs(args.func, meta_commands.command_metrics_probe)
        self.assertEqual(args.account_id, "123")
        self.assertEqual(args.limit, 25)
        self.assertEqual(args.profile, "batch")

    def test_meta_probe_parses_action_and_roas_metrics(self) -> None:
        result = build_meta_metric_probe(
            FakeMetaMetricsClient(),
            account_id="123",
            since="2026-05-01",
            until="2026-05-09",
        )
        active = {item["metric"]: item for item in result["active_metrics"]}
        empty = {item["metric"] for item in result["supported_empty_metrics"]}

        self.assertIn("actions", active)
        self.assertIn("action_values", active)
        self.assertIn("purchase_roas", active)
        self.assertIn("video_play_actions", empty)
        self.assertEqual(result["strategy"], "grouped_batch")


class FakeTikTokMetricsClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, ...]] = []

    def integrated_report(self, report_type: str, **kwargs):
        metrics = tuple(kwargs.get("metrics") or [])
        self.calls.append(metrics)
        if "voucher_spend" in metrics:
            raise RuntimeError("metric combination unsupported")
        row_metrics = {metric: "0" for metric in metrics}
        row_metrics.update(
            {
                "spend": "50",
                "impressions": "500",
                "clicks": "20",
                "video_play_actions": "300",
                "complete_payment": "-",
                "app_install": "",
            }
        )
        dimensions = {dimension: "123" for dimension in kwargs.get("dimensions") or []}
        return {"data": {"list": [{"dimensions": dimensions, "metrics": row_metrics}]}}


class TikTokMetricProbeTests(unittest.TestCase):
    def test_tiktok_metrics_probe_command_is_exposed(self) -> None:
        args = build_parser().parse_args(
            [
                "tiktok",
                "metrics",
                "probe",
                "--advertiser-id",
                "123",
                "--access-token",
                "demo-token",
                "--since",
                "2026-05-01",
                "--until",
                "2026-05-09",
            ]
        )

        self.assertIs(args.func, tiktok_commands.command_tiktok_metrics_probe)
        self.assertEqual(args.advertiser_ids, ["123"])
        self.assertEqual(args.start_date, "2026-05-01")
        self.assertEqual(args.profile, "batch")

    def test_tiktok_probe_splits_failing_group_and_keeps_dash_empty(self) -> None:
        result = build_tiktok_metric_probe(
            FakeTikTokMetricsClient(),
            advertiser_ids=["123"],
            start_date="2026-05-01",
            end_date="2026-05-09",
        )
        active = {item["metric"] for item in result["active_metrics"]}
        empty = {item["metric"] for item in result["supported_empty_metrics"]}
        unsupported = {item["metric"] for item in result["unsupported_metrics"]}

        self.assertIn("spend", active)
        self.assertIn("video_play_actions", active)
        self.assertIn("complete_payment", empty)
        self.assertIn("app_install", empty)
        self.assertIn("voucher_spend", unsupported)
        self.assertGreater(result["request_stats"]["split_count"], 0)

    def test_tiktok_batch_profile_skips_high_risk_web_events(self) -> None:
        client = FakeTikTokMetricsClient()
        result = build_tiktok_metric_probe(
            client,
            advertiser_ids=["123"],
            start_date="2026-05-01",
            end_date="2026-05-09",
            profile="batch",
        )
        called_metrics = {metric for call in client.calls for metric in call}

        self.assertEqual(result["probe_profile"], "batch")
        self.assertIn("web_events", result["skipped_groups"])
        self.assertNotIn("add_to_cart", called_metrics)
        self.assertNotIn("complete_payment", called_metrics)

    def test_tiktok_metric_catalog_includes_new_supported_families(self) -> None:
        for metric in (
            "placement_type",
            "real_time_result",
            "cost_per_consideration",
            "live_views",
            "interactive_add_on_impressions",
            "website_total_find_location",
            "onsite_total_purchase_value",
            "offline_total_crm_events",
            "messaging_total_conversation_tiktok_direct_message",
            "onsite_shopping_roas",
            "vta_complete_payment_roas",
            "skan_result",
        ):
            self.assertIn(metric, TIKTOK_METRIC_MAP)

    def test_tiktok_core_coverage_treats_onsite_revenue_as_revenue(self) -> None:
        coverage = build_core_metric_coverage(platform="tiktok", totals={"onsite_total_purchase_value": "123.45"})

        self.assertEqual(coverage["status_by_metric"]["revenue"]["status"], "active")
        self.assertEqual(coverage["status_by_metric"]["revenue"]["evidence"][0]["metric"], "onsite_total_purchase_value")


class MetricPresetTests(unittest.TestCase):
    def test_recommendation_merges_probe_state_for_ecommerce(self) -> None:
        probe = {
            "platform": "meta",
            "active_metrics": [{"metric": "spend"}, {"metric": "purchase_roas"}],
            "supported_empty_metrics": [{"metric": "website_purchase_roas"}],
            "unsupported_metrics": [{"metric": "product_views"}],
        }
        result = recommend_metric_presets(platform="meta", user_type="电商", probe=probe)

        active = {item["metric"] for item in result["active_recommended_metrics"]["meta"]}
        empty = {item["metric"] for item in result["missing_or_empty_recommended_metrics"]["meta"]["supported_but_empty"]}
        unavailable = {item["metric"] for item in result["not_available"]["meta"]}
        self.assertIn("purchase_roas", active)
        self.assertIn("website_purchase_roas", empty)
        self.assertIn("product_views", unavailable)

    def test_w2a_recommendation_keeps_app_metrics(self) -> None:
        result = recommend_metric_presets(platform="all", user_type="工具", probe=None, w2a=True)

        self.assertIn("app_store_clicks", result["metrics_by_platform"]["meta"]["conversion"])
        self.assertIn("skan_app_install", result["metrics_by_platform"]["tiktok"]["conversion"])

    def test_tiktok_recommendation_prioritizes_ecommerce_onsite_metrics(self) -> None:
        result = recommend_metric_presets(platform="tiktok", user_type="电商")
        value_roas = result["metrics_by_platform"]["tiktok"]["value_roas"]

        self.assertEqual(
            value_roas[:4],
            [
                "onsite_purchases_roas",
                "onsite_shopping_roas",
                "shop_gross_revenue_by_order_submission",
                "onsite_total_purchase",
            ],
        )
        self.assertIn("onsite_total_checkout_initiation", value_roas)
        self.assertIn("onsite_total_add_to_cart", value_roas)

    def test_tiktok_recommendation_prioritizes_w2a_app_path_metrics(self) -> None:
        result = recommend_metric_presets(platform="tiktok", user_type="工具", w2a=True)
        conversion = result["metrics_by_platform"]["tiktok"]["conversion"]

        self.assertEqual(
            conversion[:4],
            [
                "onsite_destination_visits",
                "onsite_download_start",
                "real_time_app_install",
                "skan_app_install",
            ],
        )
        self.assertIn("onsite_form", result["metrics_by_platform"]["tiktok"]["vertical_specific"])

    def test_tiktok_recommendation_prioritizes_short_drama_retention_value(self) -> None:
        result = recommend_metric_presets(platform="tiktok", user_type="短剧")
        value_roas = result["metrics_by_platform"]["tiktok"]["value_roas"]
        creative_video = result["metrics_by_platform"]["tiktok"]["creative_video"]

        self.assertEqual(
            value_roas[:4],
            [
                "onsite_subscribe_value_day0",
                "onsite_subscribe_value_day1",
                "onsite_subscribe_value_day6",
                "onsite_total_subscribe_value",
            ],
        )
        self.assertEqual(
            creative_video[:3],
            [
                "paid_engaged_view_15s",
                "paid_engagement_engaged_view_15s",
                "engaged_view_15s",
            ],
        )

    def test_tiktok_recommendation_surfaces_live_and_messaging_for_social_verticals(self) -> None:
        entertainment = recommend_metric_presets(platform="tiktok", user_type="泛娱乐")
        social = recommend_metric_presets(platform="tiktok", user_type="社交")

        self.assertIn("live_views", entertainment["metrics_by_platform"]["tiktok"]["conversion"])
        self.assertIn(
            "messaging_total_conversation_tiktok_direct_message",
            entertainment["metrics_by_platform"]["tiktok"]["messaging"],
        )
        self.assertIn("live_views", social["metrics_by_platform"]["tiktok"]["conversion"])
        self.assertIn("engaged_view_15s", social["metrics_by_platform"]["tiktok"]["creative_video"])

    def test_tiktok_recommendation_prioritizes_game_and_finance_depth_events(self) -> None:
        casual = recommend_metric_presets(platform="tiktok", user_type="休闲游戏")
        midcore = recommend_metric_presets(platform="tiktok", user_type="中重度游戏")
        finance = recommend_metric_presets(platform="tiktok", user_type="金融借贷")

        self.assertEqual(
            casual["metrics_by_platform"]["tiktok"]["conversion"][:4],
            ["real_time_app_install", "skan_app_install", "app_install", "complete_tutorial"],
        )
        self.assertEqual(
            midcore["metrics_by_platform"]["tiktok"]["conversion"][:4],
            ["real_time_app_install", "skan_app_install", "app_install", "create_gamerole"],
        )
        self.assertEqual(
            finance["metrics_by_platform"]["tiktok"]["conversion"][:4],
            ["form", "onsite_form", "button_click", "messaging_total_conversation_tiktok_direct_message"],
        )

    def test_top_level_presets_command_is_exposed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "probe.json"
            path.write_text('{"platform":"meta","active_metrics":[{"metric":"spend"}]}')
            args = build_parser().parse_args(
                [
                    "metrics",
                    "presets",
                    "recommend",
                    "--platform",
                    "meta",
                    "--user-type",
                    "电商",
                    "--probe-file",
                    str(path),
                ]
            )

        self.assertEqual(args.user_type, "电商")


if __name__ == "__main__":
    unittest.main()
