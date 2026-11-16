#!/usr/bin/env python3
"""Read-only official contract watch; unavailable portal shells are not changes."""
from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import sys
from pathlib import Path

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from motata_cli.common.http import ExplicitAuth

SOURCES = {
    "smart_plus_adgroup_create": "https://business-api.tiktok.com/portal/docs/create-an-upgraded-smart-ad-group/v1.3",
    "smart_plus_adgroup_update": "https://business-api.tiktok.com/portal/docs/update-an-upgraded-smart-ad-group/v1.3",
    "smart_plus_adgroup_get": "https://business-api.tiktok.com/portal/docs/get-upgraded-smart-ad-groups/v1.3",
    "drama_series": "https://business-api.tiktok.com/portal/docs/create-an-upgraded-smart-mini-series-feed-campaign/v1.3",
}


def extract_contract(raw):
    if raw.lstrip().startswith("{"):
        value = json.loads(raw)
        if not isinstance(value, dict) or not any(key in value for key in ("paths", "parameters", "properties")):
            raise ValueError("No structured API definition")
        return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2)
    soup = BeautifulSoup(raw, "html.parser")
    for element in soup(("script", "style", "nav", "footer")):
        element.decompose()
    text = "\n".join(line.strip() for line in soup.get_text("\n").splitlines() if line.strip())
    if len(text) < 160 or "advertiser_id" not in text:
        raise ValueError("Portal content unavailable: login page, application shell or missing API contract")
    return text


def watch(cache_dir, *, session=None):
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    own = session is None
    session = session if session is not None else requests.Session()
    results = []
    try:
        for name, url in SOURCES.items():
            try:
                with session.get(url, auth=ExplicitAuth(), timeout=(10, 30), allow_redirects=False, stream=True) as response:
                    if response.status_code != 200:
                        raise ValueError("Official documentation HTTP status " + str(response.status_code))
                    chunks, size = [], 0
                    for chunk in response.iter_content(65536):
                        size += len(chunk)
                        if size > 4 * 1024 * 1024:
                            raise ValueError("Official documentation exceeds response limit")
                        chunks.append(chunk)
                    contract = extract_contract(b"".join(chunks).decode("utf-8"))
                target = cache_dir / (name + ".txt")
                previous = target.read_text() if target.exists() else None
                state = "baseline_created" if previous is None else "unchanged" if previous == contract else "review_required"
                row = {"source": name, "url": url, "status": state,
                       "sha256": hashlib.sha256(contract.encode()).hexdigest()}
                if state == "review_required":
                    row["diff"] = "\n".join(difflib.unified_diff(previous.splitlines(), contract.splitlines(), fromfile="previous", tofile="current"))
                target.write_text(contract)
            except (requests.RequestException, ValueError, OSError):
                row = {"source": name, "url": url, "status": "source_unavailable",
                       "action": "Review the official portal manually; retain the last readable baseline."}
            results.append(row)
    finally:
        if own:
            session.close()
    return {"automatic_runtime_updates": False, "sources": results}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache-dir", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    result = watch(args.cache_dir)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({row["source"]: row["status"] for row in result["sources"]}))


if __name__ == "__main__":
    main()
