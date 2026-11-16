"""Installed capability inventory; never hot-load remote execution rules."""
from __future__ import annotations

from motata_cli.common.display import print_output
from motata_cli.tiktok.endpoints import API_VERSION, ENDPOINTS, SCHEMA_REVISION


def run(args):
    entries = [{"endpoint_id": name, "method": value.method, "path": value.path,
                "operation": "write" if value.write else "read", "implementation": "direct_http",
                "account_eligibility": "unknown"} for name, value in sorted(ENDPOINTS.items())]
    print_output({"platform": "tiktok", "api_version": API_VERSION, "schema_revision": SCHEMA_REVISION,
                  "auth_center_contract": "v0.3.9", "authentication": "api_key_token_delivery",
                  "transport": "direct", "sdk_dependency": False, "jwt_required": False,
                  "automatic_runtime_updates": False, "endpoint_count": len(entries), "endpoints": entries}, as_json=True)


def register(subparsers):
    root = subparsers.add_parser("capabilities", help="Installed API capabilities and transport contract")
    commands = root.add_subparsers(dest="capabilities_command", required=True)
    for name in ("show", "check"):
        parser = commands.add_parser(name)
        parser.add_argument("platform", choices=("tiktok",), nargs="?", default="tiktok")
        parser.set_defaults(func=run)
