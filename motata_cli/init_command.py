from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass
from typing import Any

from motata_cli.auth_center.fetch_token import fetch_access_token
from motata_cli.meta.client import MetaClient
from motata_cli.common.config import CACHE_DIR, load_config, save_config
from motata_cli.common.errors import CliError
from motata_cli.common.utils import normalize_account_id, env_first
from motata_cli.meta.utils import ad_account_path
from motata_cli.common.display import print_output
from motata_cli.meta.user_type import build_user_type_report
from motata_cli.metrics.presets import recommend_metric_presets
from motata_cli.tiktok.app_discovery import discover_operable_advertiser_ids
from motata_cli.tiktok.client import TikTokClient
from motata_cli.tiktok.user_type import build_tiktok_user_type_report


META_VERSION = env_first("MOTATA_META_VERSION", default="v23.0")
AUTH_CENTER_ENV_VARS = ("AUTH_CENTER_API_KEY",)

MESSAGES = {
    "zh": {
        "platform_prompt": "请选择平台",
        "token_source_prompt": "请选择 token 来源",
        "token_source_help": "支持 direct / auth-center",
        "choice_invalid": "请选择以下选项之一: {choices}",
        "input_interrupted": "交互输入已中断。",
        "missing_platform": "缺少平台。请提供 --platform meta 或 --platform tiktok。",
        "missing_token_source": "缺少 token 来源。请提供 --token-source direct 或 --token-source auth-center。",
        "direct_token_prompt": "请输入 {label} access token（或先放入 {env_hint}，然后回车继续）: ",
        "missing_direct_token": "{label} token 是必填项。请直接传 --access-token，或先设置环境变量: {env_hint}。",
        "api_key_prompt": "请输入 Auth Center API key（或先放入 {env_hint}，然后回车继续）: ",
        "missing_api_key": "Auth Center API key 是必填项。请直接传 --auth-center-api-key，或先设置 AUTH_CENTER_API_KEY。",
        "auth_center_token_missing": "Auth Center 没有返回可用的 {label} token。请检查 API key、channel 映射和租户 token 状态。",
        "no_accounts": "当前 token 未发现可用的{label}。请显式提供 {flag}，或检查 token 是否具备账户访问权限。",
        "multiple_accounts_noninteractive": "发现多个{label}，请显式提供 {flag}（可逗号分隔多个）。",
        "discovered_accounts": "发现 {count} 个可选{label}，请输入序号；支持逗号分隔多选，首个会作为默认账户：",
        "index_invalid": "请输入有效的数字序号，可用逗号分隔多个，例如 1,3,5。",
        "index_out_of_range": "序号超出范围，请重新输入。",
        "metric_prompt": "如需自定义指标，请输入逗号分隔的 metric；直接回车则使用推荐指标。",
        "metric_example": "推荐示例: {hint}",
        "progress_user_type": "正在运行 user type 分类，获取推荐指标...",
        "setup_complete": "配置完成，推荐下一步：查询日报表",
        "setup_complete_with_command": "配置完成，推荐下一步：查询日报表\n{command}",
        "report_proposal": "配置完成，推荐下一步：查询日报表。",
        "multi_scope_note": "多账户模式下，推荐指标会基于你本次选中的全部账户联合判断；首个账户仍会作为默认账户写入配置。",
    },
    "en": {
        "platform_prompt": "Choose platform",
        "token_source_prompt": "Choose token source",
        "token_source_help": "Supported: direct / auth-center",
        "choice_invalid": "Please choose one of: {choices}",
        "input_interrupted": "Interactive input was interrupted.",
        "missing_platform": "Missing platform. Provide --platform meta or --platform tiktok.",
        "missing_token_source": "Missing token source. Provide --token-source direct or --token-source auth-center.",
        "direct_token_prompt": "Enter the {label} access token (or set {env_hint} first, then press Enter): ",
        "missing_direct_token": "{label} token is required. Provide --access-token directly, or set one of: {env_hint}.",
        "api_key_prompt": "Enter the Auth Center API key (or set {env_hint} first, then press Enter): ",
        "missing_api_key": "Auth Center API key is required. Provide --auth-center-api-key directly, or set AUTH_CENTER_API_KEY.",
        "auth_center_token_missing": "Auth Center did not return a usable {label} token. Check the API key, channel mapping, and tenant token status.",
        "no_accounts": "No usable {label} were discovered from the token. Provide {flag} explicitly, or check whether the token has account access.",
        "multiple_accounts_noninteractive": "Discovered multiple {label}. Provide {flag} explicitly; comma-separated values are supported.",
        "discovered_accounts": "Discovered {count} available {label}. Enter one or more indexes; comma-separated multi-select is supported. The first selection becomes the default account:",
        "index_invalid": "Enter valid numeric indexes. You can separate multiple selections with commas, for example: 1,3,5.",
        "index_out_of_range": "One or more indexes are out of range. Please try again.",
        "metric_prompt": "To override metrics, enter a comma-separated metric list. Press Enter to use the recommended set.",
        "metric_example": "Example recommended metrics: {hint}",
        "progress_user_type": "Running user-type classification to get recommended metrics...",
        "setup_complete": "Setup complete. Recommended next step: run the daily report.",
        "setup_complete_with_command": "Setup complete. Recommended next step: run the daily report.\n{command}",
        "report_proposal": "Setup complete. Recommended next step: run the daily report.",
        "multi_scope_note": "In multi-account mode, recommended metrics are determined from the full set of selected accounts. The first selected account is still saved as the default account.",
    },
}


