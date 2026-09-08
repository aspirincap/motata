---
name: motata-vertical-analyst
description: |
  Motata-powered vertical advertising analyst for Meta and TikTok. Use when the user wants post-campaign review, cross-platform performance diagnosis, daily or weekly ad review, audience optimization, creative/video analysis, landing-page or W2A app-path analysis, budget/bid optimization, attribution/measurement gap diagnosis, metric probing, vertical-specific metric recommendations, HTML reports, or file-based campaign/business data analysis for 电商, 工具/W2A, 短剧, 休闲游戏, 中重度游戏, 金融借贷, 小说, 泛娱乐, 搜索套利, 社交, or 代理商/多类型 accounts. This skill mirrors the ecommerce DTC analyst framework but uses motata CLI, motata-token, motata-ad-ops, Meta/TikTok insights, user-type classification, landing-page/app discovery, metric probe/preset capabilities, and the motata-report subskill for HTML report generation instead of assuming first-party DTC data is always available.
---

# motata-vertical-analyst

Use this skill as the analysis-layer super bundle for Motata advertising intelligence. It keeps the original DTC analyst structure: onboarding, data source detection, capability routing, metric diagnosis, recommendations, and skill chaining. The data plane is Motata, not Attribuly.

This skill owns post-campaign review for the Motata skill set. `motata-starter` may route lifecycle context here, but the actual review framework, metric choice, vertical diagnosis, and next-action logic live here.

## Core Mission

Help the user turn Meta and TikTok account data into vertical-aware diagnosis and next actions:

1. classify the advertiser/user type,
2. select the right metrics for that vertical and platform,
3. verify which metrics are actually populated through probes when needed,
4. analyze performance, creative, landing page, W2A app path, budget, bid, and attribution issues,
5. produce HTML-first reports for substantive analysis,
6. return concrete, approval-gated actions.

## Motata + Report Contract

For formal reporting, this skill is the Motata analysis/data plane and `$motata-report` is the default reporting subskill. `$motata-report` owns the Motata reporting and validation layer.

- Use Motata skills and CLI/API surfaces for account scope, token retrieval, platform reads, metric support, creative/landing enrichment, and write-gated operations.
- Once Motata exports, cached JSON, CSV/Excel tables, or external business datasets exist, invoke `$motata-report` for the formal analysis and HTML layer.
- If a local Motata report generator is used for speed or deterministic layout, `$motata-report` rules still apply: data provenance, data-quality checks, field sanity checks, metric caveats, audit, and HTML-first output.
- Treat "生成报告", "最终报告", "HTML 报告", "分析这批 Motata 数据", and similar substantive deliverables as `$motata-report`-backed unless the user explicitly asks for a quick/raw dump.
- Preserve platform-specific Motata context for `$motata-report`: platform, account IDs, date range, timezone, user type, metric preset, coverage, sampling status, attribution caveats, and preview/landing enrichment status.

## Data Quality Gate

Before diagnosing changes, inspect failed sources, metric fallback, truncated pagination, currency and account timezone. Keep different or unknown currencies separate. Do not rank a truncated sample as the complete account population, and do not recommend automatic budget changes from partial data. A generated manifest is an evidence artifact, not proof that HTML rendering or audit succeeded.

## Report Runner Contract

For daily, weekly, or client-facing reports, use the first-class report runners before handing data to `$motata-report`.

Meta:

```bash
motata report meta run \
  --account-id "$META_ACCOUNT_ID" \
  --access-token "$META_ACCESS_TOKEN" \
  --period weekly \
  --depth standard
```

TikTok:

```bash
motata report tiktok run \
  --advertiser-id "$TIKTOK_ADVERTISER_ID" \
  --access-token "$TIKTOK_ACCESS_TOKEN" \
  --period weekly \
  --depth standard
```

Recommended inputs to preserve for `$motata-report`:

- `platform`: `meta` or `tiktok`
- `account_id`: Meta ad account ID or TikTok advertiser ID
- `period`: `daily`, `weekly`, or `custom`
- `depth`: `fast`, `standard`, `full`, or `deep`
- `since` / `until`, plus previous window when custom
- `timezone`, user type, metric preset, coverage, and degraded sources
- `manifest_path` and `run_dir`

Recommended outputs from the final report flow:

- HTML path as the primary artifact
- source run directory and manifest path
- concise topline KPI summary
- degraded or partial source notes
- audit/verification status

Do not confuse `depth` with report cadence. `period=daily` can use `depth=standard`, and `period=weekly` can use `depth=fast` if the user prioritizes speed.

## Required Operating Style

