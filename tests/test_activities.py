from __future__ import annotations

import unittest
from contextlib import redirect_stderr
import io
import json

from motata_cli.__main__ import build_parser
from motata_cli.meta import commands as meta_commands
from motata_cli.meta.activities import build_meta_activities_report
from motata_cli.tiktok import commands as tiktok_commands
from motata_cli.tiktok.activities import build_tiktok_activities_report


class FakeMetaActivitiesClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict, int | None]] = []

    def paginate(self, path: str, *, params: dict | None = None, max_pages: int | None = None):
        self.calls.append((path, params or {}, max_pages))
        return [
            {
                "event_type": "ad_update",
                "object_type": "ad",
                "actor_name": "Ada",
                "object_name": "Creative A",
            },
            {
                "event_type": "campaign_update",
                "object_type": "campaign",
                "actor_name": "Ada",
                "object_name": "Campaign A",
            },
        ]


class FakeTikTokActivitiesClient:
    def __init__(self) -> None:
        self.created_payloads: list[dict] = []
        self.checked: list[tuple[str, str]] = []

    def create_changelog_task(self, payload: dict):
        self.created_payloads.append(payload)
        return {"data": {"task_id": "task-1", "status": "PROCESSING"}}

    def check_changelog_task(self, advertiser_id: str, task_id: str):
        self.checked.append((advertiser_id, task_id))
        return {
            "data": {
                "task_id": task_id,
                "status": "SUCCESS",
                "list": [
                    {
                        "operation_type": "UPDATE",
                        "object_type": "ad",
                        "operator": "Grace",
                    }
                ],
            }
        }


class FakeEmptyTikTokActivitiesClient:
    def create_changelog_task(self, payload: dict):
        return {"data": {"task_id": "task-empty", "status": "PROCESSING"}}

    def check_changelog_task(self, advertiser_id: str, task_id: str):
        return {"data": {"task_id": task_id, "status": "SUCCESS"}}


class FakeDownloadTikTokActivitiesClient:
    def __init__(self) -> None:
        self.downloaded: list[tuple[str, str]] = []

    def create_changelog_task(self, payload: dict):
        return {"data": {"task_id": "task-download", "status": "PROCESSING"}}

    def check_changelog_task(self, advertiser_id: str, task_id: str):
        return {"data": {"task_id": task_id, "status": "SUCCESS"}}

    def download_changelog_task(self, advertiser_id: str, task_id: str):
        self.downloaded.append((advertiser_id, task_id))
        file_data = (
            "b'Budget changes,0\r\n"
            "Bid changes,0\r\n"
            "Status changes,1\r\n"
            "\r\n"
            "Time,log_object_type,Object ID,Object,Operator,Source,App ID,Activity details\r\n"
            "2026-05-13 07:50,Campaign,1864900642823170,On/Off Status,Ada,Marketing API,--,\"[]\"\r\n'"
        )
        return {
            "data": {
                "status": "SUCCESS",
                "changelog": json.dumps({"file_data": file_data, "file_name": "task-download.csv"}),
            }
        }


class ActivitiesTests(unittest.TestCase):
    def test_meta_activities_command_is_exposed(self) -> None:
        args = build_parser().parse_args(
            [
                "meta",
                "activities",
                "get",
                "--account-id",
                "123",
                "--access-token",
                "demo-token",
                "--since",
                "2026-05-01",
                "--until",
                "2026-05-02",
            ]
        )

        self.assertIs(args.func, meta_commands.command_activities_get)

    def test_tiktok_activities_command_is_exposed(self) -> None:
        args = build_parser().parse_args(
            [
                "tiktok",
                "activities",
                "get",
                "--advertiser-id",
                "123",
                "--access-token",
                "demo-token",
                "--since",
                "2026-05-01",
                "--until",
                "2026-05-02",
                "--module",
                "STATUS",
                "--object-type",
                "AD",
                "--operation-types",
                "STATUS,UPDATE",
            ]
        )

        self.assertIs(args.func, tiktok_commands.command_tiktok_activities_get)

    def test_meta_activities_uses_ad_account_activities_edge(self) -> None:
        client = FakeMetaActivitiesClient()
        result = build_meta_activities_report(
            client,
            account_id="123",
            since="2026-05-01",
            until="2026-05-02",
            limit=50,
            max_pages=2,
        )

        path, params, max_pages = client.calls[0]
        self.assertEqual(path, "act_123/activities")
        self.assertEqual(params["since"], "2026-05-01")
        self.assertEqual(params["until"], "2026-05-02")
        self.assertEqual(max_pages, 2)
        self.assertEqual(result["summary"]["activity_count"], 2)
        self.assertEqual(result["summary"]["top_actors"][0]["actor"], "Ada")

    def test_tiktok_activities_use_changelog_task_create_and_check(self) -> None:
        client = FakeTikTokActivitiesClient()
        with redirect_stderr(io.StringIO()):
            result = build_tiktok_activities_report(
                client,
                advertiser_id="123",
                start_date="2026-05-01",
                end_date="2026-05-02",
                module="STATUS",
                object_type="AD",
                operation_types=["STATUS,UPDATE"],
                wait=True,
                timeout_seconds=1,
                poll_seconds=0,
            )

        self.assertEqual(client.created_payloads[0]["advertiser_id"], "123")
        self.assertEqual(client.created_payloads[0]["start_time"], "2026-05-01 00:00:00")
        self.assertEqual(client.created_payloads[0]["end_time"], "2026-05-02 23:59:59")
        self.assertEqual(client.created_payloads[0]["module"], "STATUS")
        self.assertEqual(client.created_payloads[0]["object_type"], "AD")
        self.assertEqual(client.created_payloads[0]["operation_types"], ["STATUS", "UPDATE"])
        self.assertEqual(client.checked, [("123", "task-1")])
        self.assertEqual(result["task_id"], "task-1")
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["task_status"], "SUCCESS")
        self.assertEqual(result["summary"]["activity_count"], 1)

    def test_tiktok_activities_success_with_empty_rows_is_not_pending(self) -> None:
        client = FakeEmptyTikTokActivitiesClient()
        with redirect_stderr(io.StringIO()):
            result = build_tiktok_activities_report(
                client,
                advertiser_id="123",
                start_date="2026-05-01",
                end_date="2026-05-02",
                wait=True,
                timeout_seconds=1,
                poll_seconds=0,
            )

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["task_status"], "SUCCESS")
        self.assertEqual(result["summary"]["activity_count"], 0)

    def test_tiktok_activities_downloads_successful_changelog_csv(self) -> None:
        client = FakeDownloadTikTokActivitiesClient()
        with redirect_stderr(io.StringIO()):
            result = build_tiktok_activities_report(
                client,
                advertiser_id="123",
                start_date="2026-05-01",
                end_date="2026-05-02",
                wait=True,
                timeout_seconds=1,
                poll_seconds=0,
            )

        self.assertEqual(client.downloaded, [("123", "task-download")])
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["summary"]["activity_count"], 1)
        self.assertEqual(result["summary"]["top_object_types"][0], {"object_type": "Campaign", "count": 1})
        self.assertEqual(result["summary"]["top_operation_types"][0], {"operation_type": "On/Off Status", "count": 1})
        self.assertEqual(result["rows"][0]["Object ID"], "1864900642823170")


if __name__ == "__main__":
    unittest.main()
