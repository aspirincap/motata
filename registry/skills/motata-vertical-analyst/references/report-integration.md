# Report Integration

Use this reference when Motata analysis becomes a formal report, client-facing HTML, exported-data analysis, or external business-data analysis.

## Route

Route to `$motata-report` after Motata has materialized platform data locally as JSON/CSV/Excel or after the user provides external datasets.

Do not route routine API reads, account discovery, token retrieval, campaign inspection, or write operations to `$motata-report`; keep those in Motata CLI skills.

For first-class report pulls, materialize data with:

```bash
motata report meta run --account-id "$META_ACCOUNT_ID" --access-token "$META_ACCESS_TOKEN" --period weekly --depth standard
motata report tiktok run --advertiser-id "$TIKTOK_ADVERTISER_ID" --access-token "$TIKTOK_ACCESS_TOKEN" --period weekly --depth standard
```

Use `period=daily|weekly|custom` for cadence and date windows. Use `depth=fast|standard|full|deep` for completeness and latency. Keep both values in the handoff.

## What To Pass To `$motata-report`

- platform and account/advertiser IDs,
- date range and timezone,
- user type/classification and confidence,
- metric preset used,
- coverage state: full, batch, partial, sampled, or failed,
- attribution limits such as SKAN, modeled data, Meta attribution windows, or W2A redirects,
- source run directory,
- preview and landing enrichment status,
- data gaps and failed/degraded commands.
- whether previous-period data exists and has the same date-window length as the current period.
- for TikTok creative sections, the final ad IDs selected for report display before running targeted creative-retention/asset enrichment.
- for TikTok, whether `targeted_creative_retention` used only top/final ad IDs and whether preview enrichment was skipped or included.

## Required Output

`$motata-report` should return:

- primary HTML path,
- source run directory,
- validation or audit artifact path,
- concise KPI summary,
- degraded data notes.

## HTML Requirements

- Creative/ad tables include inline `Preview`; preview links are exposed through a hover/focus action on the preview image rather than a standalone URL column.
- Long URLs and long entity names wrap inside table cells.
- Reports include data quality/provenance.
- Period-over-period conclusions appear only when current and previous windows are both present and equal length.
- TikTok creative-retention enrichment is targeted to final report ad IDs by default; broad scans are exploratory, not the default report path.
- Final artifacts do not include tokens, API keys, auth headers, or raw token payloads.