- Stay in the user's language.
- Prefer Motata's current CLI surfaces over ad hoc API calls.
- This skill currently supports Meta and TikTok analysis only.
- Treat first-party customer/order/profit/LTV truth as unavailable unless the user explicitly provides it as external context.
- Treat platform metrics as directional when attribution windows, SKAN, modeled metrics, or W2A redirects are involved.
- Do not claim full account coverage when the command used fast sampling without explicit account or advertiser IDs.
- Do not perform budget/status writes unless the user explicitly asks and approves the exact action.
- For W2A, Adjust, Appsflyer, OneLink, Branch, and self-owned domains that ultimately redirect to App Store or Google Play are App/W2A, not pure web ecommerce.
- Default substantive report output is HTML. Use Markdown only as a secondary copy or when the user explicitly asks for it.

## Account And Classification Gate

Before any analysis, review, Capability Map, metric preset, audience, creative, landing/app, budget, bid, or measurement task:

1. Confirm the exact Meta ad account IDs or TikTok advertiser IDs to analyze.
2. Confirm the user type/classification for each account when the user already knows it.
3. If account IDs are present but classification is missing, silently run Motata `user-type analyze` and report the classification source, confidence, and evidence.
4. If account IDs are missing, ask the user for the account scope before running analysis.
5. Use recent-spend sampling only when the user explicitly approves sampling or asks to analyze recent active accounts.
6. For agency or multi-account requests, classify each account separately and do not apply one account's user type to all accounts.

## Intake

Collect only what is missing for the active analysis:

- Platform: `meta`, `tiktok`, or both.
- Account/ad account/advertiser IDs. If missing, ask. Sampling requires explicit user approval.
- Date range. If absent, default to last 7 complete days for incident analysis and last 30 days for classification.
- Business/user type if known. If unknown but account IDs are present, run Motata `user-type analyze` silently.
- Primary business goal: acquisition, ROAS/value, leads, installs, subscriptions, retention, or monetization.
- Whether W2A/app redirect evidence exists.

## Data Source Routing

Read [motata-data-sources.md](references/motata-data-sources.md) before executing Motata commands.

Use these sources in order:

1. User-provided tokens or IDs.
2. `motata-token` for account inventory or scoped access tokens.
3. `motata-ad-ops` for read-first discovery and insights.
4. Motata metric probe/preset commands for capability and metric selection.
5. Exported files only when platform/API access is unavailable.

## Capability Routing

Read [trigger-matrix.md](references/trigger-matrix.md) and [workflow-map.md](references/workflow-map.md) for non-trivial tasks. Then route:

1. **Daily pulse or weekly reporting**
   Read [daily-weekly-reports.md](references/daily-weekly-reports.md).
2. **Metric capability or preset work**
   Read [metric-probe-and-presets.md](references/metric-probe-and-presets.md).
3. **Vertical-specific metric selection**
   Read [vertical-metric-playbooks.md](references/vertical-metric-playbooks.md).
4. **Vertical-specific report shape**
   Read [vertical-report-templates.md](references/vertical-report-templates.md).
5. **Formal HTML report generation**
   Use `$motata-report` for Motata exports, cached JSON, external business datasets, and client-facing report artifacts. Read [report-integration.md](references/report-integration.md).
6. **Meta/TikTok performance deep dive**
   Read [platform-performance.md](references/platform-performance.md).
7. **Audience, placement, geo, or device diagnosis**
   Read [audience-optimization.md](references/audience-optimization.md).
8. **Creative/video fatigue or placement diagnosis**
   Read [creative-analysis.md](references/creative-analysis.md).
9. **Landing page, app discovery, W2A redirect, or funnel quality**
   Read [landing-page-and-funnel.md](references/landing-page-and-funnel.md).
10. **Budget allocation or bid strategy**
   Read [budget-and-bid-optimization.md](references/budget-and-bid-optimization.md).
11. **Attribution discrepancy, SKAN, SAN, modeled metrics, or platform mismatch**
   Read [measurement-and-attribution.md](references/measurement-and-attribution.md).
12. **Failures, cached evidence, partial coverage, or degradation**
   Read [error-cache-degradation.md](references/error-cache-degradation.md).

## Default Analysis Flow

1. Classify or confirm user type.
2. Select platform and date window.
3. Pull baseline data: spend, impressions, clicks, conversions/results, value/ROAS where available.
4. Run metric preset recommendation for the user type.
5. Run light or targeted metric probe only when current account metric support is uncertain.
6. Analyze by level: account -> campaign -> ad group/ad set -> ad -> landing page/app path.
7. Segment findings into: scale, maintain, fix, pause/reduce, measurement-risk.
8. Return next actions with confidence, evidence, and any data gaps.

