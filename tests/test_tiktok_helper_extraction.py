"""Compatibility contracts for the TikTok helper/service extraction."""
import argparse
import ast
import inspect
import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import Mock, patch

from motata_cli.tiktok import commands, payloads
from motata_cli.tiktok.services import copy_payloads, discovery, normalization


MODULES = (payloads, copy_payloads, normalization, discovery)


class TikTokHelperExtractionTests(unittest.TestCase):
    def test_every_extracted_helper_retains_facade_signature(self):
        count = 0
        for module in MODULES:
            for name, function in inspect.getmembers(module, inspect.isfunction):
                if function.__module__ != module.__name__:
                    continue
                with self.subTest(helper=name):
                    signature = inspect.signature(function)
                    parameters = [p for p in signature.parameters.values() if p.name != "deps"]
                    self.assertEqual(
                        inspect.signature(getattr(commands, name)),
                        signature.replace(parameters=parameters),
                    )
                    count += 1
        self.assertEqual(count, 134)

    def test_services_do_not_import_facade(self):
        for module in MODULES:
            tree = ast.parse(inspect.getsource(module))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom):
                    self.assertNotIn("commands", (node.module or "").split("."))
                    self.assertNotIn("commands", [alias.name for alias in node.names])
                elif isinstance(node, ast.Import):
                    for alias in node.names:
                        self.assertNotIn("commands", alias.name.split("."))

    def test_payload_uses_current_facade_dependencies(self):
        args = argparse.Namespace(
            advertiser_id="123", name="new campaign", objective_type="TRAFFIC",
            budget=None, budget_mode=None, operation_status=None, campaign_type=None,
            sales_destination=None, optimization_goal=None, special_industries=None,
        )
        with patch.object(commands, "load_payload", return_value={"custom": True}) as load:
            with patch.object(commands, "generate_tiktok_request_id", return_value="patched-id"):
                result = commands.build_campaign_create_payload(args)
        load.assert_called_once_with(args)
        self.assertEqual(result, {
            "custom": True, "campaign_name": "new campaign", "objective_type": "TRAFFIC",
            "advertiser_id": "123", "request_id": "patched-id",
        })

    def test_copy_strategy_uses_patched_clock(self):
        fixed_time = datetime(2026, 1, 2, 3, 4, 5)
        clock = Mock(wraps=datetime)
        clock.now.return_value = fixed_time
        payload = {}
        with patch.object(commands, "datetime", clock):
            commands.normalize_copy_schedule_fields(payload)
        self.assertEqual(payload["schedule_start_time"], "2026-01-02 03:14:05")

    def test_nested_normalization_patch_is_preserved(self):
        event = {"event_code": "PURCHASE"}
        with patch.object(commands, "summarize_tiktok_pixel_event", return_value={"patched": True}) as summarize:
            result = commands.summarize_tiktok_pixel({"pixel_id": "p", "events": [event]})
        summarize.assert_called_once_with(event)
        self.assertEqual(result["events"], [{"patched": True}])

    def test_discovery_uses_current_summary_binding(self):
        portfolio = {"creative_portfolio_id": "42"}
        summary = {"creative_portfolio_id": "patched"}
        with patch.object(commands, "summarize_tiktok_creative_portfolio", return_value=summary) as summarize:
            result = commands.select_tiktok_creative_portfolios([portfolio])
        summarize.assert_called_once_with(portfolio)
        self.assertEqual(result["best_match"], summary)

    def test_helpers_accept_minimal_explicit_dependencies_without_facade(self):
        self.assertEqual(payloads.normalize_product_price_value("$12.50", deps=SimpleNamespace()), 12.5)
        self.assertEqual(normalization.dedupe_strings(["a", "a", "b"], deps=SimpleNamespace()), ["a", "b"])
        source = {"campaign_automation_type": "MANUAL"}
        resolve = Mock(return_value="SMART_PLUS")
        result = copy_payloads.is_smart_plus_campaign_type(
            source, deps=SimpleNamespace(get_campaign_automation_type=resolve),
        )
        self.assertTrue(result)
        resolve.assert_called_once_with(source)

    def test_validation_context_kwargs_remain_supported(self):
        result = commands.build_validation_result("creative", "123")
        commands.add_validation_error(result, "INVALID", "bad value", field="video_id")
        self.assertEqual(result["errors"][0]["context"]["field"], "video_id")


if __name__ == "__main__":
    unittest.main()
