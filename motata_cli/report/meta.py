from __future__ import annotations

import argparse
import json
import time
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Callable

from motata_cli.init_profile import (
    load_init_profile,
    resolve_default_access_token,
    resolve_default_account,
    resolve_default_accounts,
    resolve_init_metrics,
)
from motata_cli.meta.app_discovery import build_meta_app_report
from motata_cli.meta.activities import build_meta_activities_report
from motata_cli.meta.audience import build_meta_audience_breakdown
from motata_cli.meta.client import MetaClient
from motata_cli.meta.commands import CliError, normalize_account_id
from motata_cli.meta.landing_pages import build_landing_page_report
from motata_cli.meta.output import print_output
from motata_cli.meta.services import get_entity, list_entities
from motata_cli.meta.user_type import build_user_type_report
from motata_cli.report.activity_factors import build_activity_factor_report, rank_activity_targets


INSIGHT_ATTRIBUTION = json.dumps(["7d_click", "1d_view"], separators=(",", ":"))

BASE_METRIC_FIELDS = [
    "spend",
    "impressions",
    "clicks",
    "ctr",
    "cpc",
    "cpm",
    "frequency",
    "reach",
    "actions",
    "action_values",
    "purchase_roas",
    "website_purchase_roas",
]

LEVEL_DIMENSION_FIELDS = {
    "account": ["account_id", "account_name"],
    "campaign": ["account_id", "account_name", "campaign_id", "campaign_name", "objective"],
    "adset": [
        "account_id",
        "account_name",
        "campaign_id",
        "campaign_name",
        "adset_id",
        "adset_name",
        "objective",
        "optimization_goal",
    ],
    "ad": [
        "account_id",
        "account_name",
        "campaign_id",
        "campaign_name",
        "adset_id",
        "adset_name",
        "ad_id",
        "ad_name",
        "objective",
        "optimization_goal",
    ],
}

ADSET_STRUCTURE_FIELDS = [
    "id",
    "name",
    "status",
    "effective_status",
    "campaign_id",
    "daily_budget",
    "lifetime_budget",
    "optimization_goal",
    "billing_event",
    "bid_strategy",
    "targeting",
    "promoted_object",
]

AD_STRUCTURE_FIELDS = [
    "id",
    "name",
    "status",
    "effective_status",
    "adset_id",
    "campaign_id",
    "creative{id,name,thumbnail_url,image_url,object_story_spec,asset_feed_spec,url_tags,link_url,object_url,template_url}",
]

CREATIVE_STRUCTURE_FIELDS = [
    "id",
    "name",
    "object_story_id",
    "effective_object_story_id",
    "object_story_spec",
    "asset_feed_spec",
    "image_url",
    "thumbnail_url",
    "video_id",
    "url_tags",
]


@dataclass(frozen=True)
class PeriodWindow:
    since: str
    until: str
    previous_since: str | None = None
    previous_until: str | None = None

    @property
    def has_previous(self) -> bool:
        return bool(self.previous_since and self.previous_until)


@dataclass(frozen=True)
class DepthPlan:
    depth: str
    insight_levels: tuple[str, ...]
    previous_levels: tuple[str, ...]
    audience_breakdowns: tuple[str, ...]
    landing_profile: str
    landing_limit: int
    app_profile: str
    user_type_profile: str
    structure_mode: str
    async_insights: bool
    include_apps: bool
    include_landing: bool
    include_previews: bool


def _iso(value: date) -> str:
    return value.isoformat()


def resolve_period(args: argparse.Namespace, today: date | None = None) -> PeriodWindow:
    today = today or date.today()
    if args.period == "custom":
        if not args.since or not args.until:
            raise ValueError("--period custom requires --since and --until")
        previous_since = args.previous_since
        previous_until = args.previous_until
        if args.compare and not (previous_since and previous_until):
            current_since = date.fromisoformat(args.since)
            current_until = date.fromisoformat(args.until)
            days = (current_until - current_since).days + 1
            if days > 0:
                previous_until_date = current_since - timedelta(days=1)
                previous_since_date = previous_until_date - timedelta(days=days - 1)
                previous_since = _iso(previous_since_date)
                previous_until = _iso(previous_until_date)
        return PeriodWindow(args.since, args.until, previous_since, previous_until)

    end = today - timedelta(days=1)
    if args.period == "daily":
        start = end
    else:
        start = end - timedelta(days=6)
    previous_until = start - timedelta(days=1)
    previous_since = previous_until - timedelta(days=(end - start).days)
    return PeriodWindow(
        _iso(start),
        _iso(end),
        _iso(previous_since) if args.compare else None,
        _iso(previous_until) if args.compare else None,
    )