@dataclass(frozen=True)
class PlatformSettings:
    name: str
    label: str
    account_label: str
    account_flag: str
    token_env_vars: tuple[str, ...]
    auth_center_channel: str
    report_group: str


@dataclass(frozen=True)
class InitUi:
    lang: str

    def text(self, key: str, **kwargs: Any) -> str:
        template = MESSAGES[self.lang][key]
        return template.format(**kwargs)


PLATFORMS: dict[str, PlatformSettings] = {
    "meta": PlatformSettings(
        name="meta",
        label="Meta",
        account_label="广告账户",
        account_flag="--account-id",
        token_env_vars=("META_ACCESS_TOKEN", "MOTATA_META_ACCESS_TOKEN"),
        auth_center_channel="meta",
        report_group="meta",
    ),
    "tiktok": PlatformSettings(
        name="tiktok",
        label="TikTok",
        account_label="广告账户",
        account_flag="--advertiser-id",
        token_env_vars=("TIKTOK_ACCESS_TOKEN", "MOTATA_TIKTOK_ACCESS_TOKEN"),
        auth_center_channel="tiktok",
        report_group="tiktok",
    ),
}


def env_first(*names: str) -> str | None:
    for name in names:
        value = os.environ.get(name)
        if value not in (None, ""):
            return value
    return None


def is_interactive(args: argparse.Namespace) -> bool:
    return not getattr(args, "yes", False) and sys.stdin.isatty() and sys.stdout.isatty()


def resolve_ui(args: argparse.Namespace) -> InitUi:
    requested = str(getattr(args, "lang", "auto") or "auto").lower()
    if requested not in {"auto", "zh", "en"}:
        requested = "auto"
    if requested == "auto":
        locale = (os.getenv("LC_ALL") or os.getenv("LANG") or "").lower()
        requested = "zh" if "zh" in locale or "cn" in locale else "en"
    return InitUi(lang=requested)


def prompt_text(message: str, *, allow_empty: bool = False, ui: InitUi | None = None) -> str:
    while True:
        try:
            value = input(message).strip()
        except EOFError as exc:  # pragma: no cover - interactive shell edge case
            raise CliError((ui or InitUi("en")).text("input_interrupted")) from exc
        if value or allow_empty:
            return value


def prompt_choice(message: str, choices: tuple[str, ...], *, ui: InitUi) -> str:
    choice_hint = "/".join(choices)
    while True:
        value = prompt_text(f"{message} [{choice_hint}]: ", ui=ui).lower()
        if value in choices:
            return value
        print(ui.text("choice_invalid", choices=", ".join(choices)), file=sys.stderr)


