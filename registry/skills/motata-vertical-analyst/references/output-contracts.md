# Output Contracts

## Default Deliverable

Default substantive Motata reports to HTML.

- Primary artifact: `analysis_report.html` or the equivalent generated HTML file.
- Use `$motata-report` for formal report generation, UDA-style validation, creative preview table requirements, and post-generation audit.
- Chat response: short summary plus the HTML file path.
- Markdown: secondary companion copy only; do not make it the primary report unless the user asks.
- For API-only quick answers, a concise chat response is fine. For full review, weekly report, exported-file analysis, or external business-data analysis, produce or request an HTML report.

## Coverage Header

Start substantive reports with:

```text
Scope: platform(s), account/ad account/advertiser IDs or sampling mode, date range
User type: type + confidence/source
Classification source: user-provided | Motata user-type analyze | unavailable
Metric set: preset name or custom set
Coverage: explicit accounts or approved recent-spend sampling
Coverage state: full | batch | partial | sampled | failed_with_reason
Limits: platform data only unless external business data was provided
```

For batch Capability Maps, also include:

```text
Profile: batch/light/vertical/full
Fallbacks: count and reason, especially Meta app limit or missing token
Conv-like definition: state the action/event families used and warn that values are not comparable across verticals
```

## Performance Report

Default to an HTML version of this structure. Markdown below is only the content skeleton.

```markdown
# Performance Review

## Summary
- ...

## KPI Snapshot
| Platform | Entity | Spend | Impressions | Clicks | CTR | Result | Cost/Result | Value/ROAS |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |

## Findings
1. ...

## Actions
| Action | Entity | Why | Risk | Needs approval |
| --- | --- | --- | --- | --- |

## Data Gaps
- ...
```

## Daily Pulse

Default to an HTML version of this structure. Markdown below is only the content skeleton.

```markdown
# Daily Pulse

Scope: ...
User type: ...
Classification source: ...
Date: ...
Coverage state: ...

## Status
- normal | watch | urgent
- main reason: ...

## KPI Snapshot
| Platform | Account | Spend | Clicks | CTR | Result | Cost/Result | Key vertical metric |
| --- | --- | ---: | ---: | ---: | ---: | ---: | --- |

## Watch Items
| Entity | Signal | Why it matters | Action |
| --- | --- | --- | --- |

## Data Gaps
- ...
```

## Weekly Report

Default to an HTML version of this structure. Markdown below is only the content skeleton.

```markdown
# Weekly Performance Review

Scope: ...
User type: ...
Classification source: ...
Period: current vs previous
Coverage state: ...

## Executive Summary
- ...

## KPI Change
| Platform | Spend Δ | Click Δ | Result Δ | Cost/Result Δ | Key vertical metric Δ |
| --- | ---: | ---: | ---: | ---: | ---: |

## Drivers
| Driver | Evidence | Impact | Confidence |
| --- | --- | --- | --- |

## Winners / Watchlist / Bleeders
| Bucket | Entity | Evidence | Next action |
| --- | --- | --- | --- |

## Next Actions
| Priority | Action | Approval needed | Evidence |
| --- | --- | --- | --- |
```

## Audience Optimization Report

```markdown
# Audience Optimization

Scope: ...
User type: ...
Classification source: ...
Coverage state: ...

## Segment Findings
| Lens | Segment | Spend share | Result | CPA index | Tags | Action |
| --- | --- | ---: | ---: | ---: | --- | --- |

## Country / Age-Gender / Placement / Device Notes
- ...

## Summary Buckets
- top_problem_segments:
- scale_candidates:
- cheap_click_traps:
- measurement_warnings:

## Limits
- Platform-side proxy analysis only.
```

## Vertical Report

Default to an HTML version of this structure. Markdown below is only the content skeleton.

```markdown
# [User Type] Performance Report

Scope: ...
User type: ...
Classification source: ...
Metric set: ...
Coverage state: ...
Limits: platform data only unless external business data was provided

## KPI Snapshot
- ...

## Vertical Diagnosis
- ...

## Actions
- ...

## Data Gaps
- ...
```

## Metric Probe Summary

```markdown
# Metric Probe Summary

## Active Metrics
- category: metric = sample

## Supported But Empty
- ...

## Unsupported / Conflicting
- ...

## Request Stats
- requests:
- splits:
- fallbacks:
```

## Creative Tables

Any HTML report section that lists Creative, Ad Creative, video, image, or creative-retention rows must include these columns by default:

| Column | Requirement |
| --- | --- |
| Preview | Render an inline image from `preview_image_url`, thumbnail, cover, image URL, or ad/creative preview image when available. |
| Preview action | Hover/focus action on the preview image using `preview_url`, ad preview URL, creative preview URL, playable/video URL, image URL, or thumbnail URL. Do not add a standalone Preview URL column by default. |
| Platform | Meta or TikTok. |
| Campaign | Campaign name/id when available. |
| Ad group / Ad set | Ad group/ad set name/id when available. |
| Ad / Creative | Ad id, creative id, and ad/creative name when available. |
| Metrics | Spend, impressions, clicks, CTR, conversion/result, CPA/CVR, and vertical-specific creative metrics. |
| Diagnosis | Winner/watchlist/fatigue/drop-off reason where relevant. |

HTML rendering rules:

- Use `<img>` for `Preview` with constrained dimensions so rows stay scannable.
- Do not use a standalone Preview URL column by default; expose the click target as an "打开预览 / Preview" hover/focus action on the preview cell.
- For video creative, use the cover/thumbnail for `Preview` and attach the playable/preview/video URL to the preview action when both exist.
- If preview data is missing, show `Unavailable` in `Preview` while preserving the row and the diagnosis.

## Preset Recommendation

Default to an HTML version when the recommendation is part of a broader report. Markdown below is only the content skeleton.

```markdown
# Metric Preset Recommendation

User type: ...
W2A: yes/no

## Use Now
- ...

## Keep But Empty
- ...

## Not Available
- ...

## Notes
- ...
```

## Recommendation Tone

- Separate evidence from hypothesis.
- Say "likely" when only proxy metrics support a diagnosis.
- Use "needs approval" for any mutation.
- Keep command outputs summarized, not pasted wholesale.
- Do not infer first-party customer/order truth, profit, LTV, or true incrementality from platform-only data.

## Degradation Notice

```text
Coverage state: full | batch | partial | sampled | failed_with_reason
Completed: ...
Skipped: ...
Reason: ...
Recommended follow-up: ...
```
