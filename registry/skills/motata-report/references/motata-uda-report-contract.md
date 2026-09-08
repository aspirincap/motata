# Motata Report Contract

## Division of Labor

Motata layer:

- account and token resolution,
- Meta/TikTok data pulls,
- user-type classification,
- metric preset/probe,
- creative preview and landing enrichment,
- platform-specific caveats and approval-gated write actions.

Report validation layer:

- data loading and validation,
- source provenance and row coverage,
- metric sanity checks,
- exploratory summaries and outlier/driver identification,
- final HTML report structure,
- companion validation artifacts.

## Required Artifacts

For a formal report, produce as many of these as practical:

```text
build/report_runs/<platform>_<account>_<period>_<depth>_<until>/
├── manifest.json
├── user_type.json
├── current_*_insights.json
├── previous_*_insights.json
├── audience_*.json
├── landing_pages.json
├── report_audit.json
└── validation_summary.json  # optional but preferred

build/<account-or-advertiser>_<platform>_report_<date>.html
```

If the report uses external CSV/Excel/business data, also keep normalized intermediate CSV/JSON files and cite their provenance in the HTML.

## Validation Checklist

Required checks:

- `manifest.json` exists when data came from `motata report meta run` or `motata report tiktok run`,
- all referenced source files exist,
- JSON files parse,
- account-level current and previous data are non-empty,
- campaign/adset/ad or campaign/adgroup/ad rows are non-empty for performance reports,
- required KPI fields are parseable,
- Creative/ad tables include an inline `Preview`; preview links are exposed through a hover/focus action on the preview image rather than a standalone URL column,
- HTML contains URL wrapping rules,
- final artifact does not contain access tokens, API keys, auth headers, or raw token payloads.

Recommended checks:

- row counts are listed in the report,
- report explains partial/degraded sources,
- attribution windows are stated,
- first-party data absence is stated,
- platform-specific unsupported or empty metrics are not treated as zero business truth.

## HTML Sections

Use this order by default:

1. Scope and KPI hero.
2. Executive summary.
3. Recommended actions.
4. Campaign/ad group/ad set drivers.
5. Creative/ad table with previews.
6. Landing/app/W2A path.
7. Audience/placement/device/geo.
8. Measurement risks and data gaps.
9. Data quality and provenance.

## Creative Preview Policy

Meta:

- Do not deep-fetch ad content previews for every ad in a large account.
- Decide the final report ads first from insights.
- Fetch creative thumbnail and ad preview only for those final ads.
- If preview fetching fails, keep the row and show `Unavailable` plus `-`.

TikTok:

- Decide final report creative/ad rows from the base insights pull first.
- Run creative-retention/asset enrichment only for those final ad IDs; do not scan/enrich the whole account just to populate report preview rows.
- If a broader creative-retention scan is explicitly requested, label it as exploratory and keep it out of the default report path.
- Treat `smart_plus_ad_id` as equivalent to `ad_id_v2` when normalizing Smart+ and upgraded Smart+ ad identity.
- Use cover/thumbnail for the inline image and attach playable/video/preview URLs to the preview hover/focus action.
- Warn that signed TikTok URLs may expire.

## Period Comparison Policy

- Preserve `period` and `depth` separately from the manifest. `period` is cadence/date-window logic; `depth` is completeness/latency.
- Period-over-period cards and conclusions are allowed only when both current and previous datasets are present and the date ranges are equal length.
- If the previous window is missing, partial, or a different length, render single-period KPIs and record the comparison gap in data quality.

## Table Rendering Rules

Every HTML report must include CSS equivalent to:

```css
.table-wrap { overflow:auto; max-width:100%; }
table { table-layout:fixed; width:100%; }
th, td { overflow-wrap:anywhere; word-break:break-word; }
.table-wrap a { overflow-wrap:anywhere; word-break:break-all; }
```

Preview images must have constrained width/height and `object-fit: cover`.

## Chat Response

Keep chat short:

- HTML path,
- source run directory,
- dates,
- 3-5 KPI numbers,
- validation/test result,
- degraded data notes.