def parse_multi_values(raw: str | None) -> list[str]:
    if not raw:
        return []
    values = [
        item.strip()
        for chunk in str(raw).replace("\n", ",").split(",")
        for item in [chunk]
        if item.strip()
    ]
    return list(dict.fromkeys(values))


def normalize_platform(value: str | None) -> str | None:
    if not value:
        return None
    lowered = value.strip().lower()
    if lowered in {"facebook", "fb", "meta"}:
        return "meta"
    if lowered in {"tt", "tik", "tiktok"}:
        return "tiktok"
    return lowered


def resolve_platform(args: argparse.Namespace, ui: InitUi) -> PlatformSettings:
    platform = normalize_platform(getattr(args, "platform", None))
    if not platform and is_interactive(args):
        platform = prompt_choice(ui.text("platform_prompt"), ("meta", "tiktok"), ui=ui)
    if platform not in PLATFORMS:
        raise CliError(ui.text("missing_platform"))
    return PLATFORMS[platform]


def resolve_token_source(args: argparse.Namespace, ui: InitUi) -> str:
    token_source = getattr(args, "token_source", None)
    if not token_source and is_interactive(args):
        token_source = prompt_choice(ui.text("token_source_prompt"), ("direct", "auth-center"), ui=ui)
    if token_source not in {"direct", "auth-center"}:
        raise CliError(ui.text("missing_token_source"))
    return token_source


def resolve_direct_token(args: argparse.Namespace, settings: PlatformSettings, ui: InitUi) -> tuple[str, dict[str, Any]]:
    access_token = getattr(args, "access_token", None) or env_first(*settings.token_env_vars)
    if not access_token and is_interactive(args):
        env_hint = " / ".join(settings.token_env_vars)
        access_token = prompt_text(
            ui.text("direct_token_prompt", label=settings.label, env_hint=env_hint),
            allow_empty=True,
            ui=ui,
        )
        if not access_token:
            access_token = env_first(*settings.token_env_vars)
    if not access_token:
        env_hint = ", ".join(settings.token_env_vars)
        raise CliError(ui.text("missing_direct_token", label=settings.label, env_hint=env_hint))
    return access_token, {"source": "direct", "env_checked": list(settings.token_env_vars)}


def resolve_auth_center_api_key(args: argparse.Namespace, ui: InitUi) -> str:
    api_key = getattr(args, "auth_center_api_key", None) or env_first(*AUTH_CENTER_ENV_VARS)
    if not api_key and is_interactive(args):
        api_key = prompt_text(
            ui.text("api_key_prompt", env_hint=" / ".join(AUTH_CENTER_ENV_VARS)),
            allow_empty=True,
            ui=ui,
        )
        if not api_key:
            api_key = env_first(*AUTH_CENTER_ENV_VARS)
    if not api_key:
        raise CliError(ui.text("missing_api_key"))
    return api_key


def fetch_auth_center_token_for_init(
    *,
    settings: PlatformSettings,
    account_id: str | None,
    api_key: str,
    base_url: str,
    header_mode: str,
    ui: InitUi,
) -> tuple[str, dict[str, Any]]:
    attempts: list[dict[str, Any]] = []
    if account_id:
        token, payload = fetch_access_token(
            mode="account",
            base_url=base_url,
            api_key=api_key,
            header_mode=header_mode,
            account_id=account_id,
        )
        attempts.append({"mode": "account", "account_id": account_id, "token_found": bool(token)})
        if token:
            return token, {"source": "auth-center", "mode": "account", "attempts": attempts, "response_ok": bool(payload)}
    token, payload = fetch_access_token(
        mode="channel",
        base_url=base_url,
        api_key=api_key,
        header_mode=header_mode,
        channel=settings.auth_center_channel,
    )
    attempts.append({"mode": "channel", "channel": settings.auth_center_channel, "token_found": bool(token)})
    if token:
        return token, {"source": "auth-center", "mode": "channel", "attempts": attempts, "response_ok": bool(payload)}
    raise CliError(ui.text("auth_center_token_missing", label=settings.label))


