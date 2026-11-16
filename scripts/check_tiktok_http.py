#!/usr/bin/env python3
"""Offline coverage gate for the Direct HTTP migration and SDK removal."""
from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    from motata_cli.tiktok.client import TikTokClient
    from motata_cli.tiktok.endpoints import ENDPOINTS, endpoint_for
    manifest = json.loads((ROOT / "docs/tiktok-endpoint-matrix.json").read_text())
    assert len(manifest["public_methods"]) == 74
    assert len(manifest["sdk_operations"]) == 51
    assert all(callable(getattr(TikTokClient, name, None)) for name in manifest["public_methods"])
    for spec in manifest["sdk_operations"]:
        endpoint = ENDPOINTS[spec["endpoint_id"]]
        assert (endpoint.method, endpoint.path) == (spec["method"], spec["path"]), spec["endpoint_id"]
    for spec in manifest["existing_http"]:
        endpoint_for(spec["method"], spec["path"])
    for path in (ROOT / "motata_cli").rglob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                names = [alias.name for alias in node.names] + [getattr(node, "module", "") or ""]
                assert not any("business_api_client" in name or "tiktok_sdk" in name for name in names), str(path)
    assert not (ROOT / "motata_cli/tiktok/sdk.py").exists()
    assert not (ROOT / "motata_cli/transport/tiktok_sdk.py").exists()
    assert not (ROOT / "motata_cli/_vendor/tiktok_business_api_sdk").exists()
    client_source = (ROOT / "motata_cli/tiktok/client.py").read_text()
    assert "requests." not in client_source and "get_business_api_client" not in client_source
    with TikTokClient("offline-fixture"):
        pass
    assert not any(name == "business_api_client" or name.startswith("business_api_client.") for name in sys.modules)
    print(json.dumps({"status": "passed", "original_public_methods": 74, "original_sdk_operations": 51,
                      "registered_endpoints": len(ENDPOINTS), "sdk_runtime_dependency": False}))


if __name__ == "__main__":
    main()