def depth_plan(depth: str) -> DepthPlan:
    plans = {
        "fast": DepthPlan(
            depth="fast",
            insight_levels=("account", "campaign", "ad"),
            previous_levels=("account", "campaign"),
            audience_breakdowns=("country",),
            landing_profile="batch",
            landing_limit=1000,
            app_profile="batch",
            user_type_profile="batch",
            structure_mode="none",
            async_insights=False,
            include_apps=False,
            include_landing=True,
            include_previews=False,
        ),
        "standard": DepthPlan(
            depth="standard",
            insight_levels=("account", "campaign", "adset", "ad"),
            previous_levels=("account", "campaign", "adset", "ad"),
            audience_breakdowns=("country", "age_gender", "placement"),
            landing_profile="batch",
            landing_limit=3000,
            app_profile="batch",
            user_type_profile="batch",
            structure_mode="top",
            async_insights=True,
            include_apps=True,
            include_landing=True,
            include_previews=True,
        ),
        "full": DepthPlan(
            depth="full",
            insight_levels=("account", "campaign", "adset", "ad"),
            previous_levels=("account", "campaign", "adset", "ad"),
            audience_breakdowns=("country", "age_gender", "placement", "device"),
            landing_profile="full",
            landing_limit=5000,
            app_profile="full",
            user_type_profile="full",
            structure_mode="top",
            async_insights=True,
            include_apps=True,
            include_landing=True,
            include_previews=True,
        ),
        "deep": DepthPlan(
            depth="deep",
            insight_levels=("account", "campaign", "adset", "ad"),
            previous_levels=("account", "campaign", "adset", "ad"),
            audience_breakdowns=("country", "age_gender", "placement", "device"),
            landing_profile="full",
            landing_limit=5000,
            app_profile="full",
            user_type_profile="full",
            structure_mode="all",
            async_insights=True,
            include_apps=True,
            include_landing=True,
            include_previews=True,
        ),
    }
    return plans[depth]


def default_run_dir(account_id: str, period: str, depth: str, window: PeriodWindow) -> Path:
    clean_account = str(account_id).replace("act_", "")
    return Path("build") / "report_runs" / f"meta_{clean_account}_{period}_{depth}_{window.until}"


def _ad_account_path(account_id: str) -> str:
    account_id = str(account_id).strip()
    if account_id.startswith("act_"):
        return account_id
    return f"act_{account_id}"


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _payload_row_count(payload: Any) -> int | None:
    if isinstance(payload, list):
        return len(payload)
    if not isinstance(payload, dict):
        return None
    for key in ("data", "rows", "factors"):
        value = payload.get(key)
        if isinstance(value, list):
            return len(value)
    sections = payload.get("sections")
    if isinstance(sections, dict):
        total = 0
        for section in sections.values():
            if isinstance(section, dict):
                rows = section.get("segments") or section.get("rows")
                if isinstance(rows, list):
                    total += len(rows)
        return total
    return None


def _fnum(value: Any) -> float:
    if value is None:
        return 0.0
    try:
        return float(str(value).replace(",", "").replace("%", ""))
    except Exception:
        return 0.0


def _dedupe(values: list[str]) -> list[str]:
    return [value for value in dict.fromkeys(values) if value]


def _resolved_metric_fields(extra_metrics: list[str] | None = None) -> list[str]:
    return _dedupe([*BASE_METRIC_FIELDS, *(extra_metrics or [])])


def _insight_params(level: str, window: tuple[str, str], limit: int, *, metric_fields: list[str]) -> dict[str, Any]:
    fields = [*LEVEL_DIMENSION_FIELDS[level], *metric_fields]
    return {
        "level": level,
        "time_range": json.dumps({"since": window[0], "until": window[1]}, separators=(",", ":")),
        "fields": ",".join(fields),
        "action_report_time": "impression",
        "action_attribution_windows": INSIGHT_ATTRIBUTION,
        "use_unified_attribution_setting": "true",
        "limit": limit,
    }