def resolve_access_token(
    args: argparse.Namespace,
    settings: PlatformSettings,
    token_source: str,
    account_id: str | None,
    ui: InitUi,
) -> tuple[str, dict[str, Any]]:
    if token_source == "direct":
        return resolve_direct_token(args, settings, ui)

    api_key = resolve_auth_center_api_key(args, ui)
    access_token, metadata = fetch_auth_center_token_for_init(
        settings=settings,
        account_id=account_id,
        api_key=api_key,
        base_url=getattr(args, "auth_center_base_url", None) or os.getenv("AUTH_CENTER_BASE_URL", "http://localhost:8000"),
        header_mode=getattr(args, "auth_center_header", "x-api-key"),
        ui=ui,
    )
    metadata["api_key_env_checked"] = list(AUTH_CENTER_ENV_VARS)
    metadata["base_url"] = getattr(args, "auth_center_base_url", None) or os.getenv("AUTH_CENTER_BASE_URL", "http://localhost:8000")
    return access_token, metadata


def discover_meta_accounts(access_token: str, *, limit: int = 25) -> list[dict[str, Any]]:
    meta = MetaClient(access_token, version=META_VERSION, error_factory=CliError)
    rows = meta.get(
        "me/adaccounts",
        params={
            "fields": "id,name,account_status,currency,timezone_name,business",
            "limit": limit,
        },
    ).get("data") or []
    return [
        {
            "id": normalize_account_id(row.get("id")),
            "name": row.get("name"),
            "status": row.get("account_status"),
            "currency": row.get("currency"),
            "timezone": row.get("timezone_name"),
            "raw": row,
        }
        for row in rows
        if row.get("id")
    ]


def discover_tiktok_accounts(access_token: str, *, limit: int = 25) -> list[dict[str, Any]]:
    client = TikTokClient(access_token)
    rows = discover_operable_advertiser_ids(client, advertiser_limit=limit)
    return [
        {
            "id": str(row.get("advertiser_id") or "").strip(),
            "name": row.get("advertiser_name"),
            "status": row.get("advertiser_account_type"),
            "currency": row.get("currency"),
            "timezone": row.get("timezone"),
            "raw": row,
        }
        for row in rows
        if row.get("advertiser_id")
    ]


def discover_accounts(settings: PlatformSettings, access_token: str, *, limit: int = 25) -> list[dict[str, Any]]:
    if settings.name == "meta":
        return discover_meta_accounts(access_token, limit=limit)
    return discover_tiktok_accounts(access_token, limit=limit)


def normalize_selected_accounts(settings: PlatformSettings, raw: str | None) -> list[str]:
    values = parse_multi_values(raw)
    if settings.name == "meta":
        return [normalize_account_id(value) for value in values]
    return [str(value).strip() for value in values if str(value).strip()]


def pick_rows_by_ids(rows: list[dict[str, Any]], selected_ids: list[str]) -> list[dict[str, Any]]:
    by_id = {str(row.get("id") or ""): row for row in rows}
    return [by_id[item] for item in selected_ids if item in by_id]


def parse_selected_indexes(raw: str, count: int) -> list[int] | None:
    values = parse_multi_values(raw)
    if not values:
        return None
    parsed: list[int] = []
    for value in values:
        if not value.isdigit():
            return None
        index = int(value)
        if index < 1 or index > count:
            return []
        if index not in parsed:
            parsed.append(index)
    return parsed


