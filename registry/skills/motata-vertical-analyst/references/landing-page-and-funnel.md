# Landing Page And Funnel

## Purpose

Use this reference for landing page performance, app discovery, W2A routing, Adjust/Appsflyer/self-domain redirects, and click-to-conversion quality.

## Motata Entry Points

```bash
python3 -m motata_cli meta landing-pages analyze --help
python3 -m motata_cli meta apps analyze --help
python3 -m motata_cli meta user-type analyze --help
python3 -m motata_cli tiktok landing-pages analyze --help
python3 -m motata_cli tiktok apps analyze --help
python3 -m motata_cli tiktok user-type analyze --help
```

## W2A App Classification

Treat as App/W2A when evidence includes:

- Adjust tracking domains.
- Appsflyer / OneLink tracking domains.
- Branch or similar deferred deep link domains.
- Self-owned landing domain that ultimately redirects to App Store or Google Play.
- Campaign/ad metadata that identifies promoted app IDs or app names.

Do not recommend pure web ecommerce purchase-only metrics for W2A unless web purchase events are actually active.

## TikTok Smart+ / Upgraded Smart+

For TikTok landing analysis, preserve the full URL evidence chain instead of only reporting the final grouped URL:

- `SMART_PLUS`: if normal ad detail has no URL, fall back to Smart+ campaign detail.
- `UPGRADED_SMART_PLUS`: use upgraded Smart+ ad detail and keep both report `ad_id` and Smart+ ad ID.
- `UPGRADED_SMART_PLUS_CREATIVE`: treat report `ad_id` as the creative/material ID and use `ad_id_v2`/`smart_plus_ad_id` as the upgraded Smart+ ad ID when available.
- Keep `url_evidence` by kind: `landing`, `app_store`, `deeplink`, `creative_asset`, or `unknown`.
- Creative asset URLs are not landing pages, but they are useful for diagnosing which素材 carried the path; do not let TikTok CDN URLs pollute landing-page grouping.

## Funnel Interpretation

Platform APIs usually do not expose full on-site funnel. Use proxies:

| Stage | Meta proxy | TikTok proxy |
| --- | --- | --- |
| Impression -> attention | video views, ThruPlay, reach/frequency | video 2s/6s, engaged view |
| Attention -> click | CTR, outbound_clicks, inline_link_clicks | CTR, clicks |
| Click -> landing/store | landing page URL, app store clicks, deeplink clicks | URL/app metadata, app install |
| Store/app -> conversion | app install, registration, trial, subscribe, purchase | install/SAN/SKAN app events |
| Web commerce | view content, add to cart, initiate checkout, purchase | view content, add to cart, checkout, purchase |

## Landing Page Diagnostics

| Symptom | Likely cause | Action |
| --- | --- | --- |
| High CTR, low result | slow page, weak offer, redirect break, app store mismatch | inspect landing URL/app path |
| Good app store click, weak install | store listing or geo/device mismatch | check promoted app and campaign geo |
| High install, weak registration/trial | onboarding or low-quality traffic | optimize deeper event |
| Ecommerce ATC but no purchase | price/shipping/checkout friction | check offer and checkout |

## Output

Return:

- Landing pages and app/store destinations found.
- Smart+/升级版 Smart+ ad-to-creative URL evidence, including creative URL and landing/app URL sources.
- W2A evidence and classification.
- Funnel proxy table.
- Broken/mismatched links or suspicious redirects.
- Metric preset implication: web, app, W2A, or mixed.
