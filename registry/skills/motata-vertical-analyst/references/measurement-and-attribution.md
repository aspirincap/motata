# Measurement And Attribution

## Purpose

Use this reference for platform mismatches, SKAN/SAN limitations, attribution windows, modeled/estimated metrics, and "why numbers do not match" questions.

## Common Causes

| Cause | Platform |
| --- | --- |
| Attribution window differences | Meta, TikTok |
| Impression-time vs conversion-time reporting | Meta, TikTok |
| SKAN privacy thresholds and delayed postbacks | Meta, TikTok |
| SAN availability only for advanced/dedicated app campaigns | TikTok |
| Modeled or estimated metrics | Meta |
| Campaign objective mismatch at campaign aggregation | TikTok result/cost_per_result |
| W2A redirect loses web context | Both |

## Meta Checks

- `action_attribution_windows`
- `action_report_time`
- `use_account_attribution_setting`
- `use_unified_attribution_setting`
- estimated/in-development fields such as reach/frequency/unique metrics/video play.

## TikTok Checks

- `campaign_dedicate_type`
- SKAN metrics and postback sequence.
- SAN/app event metrics.
- `result`, `cost_per_result`, `result_rate` at campaign level when ad groups have mixed optimization goals.
- Metrics returning `-` under invalid/no-meaning conditions.

## Probe Use

Use metric probe to establish whether a metric is:

- supported and active,
- supported but empty,
- unsupported for this account/date/level/permission.

Do not treat unsupported as business truth.

## W2A Rules

For Adjust/Appsflyer/self-domain-to-store:

- Web landing page metrics may show clicks but not final app conversions.
- App install/trial/subscribe/SKAN/SAN metrics are often more relevant than website purchase.
- If only store redirect evidence exists and app events are empty, report measurement gap.

## Output

Return:

- What differs.
- Which attribution/reporting setting likely explains it.
- Which metrics are reliable for decisions.
- Which metrics are directional only.
- What to probe or configure next.
