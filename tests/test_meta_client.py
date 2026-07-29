from __future__ import annotations

import io
import unittest
from contextlib import redirect_stderr
from unittest.mock import patch

from motata_cli.meta.client import MetaClient


class FakeResponse:
    def __init__(self, payload: dict):
        self.payload = payload

    def json(self) -> dict:
        return self.payload

    def raise_for_status(self) -> None:
        return None


class MetaClientAsyncInsightsTests(unittest.TestCase):
    def test_async_insights_polling_does_not_request_report_url(self) -> None:
        calls: list[tuple[str, str, dict | None]] = []

        def fake_post(url: str, *, data=None, files=None, timeout=None):
            calls.append(("POST", url, dict(data or {})))
            return FakeResponse({"report_run_id": "123456789"})

        def fake_get(url: str, *, params=None, timeout=None):
            params = dict(params or {})
            calls.append(("GET", url, params))
            if url.endswith("/123456789"):
                self.assertEqual(params.get("fields"), "async_status,async_percent_completion")
                self.assertNotIn("async_report_url", params.get("fields") or "")
                return FakeResponse(
                    {
                        "id": "123456789",
                        "async_status": "Job Completed",
                        "async_percent_completion": 100,
                    }
                )
            if url.endswith("/123456789/insights"):
                return FakeResponse({"data": [{"spend": "1.23"}]})
            raise AssertionError(f"unexpected GET {url}")

        client = MetaClient("token", version="v23.0", base_url="https://graph.test/v23.0")
        with patch("motata_cli.meta.client.requests.post", side_effect=fake_post), patch(
            "motata_cli.meta.client.requests.get", side_effect=fake_get
        ), redirect_stderr(io.StringIO()):
            rows = client.paginate_insights(
                "act_123/insights",
                params={"fields": "spend,actions", "limit": 3000},
                prefer_async=True,
            )

        self.assertEqual(rows, [{"spend": "1.23"}])
        requested_fields = [
            str((params or {}).get("fields") or "")
            for method, _url, params in calls
            if method == "GET"
        ]
        self.assertNotIn("async_report_url", ",".join(requested_fields))
        self.assertEqual(
            [url.rsplit("/", 1)[-1] for method, url, _params in calls if method == "GET"],
            ["123456789", "insights"],
        )

    def test_async_insights_wait_emits_progress_to_stderr(self) -> None:
        client = MetaClient("token", version="v23.0", base_url="https://graph.test/v23.0")
        payloads = [
            {"id": "123456789", "async_status": "Job Started", "async_percent_completion": 0},
            {"id": "123456789", "async_status": "Job Completed", "async_percent_completion": 100},
        ]

        def fake_get(path: str, *, params=None):
            self.assertEqual(path, "123456789")
            self.assertEqual((params or {}).get("fields"), "async_status,async_percent_completion")
            return payloads.pop(0)

        stderr = io.StringIO()
        with patch.object(client, "get", side_effect=fake_get), redirect_stderr(stderr):
            client._wait_insights_report("123456789", poll_seconds=0)

        output = stderr.getvalue()
        self.assertIn("async insights polling report_run_id=123456789", output)
        self.assertIn("status=Job Started", output)
        self.assertIn("async insights completed report_run_id=123456789", output)
        self.assertNotIn("async_report_url", output)


if __name__ == "__main__":
    unittest.main()
