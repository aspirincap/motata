from __future__ import annotations

import os
from typing import Any

from motata_cli.meta.commands import load_config, normalize_account_id


PLATFORM_TOKEN_ENV_VARS = {
    "meta": ("META_ACCESS_TOKEN", "MOTATA_META_ACCESS_TOKEN"),
    "tiktok": ("TIKTOK_ACCESS_TOKEN", "MOTATA_TIKTOK_ACCESS_TOKEN"),
}


def _env_first(*names: str) -> str | None:
    for name in names:
        value = os.environ.get(name)
        if value not in (None, ""):
            return value
    return None


def load_init_profile(platform: str) -> dict[str, Any]:
    config = load_config()
    profiles = config.get("init_profiles") or {}
    profile = profiles.get(platform)
    return profile if isinstance(profile, dict) else {}


def _parse_multi_values(raw: Any) -> list[str]:
    if raw in (None, ""):
        return []
    values = [item.strip() for item in str(raw).replace("\n", ",").split(",") if item.strip()]
    return list(dict.fromkeys(values))


def resolve_default_account(platform: str) -> tuple[str | None, str | None]:
    config = load_config()
    values = config.get("values") or {}
    profile = load_init_profile(platform)

    if platform == "meta":
        value = values.get("default_account") or profile.get("account_id")
        if value:
            return normalize_account_id(str(value)), "init_config"
        return None, None

    if platform == "tiktok":
        value = values.get("default_tiktok_account") or profile.get("account_id")
        if value:
            return str(value).strip(), "init_config"
        return None, None

    return None, None


def resolve_default_accounts(platform: str) -> tuple[list[str], str | None]:
    profile = load_init_profile(platform)
    raw_values = profile.get("account_ids")
    values = raw_values if isinstance(raw_values, list) else [profile.get("account_id")] if profile.get("account_id") else []
    parsed = _parse_multi_values(",".join(str(value) for value in values if value))
    if platform == "meta":
        parsed = [normalize_account_id(value) for value in parsed]
    if parsed:
        return parsed, "init_profile_accounts"

    account, source = resolve_default_account(platform)
    return ([account] if account else []), source


def resolve_default_access_token(platform: str) -> tuple[str | None, str | None]:
    env_vars = PLATFORM_TOKEN_ENV_VARS.get(platform, ())
    value = _env_first(*env_vars)
    if value:
        return value, "environment"
    return None, None


def resolve_init_metrics(platform: str) -> list[str]:
    profile = load_init_profile(platform)
    metrics = profile.get("metrics")
    if not isinstance(metrics, list):
        return []
    return [str(metric).strip() for metric in metrics if str(metric).strip()]