def _pull_insights(
    meta: MetaClient,
    account_id: str,
    level: str,
    window: tuple[str, str],
    *,
    limit: int,
    async_report: bool,
    metric_fields: list[str],
) -> list[dict[str, Any]]:
    return meta.paginate_insights(
        f"{_ad_account_path(account_id)}/insights",
        params=_insight_params(level, window, limit, metric_fields=metric_fields),
        prefer_async=async_report,
        auto_async=True,
    )


def _pull_object_insights(
    meta: MetaClient,
    object_id: str,
    window: tuple[str, str],
    *,
    limit: int,
    async_report: bool,
    metric_fields: list[str],
) -> list[dict[str, Any]]:
    params = {
        "time_range": json.dumps({"since": window[0], "until": window[1]}, separators=(",", ":")),
        "fields": ",".join(metric_fields),
        "action_report_time": "impression",
        "action_attribution_windows": INSIGHT_ATTRIBUTION,
        "use_unified_attribution_setting": "true",
        "limit": limit,
    }
    return meta.paginate_insights(
        f"{object_id}/insights",
        params=params,
        prefer_async=async_report,
        auto_async=True,
    )


def _pull_object_daily_insights(
    meta: MetaClient,
    object_id: str,
    window: tuple[str, str],
    *,
    limit: int,
    async_report: bool,
    metric_fields: list[str],
) -> list[dict[str, Any]]:
    params = {
        "time_range": json.dumps({"since": window[0], "until": window[1]}, separators=(",", ":")),
        "fields": ",".join(["date_start", "date_stop", *metric_fields]),
        "time_increment": 1,
        "action_report_time": "impression",
        "action_attribution_windows": INSIGHT_ATTRIBUTION,
        "use_unified_attribution_setting": "true",
        "limit": limit,
    }
    return meta.paginate_insights(
        f"{object_id}/insights",
        params=params,
        prefer_async=async_report,
        auto_async=True,
    )


def _top_ids(rows: list[dict[str, Any]], key: str, limit: int) -> list[str]:
    ids: list[str] = []
    for row in sorted(rows, key=lambda item: _fnum(item.get("spend")), reverse=True):
        value = str(row.get(key) or "").strip()
        if value and value not in ids:
            ids.append(value)
        if len(ids) >= limit:
            break
    return ids


def _creative_id_from_ad(ad: dict[str, Any]) -> str | None:
    creative = ad.get("creative") or {}
    if isinstance(creative, dict) and creative.get("id"):
        return str(creative["id"])
    return None


def _parse_multi_values(raw: str | None) -> list[str]:
    if raw in (None, ""):
        return []
    values = [item.strip() for item in str(raw).replace("\n", ",").split(",") if item.strip()]
    return list(dict.fromkeys(values))


def _resolve_meta_report_account_ids(args: argparse.Namespace) -> tuple[list[str], str | None]:
    explicit = [normalize_account_id(value) for value in _parse_multi_values(getattr(args, "account_id", None))]
    if explicit:
        return explicit, "cli"
    if getattr(args, "all_init_accounts", False):
        account_ids, source = resolve_default_accounts("meta")
        if account_ids:
            return account_ids, source
        return [], source
    account_id, source = resolve_default_account("meta")
    return ([account_id] if account_id else []), source


def _clone_args(args: argparse.Namespace, **updates: Any) -> argparse.Namespace:
    values = vars(args).copy()
    values.update(updates)
    return argparse.Namespace(**values)


def _batch_run_dir(base_run_dir: str | None, account_id: str) -> str | None:
    if not base_run_dir:
        return None
    return str(Path(base_run_dir) / f"account_{account_id}")


def _prepare_meta_report_args(args: argparse.Namespace) -> argparse.Namespace:
    profile = load_init_profile("meta")
    account_ids, account_source = _resolve_meta_report_account_ids(args)
    if not account_ids:
        raise CliError("Missing Meta account. Provide --account-id, or run `motata init` first.")
    args.account_id = account_ids[0]
    args.account_ids = account_ids
    args.account_source = account_source

    if not getattr(args, "access_token", None):
        access_token, token_source = resolve_default_access_token("meta")
        if not access_token and getattr(args, "dry_run", False):
            args.access_token = None
            args.access_token_source = "not_required_dry_run"
        elif not access_token:
            raise CliError(
                "Missing Meta access token. Provide --access-token, or set META_ACCESS_TOKEN / MOTATA_META_ACCESS_TOKEN. "
                "`motata init` stores account defaults but does not persist tokens."
            )
        else:
            args.access_token = access_token
            args.access_token_source = token_source
    else:
        args.access_token_source = "cli"

    analysis_metrics = resolve_init_metrics("meta")
    args.analysis_metrics = analysis_metrics
    args.analysis_metrics_source = "init_profile" if analysis_metrics else "default_report_fields"
    args.init_profile = profile
    return args


