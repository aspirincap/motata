from __future__ import annotations

from typing import Any


def fnum(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def is_nonzero(value: Any) -> bool:
    return fnum(value) != 0


def safe_div(numerator: float, denominator: float) -> float | None:
    if denominator == 0:
        return None
    return numerator / denominator


def round_metric(value: float | None, digits: int = 4) -> float | None:
    if value is None:
        return None
    return round(value, digits)


def normalize_segment_value(value: Any) -> str:
    text = str(value or "").strip()
    return text if text else "unknown"


def compute_segment_metrics(rows: list[dict[str, Any]], *, top: int | None = None) -> dict[str, Any]:
    totals = {
        "spend": sum(fnum(row.get("spend")) for row in rows),
        "impressions": sum(fnum(row.get("impressions")) for row in rows),
        "clicks": sum(fnum(row.get("clicks")) for row in rows),
        "result": sum(fnum(row.get("result")) for row in rows),
        "value": sum(fnum(row.get("value")) for row in rows),
    }
    account_cpa = safe_div(totals["spend"], totals["result"])
    account_cvr = safe_div(totals["result"], totals["clicks"])

    annotated: list[dict[str, Any]] = []
    for row in rows:
        spend = fnum(row.get("spend"))
        impressions = fnum(row.get("impressions"))
        clicks = fnum(row.get("clicks"))
        result = fnum(row.get("result"))
        value = fnum(row.get("value"))
        ctr = safe_div(clicks, impressions)
        cpc = safe_div(spend, clicks)
        cpm = safe_div(spend * 1000, impressions)
        cpa = safe_div(spend, result)
        cvr = safe_div(result, clicks)
        spend_share = safe_div(spend, totals["spend"])
        result_share = safe_div(result, totals["result"])
        cpa_index = safe_div(cpa or 0, account_cpa or 0) if cpa is not None and account_cpa else None
        cvr_index = safe_div(cvr or 0, account_cvr or 0) if cvr is not None and account_cvr else None

        tags = diagnose_segment(
            spend_share=spend_share,
            result=result,
            cpa_index=cpa_index,
            cvr_index=cvr_index,
            ctr=ctr,
            cpc=cpc,
            account_cpc=safe_div(totals["spend"], totals["clicks"]),
        )
        annotated.append(
            {
                **row,
                "spend": round_metric(spend, 2),
                "impressions": int(impressions),
                "clicks": int(clicks),
                "result": round_metric(result, 2),
                "value": round_metric(value, 2),
                "ctr": round_metric((ctr or 0) * 100, 4),
                "cpc": round_metric(cpc, 4),
                "cpm": round_metric(cpm, 4),
                "cpa": round_metric(cpa, 4),
                "cvr": round_metric((cvr or 0) * 100, 4),
                "spend_share": round_metric((spend_share or 0) * 100, 4),
                "result_share": round_metric((result_share or 0) * 100, 4),
                "cpa_index": round_metric(cpa_index, 4),
                "cvr_index": round_metric(cvr_index, 4),
                "tags": tags,
            }
        )

    annotated.sort(key=lambda item: fnum(item.get("spend")), reverse=True)
    if top:
        annotated = annotated[: max(top, 1)]

    return {
        "totals": {
            "spend": round_metric(totals["spend"], 2),
            "impressions": int(totals["impressions"]),
            "clicks": int(totals["clicks"]),
            "result": round_metric(totals["result"], 2),
            "value": round_metric(totals["value"], 2),
            "cpa": round_metric(account_cpa, 4),
            "cvr": round_metric((account_cvr or 0) * 100, 4),
        },
        "segments": annotated,
    }


def diagnose_segment(
    *,
    spend_share: float | None,
    result: float,
    cpa_index: float | None,
    cvr_index: float | None,
    ctr: float | None,
    cpc: float | None,
    account_cpc: float | None,
) -> list[str]:
    tags: list[str] = []
    high_spend = (spend_share or 0) >= 0.15
    medium_spend = (spend_share or 0) >= 0.05
    if result == 0 and medium_spend:
        tags.append("no_result_spend")
    if cpa_index is not None:
        if high_spend and cpa_index >= 1.3:
            tags.append("reduce")
        elif medium_spend and cpa_index <= 0.8:
            tags.append("scale")
        elif medium_spend and cpa_index <= 1.05:
            tags.append("monitor")
    if cvr_index is not None and cvr_index < 0.7 and medium_spend:
        tags.append("weak_cvr")
    if (
        ctr is not None
        and cpc is not None
        and account_cpc is not None
        and ctr >= 0.02
        and cpc <= account_cpc * 0.7
        and (cvr_index is None or cvr_index < 0.7)
    ):
        tags.append("cheap_click_trap")
    if not tags:
        tags.append("neutral")
    return tags


def summarize_breakdown_sections(sections: dict[str, Any]) -> dict[str, Any]:
    problem: list[dict[str, Any]] = []
    scale: list[dict[str, Any]] = []
    cheap_clicks: list[dict[str, Any]] = []
    warnings: list[str] = []
    for name, section in sections.items():
        if section.get("status") != "ok":
            warnings.append(f"{name}: {section.get('status')} - {section.get('reason') or section.get('error')}")
            continue
        for segment in section.get("segments") or []:
            payload = {
                "breakdown": name,
                "segment": segment.get("segment"),
                "spend": segment.get("spend"),
                "result": segment.get("result"),
                "cpa": segment.get("cpa"),
                "cpa_index": segment.get("cpa_index"),
                "tags": segment.get("tags") or [],
            }
            tags = set(segment.get("tags") or [])
            if {"reduce", "weak_cvr", "no_result_spend"} & tags:
                problem.append(payload)
            if "scale" in tags:
                scale.append(payload)
            if "cheap_click_trap" in tags:
                cheap_clicks.append(payload)

    problem.sort(key=lambda item: (fnum(item.get("spend")), fnum(item.get("cpa_index"))), reverse=True)
    scale.sort(key=lambda item: fnum(item.get("spend")), reverse=True)
    cheap_clicks.sort(key=lambda item: fnum(item.get("spend")), reverse=True)
    return {
        "top_problem_segments": problem[:10],
        "scale_candidates": scale[:10],
        "cheap_click_traps": cheap_clicks[:10],
        "measurement_warnings": warnings,
    }
