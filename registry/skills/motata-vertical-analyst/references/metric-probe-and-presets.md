# Metric Probe And Presets

## Purpose

Use this reference when the user asks which Meta/TikTok metrics are available, which metrics should be used for a vertical, or why a metric is missing.

## Practical Position

Full probes have real value for onboarding and debugging, but they should not be the default analysis path. Meta and TikTok both contain metrics that are level-gated, objective-gated, product-gated, attribution-gated, privacy-thresholded, deprecated, estimated, in development, or incompatible with certain breakdowns.

Treat failures as discovery:

- `unsupported`: the API rejected the field/group or the account lacks permission/product support.
- `supported_empty`: the metric can return but is zero/empty in the selected time window.
- `active`: the metric returned non-zero, non-empty, non-`-` evidence.

## Default Probe Strategy

1. Run a baseline spend request.
2. Check the five non-negotiable core metrics: impressions, clicks, spend, conversion, revenue.
3. If any of the five are missing, especially revenue/value, run a full metric probe for that single account and merge the evidence before final analysis. If the full probe still returns empty/unsupported, state it as a measurement gap.
4. For batch jobs, use `--profile batch`; it is designed to avoid high-risk gated groups.
5. Probe only the groups relevant to the user type with `--profile vertical` or explicit `--group`.
6. Use `--profile full` only for connector QA, onboarding, or debugging missing core/vertical metrics.
7. Cache or save probe JSON when possible; reuse it for preset recommendations.

Profiles:

| Profile | Use | Notes |
| --- | --- | --- |
| `light` | spend/impression/click health check | lowest request cost |
| `batch` | multi-account Capability Map | default CLI profile; avoids noisy high-risk groups |
| `vertical` | normal account diagnosis | broader than batch, still safer than full |
| `full` | connector QA / metric support map | expected to produce unsupported metrics |

## Meta Commands

Light/core probe:

```bash
python3 -m motata_cli meta metrics probe \
  --account <AD_ACCOUNT_ID> \
  --since YYYY-MM-DD \
  --until YYYY-MM-DD \
  --profile light \
  --limit 1 \
  --json
```

Targeted examples:

```bash
python3 -m motata_cli meta metrics probe --account <ID> --date-preset last_7d --profile batch --json
python3 -m motata_cli meta metrics probe --account <ID> --date-preset last_7d --profile vertical --json
python3 -m motata_cli meta metrics probe --account <ID> --date-preset last_7d --profile full --json
```

Use `--async-report` for heavy groups when sync insights repeatedly fail or time out.

For Meta multi-account Capability Maps, pair probe profiles with low-request analysis profiles:

```bash
python3 -m motata_cli meta user-type analyze --account <ID> --date-preset last_7d --profile batch --json
python3 -m motata_cli meta apps analyze --account <ID> --date-preset last_7d --profile batch --json
python3 -m motata_cli meta landing-pages analyze --account <ID> --date-preset last_7d --profile batch --json
```

`landing-pages --profile batch` relies on insight URL breakdowns and skips page-token/story/ad-context deep fetches by default. If W2A/landing evidence is incomplete, rerun a single account with `--profile full` after rate limits cool down.

## TikTok Commands

Light/core probe:

```bash
python3 -m motata_cli tiktok metrics probe \
  --advertiser <ADVERTISER_ID> \
  --start-date YYYY-MM-DD \
  --end-date YYYY-MM-DD \
  --profile light \
  --page-size 1 \
  --json
```

Targeted examples:

```bash
python3 -m motata_cli tiktok metrics probe --advertiser <ID> --start-date YYYY-MM-DD --end-date YYYY-MM-DD --profile batch --json
python3 -m motata_cli tiktok metrics probe --advertiser <ID> --start-date YYYY-MM-DD --end-date YYYY-MM-DD --profile vertical --json
python3 -m motata_cli tiktok metrics probe --advertiser <ID> --start-date YYYY-MM-DD --end-date YYYY-MM-DD --group web_events --json
```

TikTok is more likely to fail at group level because many metrics require specific dimensions, campaign types, or reporting products. Real batch runs showed `web_events` can reject `add_to_cart`; keep it out of routine batch probes and run it only when web event diagnostics are explicitly needed.

The current TikTok metric catalog also covers the newer report families used in Motata analysis: `live`, `messaging`, `shop`, `offline`, `onsite_*` revenue paths, creative `interactive_add_on_*` fields, attribution (`vta_*` / `cta_*` / `evta_*`), SKAN, SAN, and the newer attributes such as `placement_type` and `campaign_automation_type`.

Vertical preset recommendations now prioritize those newer TikTok families where they matter most: `onsite_*` and `shop_*` for ecommerce, `onsite_destination_visits` / `onsite_download_start` / `real_time_app_install` / `skan_app_install` for W2A, `paid_engaged_view_15s` and `onsite_subscribe_value_day*` for short drama and novel, and `live` / `messaging` for social and泛娱乐.

## Preset Recommendation

```bash
python3 -m motata_cli metrics presets recommend \
  --platform meta|tiktok|all \
  --user-type "<TYPE>" \
  --probe-file probe.json \
  --json
```

Add `--w2a` when landing-page/app analysis found Adjust, Appsflyer, OneLink, Branch, or final App Store / Google Play redirects.

## How To Interpret Recommendations

- `recommended_active`: use directly in analysis.
- `recommended_supported_but_empty`: keep in the vertical model, but state that current account/window has no evidence.
- `not_available`: do not build analysis on it unless the user changes level, date range, campaign type, or permissions.
- `core_metric_coverage`: verify impressions, clicks, spend, conversion, and revenue. A report is not complete when revenue is omitted; it must be either populated from active value fields or explicitly marked as unavailable after full probe.

## Request Budget Guidance

| Need | Recommended scope |
| --- | --- |
| Quick report | No full probe; use presets and core insights |
| New account onboarding | `batch`, then `vertical` if needed |
| Debug missing conversion metric | Single relevant group, then split if needed |
| Missing revenue/value in final report | Single-account `full` probe, then mark active/empty/unsupported |
| Build metric support map | `full` grouped probe with request cap and saved JSON |

## Has-Data Rule

A metric has data only when a returned value is not `0`, `0.0`, empty string, `null`, empty list/object, or `-`.