class MetaReportRunner:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.window = resolve_period(args)
        self.plan = depth_plan(args.depth)
        self.analysis_metrics = _dedupe(list(getattr(args, "analysis_metrics", None) or []))
        self.metric_fields = _resolved_metric_fields(self.analysis_metrics)
        requested_include_previews = getattr(args, "include_previews", None)
        self.include_previews = self.plan.include_previews if requested_include_previews is None else bool(requested_include_previews)
        self.preview_fetch_limit = max(1, int(getattr(args, "top_objects", None) or 30)) if self.include_previews else 0
        self.run_dir = Path(args.run_dir) if args.run_dir else default_run_dir(args.account_id, args.period, args.depth, self.window)
        self.limit = args.limit or self.plan.landing_limit
        self.manifest: dict[str, Any] = {
            "platform": "meta",
            "account_id": args.account_id,
            "account_ids": list(getattr(args, "account_ids", [args.account_id])),
            "account_source": getattr(args, "account_source", "cli"),
            "period": args.period,
            "depth": args.depth,
            "window": {
                "since": self.window.since,
                "until": self.window.until,
                "previous_since": self.window.previous_since,
                "previous_until": self.window.previous_until,
            },
            "options": {
                "access_token_source": getattr(args, "access_token_source", "cli"),
                "include_previews": self.include_previews,
                "include_previews_source": "depth_default" if requested_include_previews is None else "cli",
                "preview_fetch_limit": self.preview_fetch_limit,
                "analysis_metrics": self.analysis_metrics,
                "analysis_metrics_source": getattr(args, "analysis_metrics_source", "default_report_fields"),
            },
            "init_profile": getattr(args, "init_profile", {}) or None,
            "run_dir": str(self.run_dir),
            "sources": [],
        }

    def source_names(self) -> list[str]:
        names = ["user_type", "activities", "activity_targeted_insights", "activity_daily_breakdown", "activity_factors"]
        if self.plan.include_apps:
            names.append("apps")
        names.extend([f"current_{level}_insights" for level in self.plan.insight_levels])
        if self.window.has_previous:
            names.extend([f"previous_{level}_insights" for level in self.plan.previous_levels])
        if self.plan.audience_breakdowns:
            names.append("audience_breakdown")
        if self.plan.include_landing:
            names.append("landing_pages")
        if self.plan.structure_mode != "none":
            names.extend(["adset_structure", "ad_structure", "creative_structure"])
        return names

    def dry_run_payload(self) -> dict[str, Any]:
        payload = dict(self.manifest)
        payload["sources"] = [{"name": name, "status": "planned"} for name in self.source_names()]
        return payload

    def record(self, name: str, status: str, path: str | None = None, **extra: Any) -> None:
        item = {"name": name, "status": status}
        if path:
            item["path"] = path
        item.update(extra)
        self.manifest["sources"].append(item)

    def run_source(self, name: str, fn: Callable[[], Any]) -> Any:
        path = self.run_dir / f"{name}.json"
        attempts = max(1, int(self.args.retry or 1))
        last_error = ""
        for attempt in range(1, attempts + 1):
            try:
                payload = fn()
                _write_json(path, payload)
                rows = _payload_row_count(payload)
                self.record(name, "ok", str(path), attempts=attempt, rows=rows)
                return payload
            except Exception as exc:
                last_error = str(exc)
                if attempt < attempts:
                    time.sleep(max(0.0, float(self.args.retry_wait or 0.0)))
        error_payload = {"error": last_error, "source": name, "status": "degraded"}
        _write_json(path, error_payload)
        self.record(name, "degraded", str(path), error=last_error, attempts=attempts)
        return error_payload

    def run(self) -> dict[str, Any]:
        from motata_cli.meta import commands as meta_commands

        if self.args.dry_run:
            return self.dry_run_payload()

        meta = meta_commands.build_meta(self.args)
        self.run_dir.mkdir(parents=True, exist_ok=True)

        self.run_source(
            "user_type",
            lambda: build_user_type_report(
                meta,
                account_id=self.args.account_id,
                since=self.window.since,
                until=self.window.until,
                date_preset=None,
                profile=self.plan.user_type_profile,
            ),
        )

        if self.plan.include_apps:
            self.run_source(
                "apps",
                lambda: build_meta_app_report(
                    meta,
                    account_id=self.args.account_id,
                    since=self.window.since,
                    until=self.window.until,
                    date_preset=None,
                    profile=self.plan.app_profile,
                ),
            )

        activities = self.run_source(
            "activities",
            lambda: build_meta_activities_report(
                meta,
                account_id=self.args.account_id,
                since=self.window.previous_since or self.window.since,
                until=self.window.until,
                limit=100,
                max_pages=1,
            ),
        )

        current_rows: dict[str, Any] = {}
        previous_rows: dict[str, Any] = {}
        for level in self.plan.insight_levels:
            rows = self.run_source(
                f"current_{level}_insights",
                lambda level=level: _pull_insights(
                    meta,
                    self.args.account_id,
                    level,
                    (self.window.since, self.window.until),
                    limit=self.limit,
                    async_report=self.plan.async_insights,
                    metric_fields=self.metric_fields,
                ),
            )
            if isinstance(rows, list):
                current_rows[level] = rows

        if self.window.has_previous:
            for level in self.plan.previous_levels:
                rows = self.run_source(
                    f"previous_{level}_insights",
                    lambda level=level: _pull_insights(
                    meta,
                    self.args.account_id,
                    level,
                    (self.window.previous_since or "", self.window.previous_until or ""),
                    limit=self.limit,
                    async_report=self.plan.async_insights,
                    metric_fields=self.metric_fields,
                ),
            )
                if isinstance(rows, list):
                    previous_rows[level] = rows

        if self.plan.audience_breakdowns:
            audience = self.run_source(
                "audience_breakdown",
                lambda: build_meta_audience_breakdown(
                    meta,
                    account_id=self.args.account_id,
                    since=self.window.since,
                    until=self.window.until,
                    date_preset=None,
                    breakdowns=list(self.plan.audience_breakdowns),
                    async_insights=self.plan.async_insights,
                ),
            )
            if isinstance(audience, dict):
                for breakdown, section in (audience.get("sections") or {}).items():
                    _write_json(self.run_dir / f"audience_{breakdown}.json", section)

        if self.plan.include_landing:
            self.run_source(
                "landing_pages",
                lambda: build_landing_page_report(
                    meta,
                    account_id=self.args.account_id,
                    since=self.window.since,
                    until=self.window.until,
                    date_preset=None,
                    profile=self.plan.landing_profile,
                    insight_limit=self.limit,
                    include_ads=True,
                    include_previews=self.include_previews,
                    max_ad_context_fetches=self.preview_fetch_limit,
                    enrich_product=bool(self.args.include_product),
                    async_insights=self.plan.async_insights,
                ),
            )

        if self.plan.structure_mode == "top":
            self._pull_top_structure(meta, current_rows)
        elif self.plan.structure_mode == "all":
            self._pull_all_structure(meta)

        targeted_insights = self.run_source(
            "activity_targeted_insights",
            lambda: self._pull_activity_targeted_insights(meta, activities),
        )
        activity_daily_breakdown = self.run_source(
            "activity_daily_breakdown",
            lambda: self._pull_activity_daily_breakdown(meta, activities),
        )
        self.run_source(
            "activity_factors",
            lambda: build_activity_factor_report(
                "meta",
                activities if isinstance(activities, dict) else {"rows": []},
                current_rows,
                previous_rows,
                targeted_insights=targeted_insights,
                daily_breakdown=activity_daily_breakdown,
                limit=8,
            ),
        )

        _write_json(self.run_dir / "manifest.json", self.manifest)
        return self.manifest

    def _pull_activity_targeted_insights(self, meta: MetaClient, activities: Any) -> dict[str, Any]:
        rows = (activities or {}).get("rows") if isinstance(activities, dict) else []
        targets = rank_activity_targets("meta", rows or [], limit=10)
        pulled: list[dict[str, Any]] = []
        errors: list[dict[str, str]] = []
        for target in targets:
            level = str(target.get("level") or "")
            object_id = str(target.get("object_id") or "").strip()
            if level not in {"campaign", "adset", "ad"} or not object_id:
                continue
            try:
                current_rows = _pull_object_insights(
                    meta,
                    object_id,
                    (self.window.since, self.window.until),
                    limit=25,
                    async_report=self.plan.async_insights,
                    metric_fields=self.metric_fields,
                )
                pulled.append({"level": level, "object_id": object_id, "current_rows": current_rows})
            except Exception as exc:
                errors.append({"level": level, "object_id": object_id, "error": str(exc)})
        return {
            "platform": "meta",
            "strategy": "activity_targeted_object_insights",
            "target_count": len(targets),
            "pulled_count": len(pulled),
            "rows": pulled,
            "errors": errors,
        }

    def _pull_activity_daily_breakdown(self, meta: MetaClient, activities: Any) -> dict[str, Any]:
        rows = (activities or {}).get("rows") if isinstance(activities, dict) else []
        targets = rank_activity_targets("meta", rows or [], limit=8)
        pulled: list[dict[str, Any]] = []
        errors: list[dict[str, str]] = []
        for target in targets:
            level = str(target.get("level") or "")
            object_id = str(target.get("object_id") or "").strip()
            if level not in {"campaign", "adset", "ad"} or not object_id:
                continue
            try:
                daily_rows = _pull_object_daily_insights(
                    meta,
                    object_id,
                    (self.window.previous_since or self.window.since, self.window.until),
                    limit=100,
                    async_report=self.plan.async_insights,
                    metric_fields=self.metric_fields,
                )
                pulled.append(
                    {
                        "level": level,
                        "object_id": object_id,
                        "strategy": "object_insights_time_increment_1",
                        "daily_rows": daily_rows,
                    }
                )
            except Exception as exc:
                errors.append({"level": level, "object_id": object_id, "error": str(exc)})
        return {
            "platform": "meta",
            "strategy": "activity_target_daily_breakdown",
            "date_range": {"since": self.window.previous_since or self.window.since, "until": self.window.until},
            "target_count": len(targets),
            "pulled_count": len(pulled),
            "rows": pulled,
            "errors": errors,
        }

    def _pull_top_structure(self, meta: MetaClient, current_rows: dict[str, Any]) -> None:
        top_n = max(1, int(self.args.top_objects or 30))
        adset_ids = _top_ids(current_rows.get("adset") or [], "adset_id", top_n)
        ad_ids = _top_ids(current_rows.get("ad") or [], "ad_id", top_n)

        adsets = self.run_source("adset_structure", lambda: [get_entity(meta, item, ADSET_STRUCTURE_FIELDS) for item in adset_ids])
        ads = self.run_source("ad_structure", lambda: [get_entity(meta, item, AD_STRUCTURE_FIELDS) for item in ad_ids])

        creative_ids: list[str] = []
        if isinstance(ads, list):
            for ad in ads:
                creative_id = _creative_id_from_ad(ad)
                if creative_id and creative_id not in creative_ids:
                    creative_ids.append(creative_id)
        self.run_source("creative_structure", lambda: [get_entity(meta, item, CREATIVE_STRUCTURE_FIELDS) for item in creative_ids])

    def _pull_all_structure(self, meta: MetaClient) -> None:
        account_path = _ad_account_path(self.args.account_id)
        self.run_source(
            "adset_structure",
            lambda: list_entities(meta, account_path, "adsets", fields=ADSET_STRUCTURE_FIELDS, limit=500, fetch_all=True),
        )
        self.run_source(
            "ad_structure",
            lambda: list_entities(meta, account_path, "ads", fields=AD_STRUCTURE_FIELDS, limit=500, fetch_all=True),
        )
        self.run_source(
            "creative_structure",
            lambda: list_entities(meta, account_path, "adcreatives", fields=CREATIVE_STRUCTURE_FIELDS, limit=500, fetch_all=True),
        )


def command_meta_report_run(args: argparse.Namespace) -> None:
    args = _prepare_meta_report_args(args)
    account_ids = list(getattr(args, "account_ids", [args.account_id]))
    if len(account_ids) == 1:
        runner = MetaReportRunner(args)
        print_output(runner.run(), as_json=True)
        return

    runs: list[dict[str, Any]] = []
    for account_id in account_ids:
        run_args = _clone_args(
            args,
            account_id=account_id,
            account_ids=[account_id],
            run_dir=_batch_run_dir(getattr(args, "run_dir", None), account_id),
        )
        runs.append(MetaReportRunner(run_args).run())

    result = {
        "platform": "meta",
        "mode": "batch",
        "account_ids": account_ids,
        "account_source": getattr(args, "account_source", "cli"),
        "run_count": len(runs),
        "runs": runs,
    }
    print_output(result, as_json=True)
