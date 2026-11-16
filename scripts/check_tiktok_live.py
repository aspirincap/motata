#!/usr/bin/env python3
"""Explicit, read-only live smoke test. Never saves a platform credential."""
from __future__ import annotations

import argparse
import getpass
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from motata_cli.tiktok.client import TikTokClient


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--account-id", required=True)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    token = os.getenv("TIKTOK_ACCESS_TOKEN") or getpass.getpass("TikTok test token (hidden): ")
    results = []
    with TikTokClient(token) as client:
        identities = []
        def read_identities():
            result = client.list_identities(args.account_id, page=1, page_size=5)
            data = result.get("data") or {}
            identities.extend(data.get("identity_list") or data.get("list") or data.get("identities") or [])
            return result
        yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).date().isoformat()
        checks = [
            ("account", lambda: client.get_account_info([args.account_id])),
            ("campaigns", lambda: client.list_campaigns(args.account_id, page=1, page_size=1)),
            ("smartplus_campaigns", lambda: client.list_campaigns(args.account_id, page=1, page_size=1, smart_plus=True)),
            ("smartplus_adgroups", lambda: client.list_adgroups(args.account_id, page=1, page_size=1, smart_plus=True)),
            ("identities", read_identities),
            ("integrated_report", lambda: client.integrated_report("BASIC", advertiser_id=args.account_id,
                data_level="AUCTION_ADVERTISER", dimensions=["advertiser_id"], metrics=["spend"],
                start_date=yesterday, end_date=yesterday, page=1, page_size=1)),
        ]
        for name, call in checks:
            try:
                result = call()
                data = result.get("data") or {}
                rows = (data.get("list") or data.get("identity_list") or data.get("identities") or []) if isinstance(data, dict) else []
                entry = {"check": name, "status": "passed", "code": result.get("code"),
                         "request_id": result.get("request_id"), "rows_on_page": len(rows)}
            except Exception as exc:
                entry = {"check": name, "status": "failed", "error": str(exc)}
            results.append(entry)
            print(json.dumps(entry, ensure_ascii=False), flush=True)
        identity = next((row for row in identities if row.get("identity_type") in ("TT_USER", "BC_AUTH_TT")), None)
        if identity:
            try:
                result = client.list_native_series(args.account_id, identity_id=str(identity["identity_id"]),
                    identity_type=identity["identity_type"], identity_authorized_bc_id=identity.get("identity_authorized_bc_id"), page=1, page_size=20)
                data = result.get("data") or {}
                rows = data.get("native_series_list") or data.get("list") or []
                entry = {"check": "native_series", "status": "passed", "code": result.get("code"),
                         "request_id": result.get("request_id"), "rows_on_page": len(rows)}
            except Exception as exc:
                entry = {"check": "native_series", "status": "failed", "error": str(exc)}
        else:
            entry = {"check": "native_series", "status": "not_verified", "reason": "No eligible TT_USER/BC_AUTH_TT identity in the sampled page"}
        results.append(entry)
        print(json.dumps(entry, ensure_ascii=False), flush=True)
    evidence = {"date": datetime.now(timezone.utc).isoformat(), "account_id": args.account_id,
                "credential_source": "user_supplied_platform_token", "writes_performed": False, "checks": results}
    if args.out:
        args.out.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n")
    return 0 if all(row["status"] == "passed" for row in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
