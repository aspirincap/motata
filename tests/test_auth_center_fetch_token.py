from __future__ import annotations

import argparse
import unittest

from motata_cli.auth_center import fetch_token


class AuthCenterFetchTokenTests(unittest.TestCase):
    def test_build_path_for_account_mode(self) -> None:
        args = argparse.Namespace(mode="account", account_id="7444033053753835536", agent_id=None, channel=None)
        self.assertEqual(
            fetch_token.build_path(args),
            "/api/v1/openapi/ad-accounts/7444033053753835536/token",
        )

    def test_build_query_for_inventory_mode(self) -> None:
        args = argparse.Namespace(
            mode="inventory",
            channel="tiktok",
            account_status="ACTIVE",
            page=2,
            page_size=50,
        )
        self.assertEqual(
            fetch_token.build_query(args),
            "?channel=tiktok&account_status=ACTIVE&page=2&page_size=50",
        )

    def test_format_inventory_compact_skips_missing_tokens(self) -> None:
        payload = {
            "data": {
                "accounts": [
                    {
                        "channel": "tiktok",
                        "account_id": "1",
                        "account_name": "A",
                        "token": {"access_token": "tok-1"},
                    },
                    {
                        "channel": "meta",
                        "account_id": "2",
                        "account_name": "B",
                        "token": {"access_token": ""},
                    },
                ]
            }
        }
        self.assertEqual(fetch_token.format_inventory_compact(payload), "tiktok\t1\ttok-1\tA")


if __name__ == "__main__":
    unittest.main()
