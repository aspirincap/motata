# Error, Cache, And Degradation Policy

## Purpose

Use this reference when Motata analysis hits rate limits, permission failures, empty metrics, unsupported metric groups, timeouts, or partial account coverage.

## Coverage States

| State | Meaning |
| --- | --- |
| `full` | Explicit requested accounts completed with the requested profile |
| `batch` | Explicit accounts completed with batch/light profile suitable for multi-account runs |
| `partial` | Some requested accounts or capability groups completed |
| `sampled` | User approved recent-spend sampling |
| `failed_with_reason` | No usable result; include exact sanitized reason |

## Account And Classification Failures

| Failure | Action |
| --- | --- |
| Missing account IDs | Ask for account/ad account/advertiser IDs before analysis |
| Account IDs present, user type missing | Run `user-type analyze` silently; report source and confidence |
| User type classification failed | Continue only with universal core metrics; label vertical-specific sections as unavailable |
| Multi-account mixed types | Classify each account separately; do not force one shared type |

## Platform/API Failures

| Failure | Action |
| --- | --- |
| Invalid/expired token | Stop that account, redact token, report authentication failure |
| Permission error | Report missing permission/resource; do not retry with another token source unless user provides one |
| Meta application request limit | Retry or recommend retry with low-request batch profile; skip deep landing/story/page-token work |
| TikTok metric group rejected | Split groups or mark unsupported; do not let one high-risk group fail the whole run |
| Timeout | Preserve baseline results and mark deep sections partial |
| Empty response | Distinguish no spend/data from permission or query failure |

## Metric States

| State | Meaning | Report language |
| --- | --- | --- |
| `active` | Field returned a non-zero/non-empty/non-`-` value | "active in selected window" |
| `supported_empty` | Field returned but value is zero/empty/`-` | "supported but empty for this window" |
| `unsupported` | API rejected field/group/level/permission | "not available in this query/account context" |
| `not_queried` | Skipped by profile or budget | "not queried in this run" |

Unsupported does not mean the business has no such event. Supported-empty does not prove the event never happens outside the selected date range.

## Probe Profiles

- `light`: baseline validation and fast health checks.
- `batch`: default for multi-account Capability Map runs.
- `vertical`: targeted groups for the resolved user type.
- `full`: connector QA or single-account deep debugging only.

Do not use full probes for routine multi-account reporting.

## Cache Guidance

Short-lived cached or prior-run results may be reused for:

- account metadata;
- user type classification;
- landing/app/W2A evidence;
- metric probe support map;
- recent Capability Map summary.

Rules:

- Always state when a result is cached or from a prior run.
- Do not use cached data as today's performance fact.
- Refresh daily/weekly KPI data for the requested date window.
- If cached user type conflicts with new landing/app evidence, re-run classification.

## Degradation Notice

When returning partial results, include:

```text
Coverage state: full | batch | partial | sampled | failed_with_reason
Completed: ...
Skipped: ...
Reason: ...
Recommended follow-up: ...
```

For batch runs, summarize failures by category: token/access, rate limit, unsupported metric, timeout, empty data.
