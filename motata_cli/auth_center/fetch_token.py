from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request


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


def extract_inventory_account(payload: dict, account_id: str) -> dict | None:
    data = payload.get("data") or {}
    accounts = data.get("accounts") or []
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
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = response.read().decode("utf-8")
    return json.loads(raw)


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
    except urllib.error.HTTPError as exc:
        error_text = exc.read().decode("utf-8", errors="replace")
        print(f"HTTP {exc.code}", file=sys.stderr)
        print(error_text, file=sys.stderr)
        return 1
    except urllib.error.URLError as exc:
        print(f"Request failed: {exc}", file=sys.stderr)
        return 1

    if args.mode == "inventory":
        if args.account_id:
            account = extract_inventory_account(payload, args.account_id)
            if account is None:
                print(f"No account found for account_id={args.account_id}.", file=sys.stderr)
                print(json.dumps(payload, indent=2, ensure_ascii=False), file=sys.stderr)
                return 1
            if args.json:
                print(json.dumps(account, indent=2, ensure_ascii=False))
                return 0
            token = (account.get("token") or {}).get("access_token")
            if token:
                print(token)
                return 0
            print(json.dumps(account, indent=2, ensure_ascii=False))
            return 0
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
    print(json.dumps(payload, indent=2, ensure_ascii=False), file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
