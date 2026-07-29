from __future__ import annotations
from typing import Any

from motata_cli.metrics.catalog import META_GROUPS, META_METRIC_MAP, MetricGroup
from motata_cli.metrics.core import build_core_metric_coverage
from motata_cli.metrics.probe import is_active_value, probe_grouped_metrics
from motata_cli.meta.landing_pages import (
    _ad_account_path,
    _fnum,
    _insights_params_base,
    _paginate_insights,
    _time_params,
    discover_recent_spend_accounts,
)


BASE_TRAFFIC_METRICS = ("spend", "impressions", "clicks")
META_PROBE_PROFILES: dict[str, tuple[str, ...]] = {
    "light": ("core",),
    "batch": ("core", "engagement", "actions", "results", "value_roas", "app", "video"),
    "vertical": ("core", "engagement", "actions", "results", "value_roas", "app", "video", "lead", "subscription"),
    "full": tuple(group.name for group in META_GROUPS),
}


def _account_id_from_row(account: dict[str, Any] | str) -> str:
    if isinstance(account, dict):
        return str(account.get("account_id") or account.get("id") or "").replace("act_", "")
    return str(account).replace("act_", "")


def _traffic_check(
    meta: Any,
    account_id: str,
    *,
    since: str | None,
    until: str | None,
    date_preset: str | None,
) -> dict[str, Any]:
    payload = meta.get(
        f"{_ad_account_path(account_id)}/insights",
        params={
            "level": "account",
            "fields": ",".join(BASE_TRAFFIC_METRICS),
            "limit": 1,
            **_time_params(since=since, until=until, date_preset=date_preset),
        },
    )
    row = (payload.get("data") or [{}])[0]
    return {
        "spend": _fnum(row.get("spend")),
        "impressions": _fnum(row.get("impressions")),
        "clicks": _fnum(row.get("clicks")),
        "has_traffic": any(is_active_value(row.get(metric)) for metric in BASE_TRAFFIC_METRICS),
    }


def build_meta_metric_probe(
    meta: Any,
    *,
    account_id: str | None = None,
    since: str | None = None,
    until: str | None = None,
    date_preset: str | None = "last_14d",
    account_limit: int | None = 10,
    insight_limit: int = 25,
    async_insights: bool = False,
    auto_async_insights: bool = True,
    group_names: list[str] | None = None,
    profile: str = "full",
) -> dict[str, Any]:
    discovery_errors: list[dict[str, Any]] = []
    fast_sampling = account_id is None
    if account_id:
        accounts = [{"account_id": account_id}]
    else:
        accounts, discovery_errors = discover_recent_spend_accounts(
            meta,
            since=since,
            until=until,
            date_preset=date_preset,
            account_limit=account_limit,
        )

    results: list[dict[str, Any]] = []
    for account in accounts:
        current_account_id = _account_id_from_row(account)
        traffic = _traffic_check(meta, current_account_id, since=since, until=until, date_preset=date_preset)
        request_stats_prefix = {"traffic_check_requests": 1}
        if not traffic["has_traffic"]:
            results.append(
                {
                    "platform": "meta",
                    "account_ids": [current_account_id],
                    "date_range": {"since": since, "until": until, "date_preset": date_preset},
                    "strategy": "grouped_batch",
                    "probe_profile": "custom" if group_names else profile,
                    "coverage": {"fast_recent_spend_sampling": fast_sampling, "full_account_coverage": bool(account_id)},
                    "traffic_check": traffic,
                    "core_metric_coverage": build_core_metric_coverage(platform="meta", totals=traffic),
                    "active_metrics": [],
                    "supported_empty_metrics": [],
                    "unsupported_metrics": [],
                    "request_stats": {**request_stats_prefix, "requests": 1, "successful_groups": 0, "split_count": 0, "fallback_count": 0, "failed_groups": 0},
                    "summary": {
                        "active_by_category": {},
                        "supported_empty_by_category": {},
                        "unsupported_by_category": {},
                        "notes": ["No non-zero traffic metrics found; skipped heavier metric groups."],
                    },
                }
            )
            continue

        def request_group(group: MetricGroup, metrics: tuple[str, ...]) -> list[dict[str, Any]]:
            fields = ["account_id", "account_name", *metrics]
            if group.required_level in {"campaign", "adset", "ad"}:
                fields.extend(["campaign_id", "campaign_name"])
            if group.required_level in {"adset", "ad"}:
                fields.extend(["adset_id", "adset_name"])
            if group.required_level == "ad":
                fields.extend(["ad_id", "ad_name"])
            params = {
                **_insights_params_base(attribution=True),
                **_time_params(since=since, until=until, date_preset=date_preset),
                "level": group.required_level or "campaign",
                "fields": ",".join(dict.fromkeys(fields)),
                "limit": insight_limit,
            }
            if any(metric in metrics for metric in ("actions", "action_values", "cost_per_action_type")):
                params["action_breakdowns"] = "action_type"
            if any(metric in metrics for metric in ("results", "cost_per_result", "result_rate", "objective_results")):
                params["summary"] = ",".join(metrics)
            return _paginate_insights(
                meta,
                f"{_ad_account_path(current_account_id)}/insights",
                params=params,
                prefer_async=async_insights,
                auto_async=auto_async_insights,
            )

        selected_group_names = set(group_names or META_PROBE_PROFILES.get(profile, META_PROBE_PROFILES["full"]))
        groups = [group for group in META_GROUPS if group.name in selected_group_names]
        skipped_groups = [group.name for group in META_GROUPS if group.name not in selected_group_names]
        probe = probe_grouped_metrics(
            platform="meta",
            groups=groups,
            specs=META_METRIC_MAP,
            request_fn=request_group,
        )
        probe["platform"] = "meta"
        probe["account_ids"] = [current_account_id]
        probe["date_range"] = {"since": since, "until": until, "date_preset": date_preset}
        probe["strategy"] = "grouped_batch"
        probe["probe_profile"] = "custom" if group_names else profile
        probe["skipped_groups"] = skipped_groups
        probe["coverage"] = {
            "fast_recent_spend_sampling": fast_sampling,
            "full_account_coverage": bool(account_id),
            "note": "Auto-discovery samples recent-spend accounts only." if fast_sampling else None,
        }
        probe["traffic_check"] = traffic
        probe["core_metric_coverage"] = build_core_metric_coverage(platform="meta", totals=traffic, probe=probe)
        probe["request_stats"] = {**probe["request_stats"], **request_stats_prefix, "requests": probe["request_stats"]["requests"] + 1}
        results.append(probe)

    if len(results) == 1:
        result = results[0]
        result["discovery_errors"] = discovery_errors
        return result
    return {
        "platform": "meta",
        "account_ids": [_account_id_from_row(account) for account in accounts],
        "date_range": {"since": since, "until": until, "date_preset": date_preset},
        "strategy": "grouped_batch",
        "probe_profile": "custom" if group_names else profile,
        "coverage": {"fast_recent_spend_sampling": fast_sampling, "full_account_coverage": bool(account_id)},
        "results": results,
        "discovery_errors": discovery_errors,
        "summary": {
            "account_count": len(results),
            "active_metric_count": sum(len(item.get("active_metrics") or []) for item in results),
            "unsupported_metric_count": sum(len(item.get("unsupported_metrics") or []) for item in results),
        },
    }
