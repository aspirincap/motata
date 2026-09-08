# Creative Analysis

## Purpose

Use this reference for creative fatigue, video funnel, placement, format, and audience resonance analysis.

## Required Metrics

Core:

- impressions, clicks, spend, conversion, revenue,
- CTR, CPC, CPM, result/conversion and cost per result/conversion,
- reach/frequency,
- ad name/id and campaign/ad group context.

Creative preview:

- Every Creative/ad row in a report must include an inline preview image when an ad-level or creative-level asset is available.
- Every Creative/ad row must also include a separate preview URL column, using the best available ad preview, creative preview, video URL, image URL, thumbnail URL, cover URL, or media preview URL.
- For video creative, render the thumbnail/cover image inline when available and keep the playable video or preview URL in the separate URL column.
- For Meta, prefer `thumbnail_url`, `image_url`, `object_story_spec.*.image_url`, story attachment media, `instagram_permalink_url`, and object-story permalink/post URL.
- If no preview asset exists or the API cannot return it, show `Unavailable` in the preview cell and `-` in the preview URL column; do not drop the row.

Video:

- Meta: `video_play_actions`, `video_p25_watched_actions`, `video_p50_watched_actions`, `video_p75_watched_actions`, `video_p95_watched_actions`, `video_p100_watched_actions`, `video_avg_time_watched_actions`, `video_30_sec_watched_actions`, ThruPlay when available.
- TikTok: `video_play_actions`, `video_watched_2s`, `video_watched_6s`, `video_views_p25`, `video_views_p50`, `video_views_p75`, `video_views_p100`, `average_video_play`, `engaged_view`.

Placement:

- Meta: `publisher_platform`, `platform_position`, `device_platform`, age/gender/country where needed.
- TikTok: placement/region/device style dimensions when supported.

## Fatigue Signals

| Signal | Interpretation |
| --- | --- |
| Frequency rising while CTR falls | fatigue or audience saturation |
| CPM rising with stable objective | auction pressure or quality loss |
| Video p25 okay but p50/p75 weak | mid-message problem |
| Strong video completion but low clicks | weak CTA or mismatch |
| Strong clicks but weak conversion | landing page/app-store mismatch |

## Video Funnel Calculations

```text
hook_rate = p25 / video_play_actions
mid_retention = p50 / p25
deep_retention = p75 / p50
completion_rate = p100 / video_play_actions
```

For TikTok short videos, also compare 2s and 6s watched rates.

## Short Drama Creative Retention Report

For TikTok 短剧 accounts, use the dedicated read-only report before making creative refresh calls:

```bash
python3 -m motata_cli tiktok creative-retention report \
  --advertiser <ADVERTISER_ID> \
  --start-date YYYY-MM-DD \
  --end-date YYYY-MM-DD \
  --top 20 \
  --json
```

The report ranks ad-level creatives by 2s/6s watch, engaged 15s, p25/p50/p75/p100, conversion-from-play, and cost per retained viewer. If impressions, clicks, spend, conversion, or revenue are missing, it triggers a full metric probe by default and then marks the remaining gap.

## Vertical Interpretation

- 短剧/小说/泛娱乐: hook and early retention are primary; prioritize opening scene and episode promise.
- 游戏: hook plus downstream app event quality; avoid optimizing only for cheap installs.
- 电商: creative must carry product/offer clarity and click-to-purchase quality.
- 金融借贷: creative should optimize for qualified application, not generic lead volume.
- 搜索套利: creative should optimize click quality and margin proxy, not just CTR.

## Output

Return:

- Top creative winners.
- Fatigue candidates.
- Video drop-off diagnosis.
- For 短剧: creative retention curve ranking, high-spend low-retention ads, cheap-click low-retention ads, and refresh candidates.
- Placement/format mismatches.
- Refresh/iteration brief with exact evidence.
- Creative tables with per-row preview image and a separate preview URL column.
