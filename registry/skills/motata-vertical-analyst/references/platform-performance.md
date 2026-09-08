# Platform Performance

## Purpose

Use this reference for Meta/TikTok account, campaign, ad set/ad group, or ad performance reviews.

## Minimum Data

Fetch:

- spend, impressions, clicks, CTR, CPC, CPM,
- reach and frequency when available,
- result/conversion and cost per result/conversion,
- value/ROAS if the vertical depends on value,
- objective/optimization/billing metadata.

## Grain

Analyze in this order:

1. Account overview.
2. Campaign objective and spend concentration.
3. Ad set/ad group delivery and bid/optimization.
4. Ad/creative performance.
5. Landing page/app path evidence.

## Comparison

Default comparison:

- Last 7 complete days vs previous 7 complete days for incidents.
- Last 30 days for strategic reallocation.

Calculate:

```text
delta_pct = (current - previous) / previous
```

If previous is zero, report absolute change and avoid percent claims.

## Diagnosis Rules

| Symptom | Likely issue | Next reference |
| --- | --- | --- |
| Spend up, result flat/down | audience saturation, bid/learning, creative fatigue | creative or budget/bid |
| CTR down, CPM up | creative fatigue or weak placement fit | creative-analysis |
| Clicks up, CVR/result down | landing page, store redirect, traffic quality | landing-page-and-funnel |
| High result but low value/ROAS | low-quality conversion or attribution mismatch | measurement-and-attribution |
| Good Meta, weak TikTok or reverse | creative format and user intent mismatch | creative + vertical metrics |

## Vertical Lens

Before ranking winners, load `vertical-metric-playbooks.md` and use the correct metric:

- Ecommerce: purchase/value/ROAS.
- App/W2A: install -> registration/trial/subscribe/purchase.
- Games: install quality, tutorial, retention, purchase/ad monetization.
- Finance: lead -> apply -> credit -> disbursement.
- Search arbitrage: CPC/CTR/outbound click quality and margin proxy.

## Output

Return:

- Scope and coverage.
- KPI table by platform/campaign.
- Winners, watchlist, bleeders.
- Root-cause hypotheses with evidence.
- Recommended next actions and what needs approval.
