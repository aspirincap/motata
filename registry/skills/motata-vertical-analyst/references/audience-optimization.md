# Audience Optimization

## Purpose

Use this reference when the user asks about targeting, audience quality, placement/device/geography mix, audience fatigue, cannibalization risk, or where traffic quality is weak.

Motata currently analyzes Meta and TikTok platform data only. Without first-party customer/order data, do not claim true new-customer rate, LTV, profit, or incrementality. Use platform-side proxies and label them as proxies.

Audience diagnosis must prefer explicit breakdown reports over coarse `audience_proxy` summaries. The key lenses are country, age/gender, placement, and device. If a platform rejects one dimension combination, use the CLI fallback result and report the unsupported combination instead of flattening the finding into a generic audience comment.

## Required Scope

Before analysis, resolve:

- platform: Meta, TikTok, or both;
- explicit account/ad account/advertiser IDs;
- user type, either user-provided or from `user-type analyze`;
- date range and comparison window.

If account IDs are missing, ask for them. Use recent-spend sampling only when the user explicitly approves sampling or "recent active accounts".

## Useful Dimensions

| Lens | Meta | TikTok |
| --- | --- | --- |
| Structure | campaign, ad set, ad, objective, optimization goal | campaign, ad group, ad, objective, optimization goal |
| Country / geo | `country`, optional `region` follow-up | `AUDIENCE` report with `country_code` or `country` when supported |
| Demographic | `age,gender` | `AUDIENCE` report with `age,gender`, fallback to `age` or `gender` |
| Placement | `publisher_platform,platform_position`, fallback `publisher_platform` | `AUDIENCE` report with `placement`, fallback to the closest supported placement dimension |
| Device | `device_platform` | `AUDIENCE` report with `platform`, fallback to closest supported device dimension |
| Delivery quality | reach, frequency, CPM, CTR, CPC | reach, frequency, CPM, CTR, CPC |
| Result quality | result/conversion, cost per result/conversion, value/ROAS if active | result/conversion, cost per result/conversion, app/web/SKAN/SAN events if active |
| Path quality | outbound/inline clicks, landing URL, app store/deeplink clicks | landing URL, app/app-store path, app events |

## Motata Commands

Meta:

```bash
python3 -m motata_cli meta audience breakdown \
  --account <ACCOUNT_ID> \
  --since YYYY-MM-DD \
  --until YYYY-MM-DD \
  --breakdown country \
  --breakdown age_gender \
  --breakdown placement \
  --breakdown device \
  --json
```

TikTok:

```bash
python3 -m motata_cli tiktok audience breakdown \
  --advertiser-id <ADVERTISER_ID> \
  --start-date YYYY-MM-DD \
  --end-date YYYY-MM-DD \
  --breakdown country \
  --breakdown age_gender \
  --breakdown placement \
  --breakdown device \
  --json
```

If no `--breakdown` is provided, the CLI runs all four key lenses. TikTok uses dimension fallbacks because audience dimensions are more account/objective/permission gated than Meta.

For TikTok, age, gender, placement, and platform/device dimensions must use `report_type=AUDIENCE`; `BASIC` only reliably supports core structural dimensions such as `advertiser_id`, `campaign_id`, `adgroup_id`, `ad_id`, and `country_code`.

## Segment Labels

Audience breakdown output tags each segment:

| Tag | Meaning |
| --- | --- |
| `scale` | Meaningful spend share and better-than-average CPA |
| `monitor` | Meaningful spend share and near-average CPA |
| `reduce` | High spend share and materially worse CPA |
| `weak_cvr` | Conversion rate materially below account average |
| `cheap_click_trap` | High CTR/cheap CPC but weak result quality |
| `no_result_spend` | Non-trivial spend with zero result |
| `neutral` | No strong positive or negative signal |

## Diagnosis Rules

| Signal | Likely issue | Next step |
| --- | --- | --- |
| Frequency rises while CTR falls | audience fatigue or over-narrow delivery | refresh creative, expand or split audience, inspect placements |
| CPM rises and CTR falls | weak creative-audience fit or auction pressure | creative analysis before budget increase |
| Low CPM, high clicks, weak result | cheap low-intent traffic | inspect placement/device/landing path; tag `cheap_click_trap` |
| One placement drives spend but weak result | placement mismatch | compare placement-level CPA/result and creative format |
| Strong clicks, weak app/store event | W2A or store listing mismatch | run landing/app path analysis |
| High result but weak deeper event | optimizing too shallow | move recommendation toward deeper vertical metric |
| One geo/device has low cost and weak quality | delivery arbitrage without business quality | cap or isolate for testing |

## Vertical Rules

| User type | Audience focus |
| --- | --- |
| 电商 | product/offer fit, purchase/value proxy, landing/product URL concentration |
| 工具/W2A | store redirect quality, install -> registration/trial/subscribe proxy |
| 短剧 | hook audience fit, install/register/subscribe quality |
| 休闲游戏 | CPI plus tutorial/retention/ad monetization proxies when active |
| 中重度游戏 | downstream role/level/purchase quality over cheap installs |
| 金融借贷 | lead -> apply -> credit/disbursement quality, compliance-safe segmentation |
| 小说 | install/register/subscribe/purchase quality and creative hook fit |
| 泛娱乐 | registration/login/subscribe or engagement quality |
| 搜索套利 | CPC/CTR/outbound click quality and placement/device cost mix |
| 社交 | install/register/login/subscribe quality, avoid low-quality broad traffic |
| 代理商/多类型 | classify each account first; do not compare vertical-specific CPA across types |

## Output

Return:

- account scope and user type source;
- country, age/gender, placement, and device findings when available;
- proxy metrics used and their limits;
- segments to scale, monitor, fix, reduce, or isolate;
- `top_problem_segments`, `scale_candidates`, `cheap_click_traps`, and measurement warnings;
- follow-up commands or probes if the decisive metric is missing.