def choose_account(
    args: argparse.Namespace,
    settings: PlatformSettings,
    access_token: str,
) -> tuple[list[str], list[dict[str, Any]], list[dict[str, Any]]]:
    ui = resolve_ui(args)
    provided = normalize_selected_accounts(settings, getattr(args, "account_id", None))
    if provided:
        discovered = discover_accounts(settings, access_token, limit=getattr(args, "account_limit", 25))
        selected_rows = pick_rows_by_ids(discovered, provided)
        return provided, selected_rows, discovered

    discovered = discover_accounts(settings, access_token, limit=getattr(args, "account_limit", 25))
    if not discovered:
        raise CliError(ui.text("no_accounts", label=settings.account_label, flag=settings.account_flag))
    if len(discovered) == 1:
        return [str(discovered[0]["id"])], [discovered[0]], discovered
    if not is_interactive(args):
        raise CliError(ui.text("multiple_accounts_noninteractive", label=settings.account_label, flag=settings.account_flag))

    print(ui.text("discovered_accounts", count=len(discovered), label=settings.account_label), file=sys.stderr)
    for index, row in enumerate(discovered, start=1):
        print(
            f"  {index}. {row.get('id')}  {row.get('name') or ''}".rstrip(),
            file=sys.stderr,
        )
    while True:
        selected_index = prompt_text("> ", ui=ui)
        parsed = parse_selected_indexes(selected_index, len(discovered))
        if parsed is None:
            print(ui.text("index_invalid"), file=sys.stderr)
            continue
        if parsed == []:
            print(ui.text("index_out_of_range"), file=sys.stderr)
            continue
        rows = [discovered[index - 1] for index in parsed]
        ids = [str(row["id"]) for row in rows]
        return ids, rows, discovered


def aggregate_user_type_reports(reports: list[dict[str, Any]]) -> dict[str, Any]:
    score_by_type: dict[str, float] = {}
    account_rows: list[dict[str, Any]] = []
    campaigns: list[dict[str, Any]] = []
    scraped_content: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    for report in reports:
        for item in report.get("all_types") or []:
            label = str(item.get("type") or "").strip()
            if not label:
                continue
            score_by_type[label] = score_by_type.get(label, 0.0) + float(item.get("raw_score") or 0.0)
        account_rows.extend(report.get("accounts") or [])
        campaigns.extend(report.get("campaigns") or [])
        scraped_content.extend(report.get("scraped_content") or [])
        errors.extend(report.get("errors") or [])

    max_score = max(score_by_type.values()) if score_by_type else 0.0
    all_types = [
        {
            "type": label,
            "raw_score": round(score, 2),
            "index": round(score / max_score * 100, 1) if max_score > 0 else 0.0,
        }
        for label, score in sorted(score_by_type.items(), key=lambda item: item[1], reverse=True)
    ]
    return {
        "top_types": all_types[:3],
        "all_types": all_types,
        "accounts": account_rows,
        "campaigns": campaigns,
        "scraped_content": scraped_content,
        "errors": errors,
        "account_count": len(account_rows),
        "campaign_count": len(campaigns),
        "scraped_content_count": len(scraped_content),
        "account_reports": reports,
    }


def classify_user_type_for_init(
    *,
    settings: PlatformSettings,
    access_token: str,
    account_ids: list[str],
) -> tuple[dict[str, Any], dict[str, Any]]:
    primary_account_id = account_ids[0]
    if settings.name == "meta":
        client = MetaClient(access_token, version=META_VERSION, error_factory=CliError)
        reports = [
            build_user_type_report(
                client,
                account_id=account_id,
                date_preset="last_14d",
                account_limit=1,
                campaign_limit=6,
                ad_limit=3,
                content_limit=20,
                profile="batch",
                include_evidence=False,
            )
            for account_id in account_ids
        ]
        return (
            aggregate_user_type_reports(reports),
            {
                "mode": "selected_accounts",
                "primary_account_id": primary_account_id,
                "selected_account_ids": account_ids,
            },
        )

    client = TikTokClient(access_token)
    return (
        build_tiktok_user_type_report(
            client,
            advertiser_ids=account_ids,
            advertiser_limit=max(1, len(account_ids)),
            campaign_limit=6,
            ad_limit=3,
            content_limit=20,
            page_size=200,
            max_pages=5,
            include_evidence=False,
        ),
        {
            "mode": "selected_accounts",
            "primary_account_id": primary_account_id,
            "selected_account_ids": account_ids,
        },
    )


def flatten_metric_buckets(metric_buckets: dict[str, list[str]]) -> list[str]:
    ordered: list[str] = []
    for metrics in metric_buckets.values():
        for metric in metrics:
            if metric not in ordered:
                ordered.append(metric)
    return ordered


