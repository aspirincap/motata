from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

from motata_cli.common.security import network_error, redact


def build_headers(api_key: str, header_mode: str) -> dict[str, str]:
    headers = {"Accept": "application/json"}
    if header_mode == "bearer":
        headers["Authorization"] = f"Bearer {api_key}"
    else:
        headers["X-API-Key"] = api_key
    return headers


def build_path(args: argparse.Namespace) -> str:
    if args.mode == "agent":
        if not args.agent_id:
            raise ValueError("--agent-id is required when --mode agent")
        encoded = urllib.parse.quote(args.agent_id, safe=":")
        return f"/api/v1/openapi/tokens/{encoded}/access-token"

    if args.mode == "account":
        if not args.account_id:
            raise ValueError("--account-id is required when --mode account")
        encoded = urllib.parse.quote(args.account_id, safe="")
        return f"/api/v1/openapi/ad-accounts/{encoded}/token"

    if args.mode == "inventory":
        return "/api/v1/openapi/ad-accounts/access-tokens"

    if not args.channel:
        raise ValueError("--channel is required when --mode channel")
    return f"/api/v1/openapi/agents/channel/{args.channel}/valid-token"


def build_query(args: argparse.Namespace) -> str:
    if args.mode != "inventory":
        return ""

    params: dict[str, str] = {}
    if getattr(args, "channel", None):
        params["channel"] = args.channel
    if getattr(args, "account_status", None):
        params["account_status"] = args.account_status
    if getattr(args, "page", None) is not None:
        params["page"] = str(args.page)
    if getattr(args, "page_size", None) is not None:
        params["page_size"] = str(args.page_size)

    if not params:
        return ""
    return "?" + urllib.parse.urlencode(params)


def extract_access_token(payload: dict, mode: str) -> str | None:
    data = payload.get("data") or {}
    if mode == "agent":
        return data.get("access_token")
    if mode == "account":
        token = data.get("token") or {}
        return token.get("access_token")
    if mode == "inventory":
        return None
    token = data.get("token") or {}
    return token.get("access_token")


def _inventory_accounts(payload: dict) -> list[dict]:
    data = payload.get("data")
    if not isinstance(data, dict):
        raise RuntimeError("Auth Center returned invalid inventory data.")
    accounts = data.get("accounts")
    if not isinstance(accounts, list) or any(not isinstance(item, dict) for item in accounts):
        raise RuntimeError("Auth Center returned invalid inventory accounts.")
    return accounts


def extract_inventory_account(payload: dict, account_id: str) -> dict | None:
    accounts = _inventory_accounts(payload)
    for account in accounts:
        if str(account.get("account_id") or "") == str(account_id):
            return account
    return None


def format_inventory_compact(payload: dict) -> str:
    data = payload.get("data") or {}
    accounts = data.get("accounts") or []
    lines: list[str] = []
    for account in accounts:
        token = account.get("token") or {}
        access_token = str(token.get("access_token") or "").strip()
        if not access_token:
            continue
        channel = str(account.get("channel") or token.get("channel") or "").strip()
        account_name = str(account.get("account_name") or "").strip()
        account_id = str(account.get("account_id") or "").strip()
        lines.append("\t".join([channel, account_id, access_token, account_name]))
    return "\n".join(lines)


