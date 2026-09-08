# Meta Playbooks

Use these playbooks for common Meta tasks.

## Discovery First

Start here for unfamiliar accounts:

1. `motata meta assets discover`
2. `motata meta assets promotable-pages`
3. `motata meta assets pixels`
4. `motata meta campaigns list`

Use discovery before create or update flows so page, pixel, and promotable object assumptions are real.

## Typical Read Flows

- list campaigns
- get campaign, adset, ad, or creative
- get insights
- analyze landing-page/product-level spend
- search targeting

Common command groups:

- `meta campaigns`
- `meta adsets`
- `meta ads`
- `meta creatives`
- `meta insights`
- `meta landing-pages`
- `meta apps`
- `meta targeting`

## Landing Page And Product Spend

Use `motata meta landing-pages analyze` when the user asks to extract Meta ad landing-page URLs, rank ads by spend and URL, or aggregate performance by product/landing page.

Recommended examples:

```bash
motata meta landing-pages analyze \
  --access-token "$META_ACCESS_TOKEN" \
  --since 2026-04-24 \
  --until 2026-05-07
```

```bash
motata meta landing-pages analyze \
  --account-id 123456789 \
  --access-token "$META_ACCESS_TOKEN" \
  --date-preset last_14d \
  --top 50 \
  --include-ads
```

Output groups ad-level insights by normalized landing URL, filters Meta/fbcdn/Instagram asset URLs, extracts landing URLs from ad creatives and post/story fallbacks, deduplicates Meta purchase action aliases, and reports unresolved ads with structural hints such as `object_story_id`, `Facebook.com/{page_id}_{post_id}`, creative shape, adset promoted object, and tracking post IDs. When available, the command automatically uses Page access tokens from `/me/accounts` to resolve post/story `call_to_action.value.link` URLs that user tokens often cannot read directly.

Post/story lookups are cached by `page_id_post_id`, because ads pointing at the same Page post share the same landing page. This avoids repeated Graph calls for reused posts and speeds up accounts with many ads attached to the same creative post.

URL-only reporting is the default. Use `--product` to enrich each landing page with `motata product` scrape signals such as product name and price, and `--product-limit` to cap product page scraping.

## Active App Analysis

Use `motata meta apps analyze` to discover active promoted apps from campaign, adset, ad, and creative attributes.

```bash
motata meta apps analyze \
  --access-token "$META_ACCESS_TOKEN" \
  --account-limit 2 \
  --campaign-limit 20 \
  --date-preset last_14d
```

The command first selects top-spend ad accounts, then top-spend campaigns, and groups spend by app using adset `promoted_object.application_id`, `promoted_object.object_store_url`, campaign objective, and ad creative app/store URL hints.

Because app campaigns and URL campaigns are usually mutually exclusive, the app analyzer first checks campaign/adset promoted-object evidence. When an app is already identified at that level, it skips deeper ad/creative probing and reports `ad_probe_skipped_campaign_count`; use the URL analyzer for the remaining website-style campaigns.

## Validation Flows

Use before writes when possible:

- `motata meta validate creative`
- `motata meta validate ad-link`
- `motata meta validate promoted-object`

## Create Or Update Flow

Recommended sequence:

1. discover assets
2. validate promotable objects
3. create or update campaigns, adsets, creatives, and ads
4. verify by listing or getting the created objects

## Migration And Debug

When the user wants cross-account recreation or ID remapping:

- `motata meta migrate export`
- `motata meta migrate plan`
- `motata meta migrate run`
- `motata meta migrate status`
- `motata meta migrate resume`

When the user wants raw Graph API failure detail:

- `motata meta debug graph`

## Notes

- `motata` is intended to expose detailed Meta failure payloads rather than hide them.
- For large or uncertain write flows, run `--help` first and summarize the intended mutation before execution.
