from __future__ import annotations

import unittest

from motata_cli.__main__ import build_parser
from motata_cli.meta import commands as meta_commands
from motata_cli.meta.audience import build_meta_audience_breakdown
from motata_cli.tiktok import commands as tiktok_commands
from motata_cli.tiktok.audience import build_tiktok_audience_breakdown


class FakeMetaAudienceClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def paginate_insights(self, path: str, *, params: dict | None = None, prefer_async: bool = False, auto_async: bool = True):
        breakdowns = str((params or {}).get("breakdowns") or "")
        self.calls.append((path, breakdowns))
        if breakdowns == "publisher_platform,platform_position":
            raise RuntimeError("unsupported breakdown combination")
        if breakdowns == "country":
            return [
                self._row(country="VN", spend="100", impressions="1000", clicks="100", purchase="20"),
                self._row(country="TH", spend="50", impressions="2000", clicks="200", purchase="2"),
            ]
        if breakdowns == "age,gender":
            return [
                self._row(age="25-34", gender="female", spend="80", impressions="900", clicks="90", purchase="16"),
                self._row(age="18-24", gender="male", spend="40", impressions="1200", clicks="120", purchase="1"),
            ]
        if breakdowns == "publisher_platform":
            return [
                self._row(publisher_platform="facebook", spend="90", impressions="2000", clicks="80", purchase="12"),
                self._row(publisher_platform="audience_network", spend="60", impressions="1000", clicks="300", purchase="1"),
            ]
        if breakdowns == "device_platform":
            return [
                self._row(device_platform="mobile", spend="140", impressions="2800", clicks="250", purchase="22"),
            ]
        raise AssertionError(f"unexpected breakdowns: {breakdowns}")

    @staticmethod
    def _row(**kwargs):
        purchase = kwargs.pop("purchase")
        return {
            **kwargs,
            "spend": kwargs.pop("spend"),
            "impressions": kwargs.pop("impressions"),
            "clicks": kwargs.pop("clicks"),
            "actions": [{"action_type": "purchase", "value": purchase}],
        }


class FakeTikTokAudienceClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[str, ...]]] = []

    def integrated_report(self, report_type: str, **kwargs):
        dimensions = tuple(kwargs.get("dimensions") or [])
        self.calls.append((report_type, dimensions))
        if report_type != "AUDIENCE":
            raise AssertionError(f"unexpected report_type: {report_type}")
        if dimensions == ("advertiser_id", "country_code"):
            raise RuntimeError("dimension country_code unsupported")
        if dimensions == ("advertiser_id", "country"):
            rows = [
                self._row(dimensions, ["123", "VN"], spend="100", impressions="1000", clicks="100", conversion="25"),
                self._row(dimensions, ["123", "TH"], spend="50", impressions="2000", clicks="200", conversion="1"),
            ]
        elif dimensions == ("advertiser_id", "age", "gender"):
            rows = [
                self._row(dimensions, ["123", "25-34", "FEMALE"], spend="80", impressions="800", clicks="80", conversion="20"),
            ]
        elif dimensions == ("advertiser_id", "placement"):
            rows = [
                self._row(dimensions, ["123", "TikTok"], spend="90", impressions="900", clicks="60", conversion="18"),
                self._row(dimensions, ["123", "Pangle"], spend="45", impressions="1000", clicks="250", conversion="1"),
            ]
        elif dimensions == ("advertiser_id", "platform"):
            rows = [
                self._row(dimensions, ["123", "ANDROID"], spend="120", impressions="1500", clicks="140", conversion="30"),
            ]
        else:
            raise AssertionError(f"unexpected dimensions: {dimensions}")
        return {"data": {"list": rows}}

    @staticmethod
    def _row(dimensions: tuple[str, ...], values: list[str], **metrics):
        return {
            "dimensions": dict(zip(dimensions, values, strict=True)),
            "metrics": {
                "spend": metrics["spend"],
                "impressions": metrics["impressions"],
                "clicks": metrics["clicks"],
                "conversion": metrics["conversion"],
            },
        }


class AudienceBreakdownTests(unittest.TestCase):
    def test_meta_audience_breakdown_command_is_exposed(self) -> None:
        args = build_parser().parse_args(
            [
                "meta",
                "audience",
                "breakdown",
                "--account",
                "123",
                "--access-token",
                "demo-token",
                "--since",
                "2026-05-01",
                "--until",
                "2026-05-09",
                "--breakdown",
                "country",
            ]
        )

        self.assertIs(args.func, meta_commands.command_audience_breakdown)
        self.assertEqual(args.account_id, "123")
        self.assertEqual(args.breakdowns, ["country"])

    def test_tiktok_audience_breakdown_command_is_exposed(self) -> None:
        args = build_parser().parse_args(
            [
                "tiktok",
                "audience",
                "breakdown",
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

        self.assertIs(args.func, tiktok_commands.command_tiktok_audience_breakdown)
        self.assertEqual(args.advertiser_id, "123")

    def test_meta_breakdown_computes_segment_tags_and_uses_fallback(self) -> None:
        result = build_meta_audience_breakdown(
            FakeMetaAudienceClient(),
            account_id="123",
            since="2026-05-01",
            until="2026-05-09",
        )

        self.assertEqual(result["sections"]["country"]["status"], "ok")
        self.assertEqual(result["sections"]["placement"]["breakdowns"], ["publisher_platform"])
        self.assertGreater(result["request_stats"]["fallback_count"], 0)
        country_tags = {item["segment"]: set(item["tags"]) for item in result["sections"]["country"]["segments"]}
        self.assertIn("scale", country_tags["VN"])
        self.assertIn("weak_cvr", country_tags["TH"])
        self.assertTrue(result["summary"]["top_problem_segments"])

    def test_tiktok_breakdown_tries_dimension_fallbacks(self) -> None:
        client = FakeTikTokAudienceClient()
        result = build_tiktok_audience_breakdown(
            client,
            advertiser_id="123",
            start_date="2026-05-01",
            end_date="2026-05-09",
        )

        self.assertIn(("AUDIENCE", ("advertiser_id", "country_code")), client.calls)
        self.assertEqual(result["sections"]["country"]["dimensions"], ["advertiser_id", "country"])
        self.assertEqual(result["sections"]["age_gender"]["report_type"], "AUDIENCE")
        self.assertGreater(result["request_stats"]["fallback_count"], 0)
        placement_tags = {item["segment"]: set(item["tags"]) for item in result["sections"]["placement"]["segments"]}
        self.assertIn("cheap_click_trap", placement_tags["Pangle"])


if __name__ == "__main__":
    unittest.main()