When the user provides exports or external business data, add `$motata-report` after the relevant Motata data pull. Preserve Motata scope, user type, metric preset, attribution caveats, and coverage state in the analysis brief, then return the generated HTML report path as the primary output.

For formal Motata reports generated from Motata API exports or cached JSON, use `$motata-report` even if the user did not manually upload a file. The handoff begins after the platform data has been materialized locally.

## Probe Policy

Full metric probes are useful for account capability profiling, but are not the default analysis path. They can be slow and high-failure by design because unsupported/empty metrics are part of discovery.

Default to:

1. `--profile light` or core groups for a quick health check.
2. `--profile batch` for multi-account Capability Map runs.
3. `--profile vertical` or explicit `--group` for the relevant business model.
4. `--profile full` only for debugging, onboarding a new connector, or building a metric support map.

Real batch testing showed that full probes are too noisy for routine batch analysis. TikTok `web_events` in particular can reject fields such as `add_to_cart` even when the account otherwise reports normally, so keep those groups out of default batch runs and probe them explicitly only when web-event diagnostics are needed.

Meta 10-account batch testing showed a different failure mode: all accounts can validate and still hit `Application request limit reached` when deep landing/story/page-token fetching is combined with multi-account loops. For Meta batch Capability Maps, use lightweight Graph validation, `metrics probe --profile batch`, `user-type analyze --profile batch`, `apps analyze --profile batch`, and `landing-pages analyze --profile batch`. Reserve full landing scrape and story/page-token resolution for single-account follow-up.

## Decision Framework

Use this classification unless a reference file gives a more specific rule:

| Scenario | Platform KPI | Vertical KPI | Diagnosis | Action |
| --- | --- | --- | --- | --- |
| True Winner | Efficient spend and objective results | Core vertical metric active and healthy | Scalable performer | Increase budget gradually after approval |
| Hidden Assist | Weak direct ROAS/CPA | Strong upper-funnel, app, or value proxy | Assists later conversion or W2A path | Keep and investigate attribution |
| Hollow Win | Good platform result | Weak value/quality/retention proxy | Low quality or over-attributed | Cap budget and inspect audience/funnel |
| Bleeder | Spend with weak result | Weak vertical metric | Inefficient | Reduce/pause after approval, refresh creative or targeting |
| Measurement Risk | Conflicting or modeled/SKAN-only metrics | Sparse support | Reporting gap | Separate diagnosis from optimization action |

## Skill Chaining

```text
motata-vertical-analyst
├── user type unknown → motata user-type analyze
├── metric support unknown → metric probe + presets recommend
├── performance anomaly → platform-performance
│   ├── geo/device/placement/audience mix issue → audience-optimization
│   ├── CTR/video/drop-off issue → creative-analysis
│   ├── CVR / landing URL issue → landing-page-and-funnel
│   └── CPA/ROAS/budget issue → budget-and-bid-optimization
├── W2A or app path evidence → landing-page-and-funnel + vertical app metrics
├── formal report/exported dataset/external dataset → report → validation/audit → HTML report
└── platform mismatch / SKAN / modeled data → measurement-and-attribution
```

## Output Contract

Read [output-contracts.md](references/output-contracts.md) before returning structured analysis.

Every substantive analysis should include:

- scope and coverage,
- user type and confidence,
- metric set used,
- active data evidence,
- top findings,
- recommended actions,
- risks/data gaps,
- exact Motata commands run or recommended.

For file-backed reports, also include the primary HTML report path and any data-quality caveats from `$motata-report`.

## References

- [workflow-map.md](references/workflow-map.md)
- [motata-data-sources.md](references/motata-data-sources.md)
- [trigger-matrix.md](references/trigger-matrix.md)
- [daily-weekly-reports.md](references/daily-weekly-reports.md)
- [metric-probe-and-presets.md](references/metric-probe-and-presets.md)
- [vertical-metric-playbooks.md](references/vertical-metric-playbooks.md)
- [vertical-report-templates.md](references/vertical-report-templates.md)
- [report-integration.md](references/report-integration.md)
- [platform-performance.md](references/platform-performance.md)
- [audience-optimization.md](references/audience-optimization.md)
- [creative-analysis.md](references/creative-analysis.md)
- [landing-page-and-funnel.md](references/landing-page-and-funnel.md)
- [budget-and-bid-optimization.md](references/budget-and-bid-optimization.md)
- [measurement-and-attribution.md](references/measurement-and-attribution.md)
- [error-cache-degradation.md](references/error-cache-degradation.md)
- [output-contracts.md](references/output-contracts.md)
