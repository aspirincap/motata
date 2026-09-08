# Meta User Type Classification

Use this workflow when a user asks to infer the advertiser/user vertical from a Meta or TikTok access token, top-spend accounts/advertisers, top campaigns, landing pages, or app-store evidence.

## Meta Command

```bash
motata meta user-type analyze \
  --access-token "$META_ACCESS_TOKEN" \
  --date-preset last_14d \
  --account-limit 10 \
  --campaign-limit 10 \
  --ad-limit 5 \
  --content-limit 60
```

Aliases: `customer-type`, `account-type`, `classify-user`.

## TikTok Command

```bash
motata tiktok user-type analyze \
  --access-token "$TIKTOK_ACCESS_TOKEN" \
  --advertiser-limit 10 \
  --campaign-limit 10 \
  --ad-limit 5 \
  --content-limit 60
```

Pass `--advertiser-id <id>` to analyze specific advertisers. Repeat it for multiple advertisers.
Add `--smart-plus` when landing URL resolution needs SmartPlus ad detail lookup.

## What It Does

1. Lists accessible Meta ad accounts or TikTok advertisers and ranks the top accounts/advertisers by spend where supported.
2. Pulls the top `--campaign-limit` spend campaigns per account/advertiser.
3. Samples top ads per campaign to resolve landing URLs via creative/story fields on Meta or ad/adgroup fields on TikTok.
4. Reuses app discovery to collect app IDs, app names, and App Store / Google Play URLs.
5. Scrapes landing pages and app-store pages up to `--content-limit`.
6. Scores these candidate types and returns the top 3 with an index:
   `短剧`, `电商`, `工具`, `赌博`, `代理商/多类型`, `休闲游戏`, `社交`, `小说`, `中重度游戏`, `搜索套利`, `泛娱乐`, `网赚`, `金融借贷`.

## Output Contract

Return these fields to the user:

- `top_types`: the three most likely types with `index` and `raw_score`
- `accounts` or `advertisers`: sampled top-spend accounts/advertisers
- `campaigns`: sampled top-spend campaigns with detected `landing_urls`, `store_urls`, and `app_names`
- `scraped_content_count`: count of landing/app-store pages read
- `errors`: permission, rate-limit, story-lookup, or scrape failures

Use `--include-evidence` when the user asks why a type was selected. It includes matched text snippets and scraped content details. Do not paste full scraped page text; summarize the relevant evidence.

## Safety

- Treat access tokens as secrets. Do not echo tokens in responses, logs, handoff notes, or committed files.
- This command is read-only against Meta, but it may perform external HTTP reads of landing pages and app-store URLs.
- If Meta returns rate limits, report partial results and the failed scope from `errors`.