def prompt_metric_overrides(recommended: list[str], *, ui: InitUi) -> list[str] | None:
    hint = ", ".join(recommended[:12])
    raw = prompt_text(
        f"{ui.text('metric_prompt')}\n{ui.text('metric_example', hint=hint)}\n> ",
        allow_empty=True,
        ui=ui,
    )
    if not raw:
        return None
    metrics = [item.strip() for item in raw.replace("\n", ",").split(",") if item.strip()]
    return metrics or None


def resolve_metrics(
    args: argparse.Namespace,
    settings: PlatformSettings,
    user_type: str,
    ui: InitUi,
) -> tuple[list[str], dict[str, Any]]:
    preset = recommend_metric_presets(platform=settings.name, user_type=user_type)
    metrics_by_bucket = (preset.get("metrics_by_platform") or {}).get(settings.name) or {}
    recommended = flatten_metric_buckets(metrics_by_bucket)
    selected = [str(metric).strip() for metric in (getattr(args, "metrics", None) or []) if str(metric).strip()]
    if not selected and is_interactive(args):
        selected = prompt_metric_overrides(recommended, ui=ui) or []
    final_metrics = selected or recommended
    metadata = {
        "user_type": user_type,
        "preset_name": preset.get("preset_name"),
        "metrics_by_bucket": metrics_by_bucket,
        "recommended_metrics": recommended,
        "selected_metrics": final_metrics,
        "customized": bool(selected),
        "analysis_notes": preset.get("analysis_notes") or [],
    }
    return final_metrics, metadata


def build_daily_report_command(settings: PlatformSettings, account_id: str) -> str:
    if settings.name == "meta":
        return (
            "motata report meta run "
            f"--account-id {account_id} "
            "--period daily "
            "--depth standard "
            f"--access-token ${settings.token_env_vars[0]}"
        )
    return (
        "motata report tiktok run "
        f"--advertiser-id {account_id} "
        "--period daily "
        "--depth standard "
        f"--access-token ${settings.token_env_vars[0]}"
    )


def persist_init_summary(
    *,
    settings: PlatformSettings,
    account_ids: list[str],
    account_rows: list[dict[str, Any]],
    token_source: str,
    user_type_result: dict[str, Any],
    user_type_scope: dict[str, Any],
    metrics: list[str],
    metrics_metadata: dict[str, Any],
    report_command: str,
) -> str:
    account_id = account_ids[0]
    account_row = account_rows[0] if account_rows else None
    config = load_config()
    values = config.setdefault("values", {})
    values["default_platform"] = settings.name
    if settings.name == "meta":
        values["default_account"] = normalize_account_id(account_id)
    else:
        values["default_tiktok_account"] = account_id

    init_profiles = config.setdefault("init_profiles", {})
    init_profiles[settings.name] = {
        "platform": settings.name,
        "account_id": account_id,
        "account_name": (account_row or {}).get("name"),
        "account_ids": account_ids,
        "accounts": [
            {"id": str(row.get("id") or ""), "name": row.get("name"), "status": row.get("status")}
            for row in account_rows
            if row.get("id")
        ],
        "token_source": token_source,
        "user_type": (user_type_result.get("top_types") or [{}])[0].get("type"),
        "top_types": user_type_result.get("top_types") or [],
        "user_type_scope": user_type_scope,
        "metrics": metrics,
        "metric_preset": metrics_metadata.get("preset_name"),
        "suggested_daily_report": report_command,
    }
    save_config(config)
    return str(CACHE_DIR / "config.json")


