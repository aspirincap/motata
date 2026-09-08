# TikTok Playbooks

Use these playbooks for TikTok advertiser operations through `motata`.

## Discovery First

For unfamiliar advertisers, start with:

1. `motata tiktok accounts list` or `info`
2. `motata tiktok assets discover`
3. `motata tiktok identities list`
4. `motata tiktok campaigns list`
5. `motata tiktok adgroups list`
6. `motata tiktok ads list`

## Common Read Flows

Primary command groups:

- `tiktok accounts`
- `tiktok campaigns`
- `tiktok smartplus-campaigns`
- `tiktok adgroups`
- `tiktok smartplus-adgroups`
- `tiktok ads`
- `tiktok smartplus-ads`
- `tiktok identities`
- `tiktok creative-assets`
- `tiktok assets`
- `tiktok items`
- `tiktok targeting`
- `tiktok insights`
- `tiktok landing-pages`

## Validation Flows

Use before create or update when possible:

- `motata tiktok validate creative`
- `motata tiktok validate ad-link`
- `motata tiktok validate promoted-object`

This is especially important for website conversions, identity-dependent creative flows, and SmartPlus routes.

## Safe Write Coverage

This repository has already validated many TikTok read commands and a small set of safe status writes.

Use the audit mindset:

1. prefer read commands first
2. use validation before create
3. use status updates only when the target object is confirmed valid

Known practical surfaces:

- campaign status updates can work as low-risk write checks
- adgroup status updates can work as low-risk write checks
- ad status updates may fail on invalid ad samples even when the command itself is registered

## Reporting

Use:

- `motata tiktok insights get`
- `motata tiktok insights smartplus-overview`
- `motata tiktok insights smartplus-breakdown`

Choose scope and date range explicitly in the result summary.

## Public Post Item Resolution

Use `motata tiktok items resolve` when the user has TikTok item IDs or video URLs and needs public post metadata without an advertiser token.

The resolver constructs `https://www.tiktok.com/@motata/video/{item_id}`, reads TikTok's public SSR payload, and returns the real handle name, `@username`, avatar URL, preview image URL, real post URL, author IDs, upload time, and nullable post modification time. TikTok usually exposes post `createTime`, but not a public post edit/update timestamp; `modify_timestamp` and `modify_datetime` should remain `null` unless TikTok returns a post-level modify/update field.

Examples:

```bash
motata tiktok items resolve 7626105681661152532 --json

motata tiktok items resolve \
  7640078700750310669 7626105681661152532 \
  --workers 8 \
  --include-timing \
  --download-dir build/tiktok-item-assets \
  --out build/tiktok-items.json
```

For batches around 40 items, prefer `--workers 8`; higher concurrency gives limited extra speed and may increase TikTok throttling risk. Use `--download-dir` only when avatar/preview files are needed.

## Landing Page Analysis

Use `motata tiktok landing-pages analyze` to mirror the Meta landing-page spend workflow for TikTok.

Default behavior is URL-only and skips product scraping. Add `--product` only when product enrichment is explicitly needed.

Examples:

```bash
motata tiktok landing-pages analyze \
  --advertiser-id 7575414006173696001 \
  --advertiser-id 7629292561567318034 \
  --advertiser-id 7630372984217436178 \
  --access-token "$TIKTOK_ACCESS_TOKEN" \
  --since 2026-04-26 \
  --until 2026-05-09
```

When only a user token is known and the matching TikTok app credentials are available, pass `--app-id` and `--secret` to discover authorized advertisers before analysis.

The command groups spend, purchase revenue, ROAS, and complete-payment counts by canonical landing URL, returns the largest unresolved ads under `no_url`, and caches repeated adgroup lookups so shared structures are queried once.

For very large advertisers, use `--ad-limit` to analyze only top spend ads first, then remove the limit for a full run if needed.

TikTok campaign type matters for ad details:

- Manual campaigns use `/ad/get/`; prefer `ad_ids_v2` when available.
- Legacy Smart+ campaigns use `/campaign/spc/get/` for Smart+ campaign and creative details when `/ad/get/` does not expose a landing page.
- Upgraded Smart+ campaigns should use `/smart_plus/ad/get/` when a `smart_plus_ad_id` or upgraded automation type is available; landing pages often appear in `landing_page_url_list`.

## Active App Analysis

Use `motata tiktok apps analyze` to discover active promoted apps from campaign, adgroup, and ad attributes.

Examples:

```bash
motata tiktok apps analyze \
  --access-token "$TIKTOK_ACCESS_TOKEN" \
  --advertiser-limit 2 \
  --campaign-limit 20 \
  --since 2026-04-26 \
  --until 2026-05-09
```

If no advertiser ID is supplied, discover operable advertisers by calling `open_api/v1.3/bc/get/` and then `/open_api/v1.3/bc/asset/get/?asset_type=ADVERTISER` for each BC. Use `asset_id` as the advertiser ID; this avoids BC-report-visible accounts that fail account-level calls with "No permission to operate advertiser".

The command groups spend by app using `app_id`, `app_name`, `app_download_url`, `promotion_type`, and campaign `objective_type`.

Because app campaigns and URL campaigns are usually mutually exclusive, the app analyzer first checks campaign/adgroup app evidence. When an app is already identified at that level, it skips deeper ad probing and reports `ad_probe_skipped_campaign_count`; use the URL analyzer for website-style campaigns.

## Exclusions

Do not default into AIGC or copy flows in this skill. Only enter those areas if the user explicitly requests them.
