# Daily And Weekly Reports

## Purpose

Use this reference for daily pulse checks, pacing reviews, weekly reports, week-over-week analysis, and routine account monitoring.

Reports are based on Motata-accessible Meta/TikTok data only. Do not infer first-party revenue, profit, LTV, or true incrementality unless the user provides those values as external context.

## Required Gate

Before running a report:

1. Ask for the account/ad account/advertiser IDs if missing.
2. Ask for user type if missing.
3. If account IDs are present but user type is missing, run `user-type analyze` silently and report the classification source.
4. Use sampling only when the user explicitly approves sampling.

## Standard Pull Commands

Use these commands as the default data-pull entry points. They produce `build/report_runs/.../manifest.json`, which should then be passed to `$motata-report` for HTML generation.

Meta weekly standard:

```bash
motata report meta run \
  --account-id "$META_ACCOUNT_ID" \
  --access-token "$META_ACCESS_TOKEN" \
  --period weekly \
  --depth standard
```

TikTok weekly standard:

```bash
motata report tiktok run \
  --advertiser-id "$TIKTOK_ADVERTISER_ID" \
  --access-token "$TIKTOK_ACCESS_TOKEN" \
  --period weekly \
  --depth standard
```

Daily speed-first:

```bash
motata report meta run --account-id "$META_ACCOUNT_ID" --access-token "$META_ACCESS_TOKEN" --period daily --depth fast
motata report tiktok run --advertiser-id "$TIKTOK_ADVERTISER_ID" --access-token "$TIKTOK_ACCESS_TOKEN" --period daily --depth fast
```

`period` controls date windows. `depth` controls completeness and latency. Do not use `--depth` to mean "日报".

## Daily Pulse

Default window: yesterday in the account/reporting timezone when available, otherwise the last complete day.

Use for:

- "今天/昨天怎么样";
- "daily pulse";
- "看下今日风险";
- pacing and anomaly checks.

Minimum data:

- spend, impressions, clicks, CTR, CPC, CPM;
- result/conversion and cost per result/conversion;
- top spend campaigns/ad groups/ad sets;
- active vertical-specific metrics if already known;
- landing/app/W2A flags when relevant.

Output shape:

```markdown
# Daily Pulse

Scope: ...
User type: ...
Date: ...
Coverage: ...

## Status
- overall: normal | watch | urgent
- main reason: ...

## KPI Snapshot
| Platform | Account | Spend | Clicks | CTR | Result | Cost/Result | Key vertical metric |
| --- | --- | ---: | ---: | ---: | ---: | ---: | --- |

## Watch Items
| Entity | Signal | Why it matters | Action |
| --- | --- | --- | --- |

## No-Action Areas
- ...

## Data Gaps
- ...
```

Daily pulse should prefer low-request commands and batch profiles. It should not trigger full landing scrape or full metric probe unless the user asks for deep diagnosis.

## Weekly Report

Default window: last 7 complete days vs previous 7 complete days.

Use for:

- "上周表现";
- "weekly report";
- "周报";
- "环比";
- "复盘这周投放".

Minimum data:

- current and previous period platform KPIs;
- campaign/ad group/ad set/ad ranking;
- creative/video signals;
- landing/app/W2A evidence;
- metric preset recommendation for the resolved user type;
- known measurement risks.

Output shape:

```markdown
# Weekly Performance Review

Scope: ...
User type: ...
Period: current vs previous
Coverage: ...

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

## Creative And Audience Notes
- ...

## Landing / W2A / Measurement Notes
- ...

## Next Week Actions
| Priority | Action | Owner/approval | Evidence |
| --- | --- | --- | --- |

## Data Gaps
- ...
```

## Escalation

- CTR/frequency/video issue -> `creative-analysis.md`.
- Geo/device/placement/audience mix issue -> `audience-optimization.md`.
- High clicks but weak result -> `landing-page-and-funnel.md`.
- CPA/ROAS/budget issue -> `budget-and-bid-optimization.md`.
- SKAN/SAN/modeled/attribution issue -> `measurement-and-attribution.md`.