def request_payload(
    *,
    base_url: str,
    path: str,
    api_key: str,
    header_mode: str,
    query: str = "",
    timeout: int = 30,
) -> dict:
    url = base_url.rstrip("/") + path + query
    request = urllib.request.Request(url=url, headers=build_headers(api_key, header_mode), method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
        payload = json.loads(raw)
    except (OSError, ValueError) as exc:
        raise RuntimeError(network_error("Auth Center", exc)) from None
    if not isinstance(payload, dict) or payload.get("success") is False:
        raise RuntimeError("Auth Center returned an unsuccessful or invalid response.")
    return payload


def find_inventory_account(args: argparse.Namespace, payload: dict) -> dict | None:
    """Follow numeric pages; the local contract guarantees accounts, not metadata.

    Do not assume a short page is the last page (servers may cap page_size).
    Detect ignored pagination and cap requests rather than looping indefinitely.
    """
    seen: set[tuple[str, ...]] = set()
    page_args = argparse.Namespace(**vars(args))
    page_args.page = getattr(args, "page", None) or 1
    for _ in range(1000):
        account = extract_inventory_account(payload, args.account_id)
        if account is not None:
            return account
        accounts = _inventory_accounts(payload)
        if not accounts:
            return None
        signature = tuple(sorted(str(item.get("account_id")) for item in accounts))
        if signature in seen:
            raise RuntimeError("Inventory pagination repeated a page; account lookup is incomplete.")
        seen.add(signature)
        page_args.page += 1
        payload = request_payload(
            base_url=args.base_url, path=build_path(args), api_key=args.api_key,
            header_mode=args.header, query=build_query(page_args),
        )
    raise RuntimeError("Inventory page limit reached; account lookup is incomplete.")


def fetch_access_token(
    *,
    mode: str,
    base_url: str,
    api_key: str,
    header_mode: str = "x-api-key",
    account_id: str | None = None,
    channel: str | None = None,
    agent_id: str | None = None,
) -> tuple[str | None, dict]:
    args = argparse.Namespace(
        mode=mode,
        base_url=base_url,
        api_key=api_key,
        header=header_mode,
        account_id=account_id,
        channel=channel,
        agent_id=agent_id,
        account_status=None,
        page=1,
        page_size=20,
        compact=False,
        json=False,
    )
    path = build_path(args)
    payload = request_payload(
        base_url=base_url,
        path=path,
        api_key=api_key,
        header_mode=header_mode,
        query=build_query(args),
    )
    return extract_access_token(payload, mode), payload


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Fetch token from Auth Center OpenAPI")
    parser.add_argument("--mode", choices=["agent", "account", "inventory", "channel"], required=True)
    parser.add_argument("--agent-id", help="Format: channel:agent_id")
    parser.add_argument("--account-id", help="Ad account ID (required for account mode; optional for inventory filtering)")
    parser.add_argument("--account-status", help="Inventory filter for account_status")
    parser.add_argument("--channel", choices=["google", "meta", "facebook", "tiktok"])
    parser.add_argument("--page", type=int, default=1, help="Inventory page number")
    parser.add_argument("--page-size", type=int, default=20, help="Inventory page size")
    parser.add_argument("--compact", action="store_true", help="Inventory mode: print TSV rows for channel, account_id, access_token, account_name")
    parser.add_argument("--base-url", default=os.getenv("AUTH_CENTER_BASE_URL", "http://localhost:8000"))
    parser.add_argument("--api-key", default=os.getenv("AUTH_CENTER_API_KEY"))
    parser.add_argument("--header", choices=["x-api-key", "bearer"], default="x-api-key")
    parser.add_argument("--json", action="store_true", help="Print full JSON response")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    if not args.api_key:
        print("Missing API key. Set --api-key or AUTH_CENTER_API_KEY.", file=sys.stderr)
        return 2

    try:
        path = build_path(args)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    try:
        payload = request_payload(
            base_url=args.base_url,
            path=path,
            api_key=args.api_key,
            header_mode=args.header,
            query=build_query(args),
        )
    except (OSError, ValueError, RuntimeError) as exc:
        print(network_error("Auth Center", exc), file=sys.stderr)
        return 1

    if args.mode == "inventory":
        if args.account_id:
            try:
                account = find_inventory_account(args, payload)
            except (OSError, ValueError, RuntimeError) as exc:
                print(network_error("Auth Center inventory lookup", exc), file=sys.stderr)
                return 1
            if account is None:
                print(redact(f"No account found for account_id={args.account_id}.", (args.api_key,)), file=sys.stderr)
                return 1
            if args.json:
                print(json.dumps(account, indent=2, ensure_ascii=False))
                return 0
            token_data = account.get("token")
            token = token_data.get("access_token") if isinstance(token_data, dict) else None
            if token:
                print(token)
                return 0
            print("No access token found for the selected inventory account.", file=sys.stderr)
            return 1
        if args.compact:
            print(format_inventory_compact(payload))
            return 0
        if args.json:
            print(json.dumps(payload, indent=2, ensure_ascii=False))
            return 0
        print(json.dumps(payload, ensure_ascii=False))
        return 0

    if args.json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0

    token = extract_access_token(payload, args.mode)
    if token:
        print(token)
        return 0

    print("No access token found in response payload.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