def command_init(args: argparse.Namespace) -> None:
    ui = resolve_ui(args)
    settings = resolve_platform(args, ui)
    token_source = resolve_token_source(args, ui)
    hinted_accounts = normalize_selected_accounts(settings, getattr(args, "account_id", None))
    hinted_account = hinted_accounts[0] if hinted_accounts else None
    access_token, token_metadata = resolve_access_token(args, settings, token_source, hinted_account, ui)

    account_ids, account_rows, discovered_accounts = choose_account(args, settings, access_token)
    if is_interactive(args):
        print(ui.text("progress_user_type"), file=sys.stderr)
    user_type_payload = classify_user_type_for_init(
        settings=settings,
        access_token=access_token,
        account_ids=account_ids,
    )
    if isinstance(user_type_payload, tuple):
        user_type_result, user_type_scope = user_type_payload
    else:
        user_type_result = user_type_payload
        user_type_scope = {
            "mode": "primary_account_only",
            "primary_account_id": account_ids[0],
            "selected_account_ids": account_ids,
        }
    top_types = user_type_result.get("top_types") or []
    top_user_type = getattr(args, "user_type", None) or (top_types[0].get("type") if top_types else None) or "代理商/多类型"
    metrics, metric_metadata = resolve_metrics(args, settings, top_user_type, ui)
    primary_account_id = account_ids[0]
    primary_account_row = account_rows[0] if account_rows else None
    report_command = build_daily_report_command(settings, primary_account_id)
    config_path = persist_init_summary(
        settings=settings,
        account_ids=account_ids,
        account_rows=account_rows,
        token_source=token_source,
        user_type_result=user_type_result,
        user_type_scope=user_type_scope,
        metrics=metrics,
        metrics_metadata=metric_metadata,
        report_command=report_command,
    )

    completion_banner = ui.text("setup_complete")
    scope_note = None
    if len(account_ids) > 1:
        scope_note = ui.text("multi_scope_note")
    if is_interactive(args):
        print(ui.text("setup_complete_with_command", command=report_command), file=sys.stderr)
        if scope_note:
            print(scope_note, file=sys.stderr)

    result = {
        "status": "initialized",
        "message": completion_banner,
        "platform": settings.name,
        "token": {
            "status": "ready",
            "source": token_source,
            "details": token_metadata,
        },
        "account": {
            "id": primary_account_id,
            "name": (primary_account_row or {}).get("name"),
            "ids": account_ids,
            "rows": [
                {"id": str(row.get("id") or ""), "name": row.get("name"), "status": row.get("status")}
                for row in account_rows
                if row.get("id")
            ],
            "selection": "provided" if hinted_accounts else "discovered",
            "discovered_count": len(discovered_accounts),
        },
        "user_type": {
            "selected": top_user_type,
            "top_types": top_types,
            "scope": user_type_scope,
            "note": scope_note,
        },
        "metrics": {
            "selected": metrics,
            "customized": metric_metadata.get("customized", False),
            "recommended_by_bucket": metric_metadata.get("metrics_by_bucket") or {},
            "analysis_notes": metric_metadata.get("analysis_notes") or [],
        },
        "next_steps": {
            "suggested_daily_report": report_command,
            "proposal": ui.text("report_proposal"),
        },
        "config_path": config_path,
    }
    print_output(result, as_json=True)


def register_init_command(subparsers) -> None:
    parser = subparsers.add_parser("init", help="Initialize token source, account scope, user type, and recommended metrics.")
    parser.add_argument("--platform", choices=["meta", "tiktok"])
    parser.add_argument("--token-source", choices=["direct", "auth-center"])
    parser.add_argument("--lang", choices=["auto", "zh", "en"], default="auto", help="Interactive language for prompts and progress text.")
    parser.add_argument("--access-token")
    parser.add_argument("--auth-center-api-key", default=os.getenv("AUTH_CENTER_API_KEY"))
    parser.add_argument("--auth-center-base-url", default=os.getenv("AUTH_CENTER_BASE_URL", "http://localhost:8000"))
    parser.add_argument("--auth-center-header", choices=["x-api-key", "bearer"], default="x-api-key")
    parser.add_argument(
        "--account-id",
        "--account",
        "--advertiser-id",
        dest="account_id",
        help="One account ID, or multiple IDs separated by commas. In interactive mode you can also multi-select from the discovered list.",
    )
    parser.add_argument("--account-limit", type=int, default=25, help="Maximum accounts to discover when the account is omitted.")
    parser.add_argument("--user-type", help="Override the auto-classified user type when selecting analysis metrics.")
    parser.add_argument("--metric", dest="metrics", action="append", help="Repeat to pin custom analysis metrics.")
    parser.add_argument("--yes", action="store_true", help="Disable interactive prompts; fail fast when required input is missing.")
    parser.add_argument("--output", choices=["json", "plain", "table"], help="Override output format for init results.")
    parser.set_defaults(func=command_init)
