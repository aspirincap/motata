# Vertical Report Templates

## Purpose

Use this reference when returning a vertical-specific analysis for a resolved user type. Keep the report aligned to what Motata can measure from Meta/TikTok platform data.

Do not claim first-party revenue, profit, LTV, new-customer rate, or true incrementality unless the user supplies those facts separately. When only platform data exists, say "platform-reported" or "proxy".

Default output is an HTML report. Use the section lists below as the report structure, but return the generated HTML file path as the primary artifact. Markdown is a secondary copy only.

## Universal Header

Every vertical report starts with:

```text
Scope: platform(s), account IDs, date range
User type: user-provided | Motata auto-classified, confidence/evidence
Metric set: preset name and active metrics
Coverage: explicit accounts | approved sampling
Limits: platform data only; no first-party customer/order truth in this run
```

Every report must explicitly show the five core metrics: impressions, clicks, spend, conversion, and revenue. If any are missing from the primary insight request, run the relevant full metric probe for that single account. If the probe still finds no active value field, keep the metric in the report and mark it as empty/unsupported instead of omitting it.

Every vertical report with a Creative, video, image, ad creative, or creative-retention table must render one preview image per row when available. Expose playable/preview/video URLs through a hover/focus action on the preview image rather than a standalone Preview URL column. If no asset URL is available, keep the row and mark the preview as `Unavailable`.

## 电商

Sections:

1. Platform KPI: spend, impressions, clicks, CTR, CPC, CPM, result/conversion, cost.
2. Purchase/value proxy: purchase actions, action values, ROAS/value fields when active.
3. Funnel proxy: view content, add to cart, checkout, purchase where supported.
4. Landing/product URL concentration.
5. Creative and placement drivers.
6. Actions: scale, fix landing/offer, refresh creative, reduce waste.

Avoid: claiming new-customer quality, margin, or profit.

TikTok 电商报告里，`onsite_purchases_roas`、`onsite_shopping_roas`、`shop_gross_revenue_by_order_submission`、`onsite_total_add_to_cart`、`onsite_total_checkout_initiation`、`onsite_total_product_details_page_view` 应当出现在价值和漏斗段落的前面。

## 工具 / 工具-W2A

Sections:

1. W2A/app evidence: Adjust/Appsflyer/OneLink/Branch/self-domain-to-store/app metadata.
2. Traffic path: impression -> click -> store/deeplink/app event proxy.
3. App outcomes: install, registration, trial, subscribe, purchase where active.
4. SKAN/SAN availability and delay risk.
5. Creative hook and audience quality.
6. Actions: optimize deeper event, fix redirect/store mismatch, separate web vs app metrics.

Avoid: treating W2A as pure web ecommerce when the path ends at App Store or Google Play.

TikTok 工具/W2A 报告需要把 `onsite_destination_visits`、`onsite_download_start`、`real_time_app_install`、`skan_app_install`、`onsite_form`、`onsite_total_subscribe_value` 单独拿出来，避免只停留在浅层点击。

## 短剧

Sections:

1. Core metric coverage: impressions, clicks, spend, conversion, revenue, with full-probe supplement status.
2. Video hook and retention: 2s/6s/quartiles/completion.
3. TikTok creative retention ranking: top retention creatives, top conversion creatives, high-spend low-retention ads, cheap-click low-retention ads, refresh candidates.
4. Click/store/app path.
5. Install/register/subscribe/purchase proxy.
6. Creative episode/offer angle diagnosis.
7. Actions: opening refresh, audience split, deeper event optimization.

TikTok 短剧报告里，`paid_engaged_view_15s`、`engaged_view_15s`、`onsite_subscribe_value_day0`、`onsite_subscribe_value_day1`、`onsite_subscribe_value_day6`、`onsite_total_subscribe_value` 要作为订阅价值链主视图。

## 休闲游戏

Sections:

1. CPI/result efficiency.
2. Tutorial, retention, in-app ad, purchase proxies when active.
3. Creative hook and gameplay clarity.
4. Geo/device/placement quality.
5. Actions: avoid cheap install traps; optimize toward quality event where available.

TikTok 休闲游戏报告应优先展示 `real_time_app_install`、`skan_app_install`、`complete_tutorial`、`day7_retention`、`in_app_ad_impr`、`in_app_ad_click`、`total_purchase_value`。

## 中重度游戏

Sections:

1. Install and registration baseline.
2. Role creation, level, achievement, retention, purchase proxies when active.
3. Creative/audience fit by genre promise.
4. Actions: prioritize downstream quality over CPI.

TikTok 中重度游戏报告应优先展示 `real_time_app_install`、`skan_app_install`、`create_gamerole`、`achieve_level`、`unlock_achievement`、`day7_retention`、`total_purchase_value`。

## 金融借贷

Sections:

1. Lead/application baseline.
2. Apply -> credit -> disbursement proxy where active.
3. Geo/device/placement and compliance-safe traffic quality.
4. Actions: reduce cheap unqualified leads; optimize toward deeper qualified event.

TikTok 金融借贷报告需要把 `form`、`onsite_form`、`button_click`、`messaging_total_conversation_tiktok_direct_message`、`loan_apply`、`loan_credit`、`loan_disbursement`、`sales_lead` 放到主分析段。

## 小说

Sections:

1. Video/story hook.
2. Install/register/start trial/subscribe/purchase proxy.
3. Creative premise and audience resonance.
4. Actions: improve hook and optimize toward subscription/purchase when available.

TikTok 小说报告应优先展示 `paid_engaged_view_15s`、`engaged_view_15s`、`onsite_subscribe_value_day0`、`onsite_subscribe_value_day1`、`onsite_subscribe_value_day6`、`total_subscribe_value`、`day7_retention`。

## 泛娱乐

Sections:

1. Attention and engagement.
2. Install/register/login/subscribe/purchase proxy.
3. Placement and creative resonance.
4. Actions: separate cheap engagement from meaningful activation.

TikTok 泛娱乐报告需要把 `live_views`、`live_effective_views`、`messaging_total_conversation_tiktok_direct_message`、`follows`、`profile_visits`、`engaged_view_15s` 作为核心段落。

## 搜索套利

Sections:

1. CTR, CPC, CPM, outbound/landing click proxy.
2. Device/placement/geography cost mix.
3. Conversion/value proxy if active.
4. Actions: isolate low-quality cheap clicks; maintain margin discipline when external margin is provided.

TikTok 搜索套利报告应优先展示 `button_click`、`clicks`、`search`、`ctr`、`cpc`、`placement_type`、`onsite_destination_visits`。

## 社交

Sections:

1. Install/register/login baseline.
2. Subscribe/purchase/custom event proxy where active.
3. Audience quality and creative social proof.
4. Actions: optimize toward activation, not only install.

TikTok 社交报告需要把 `live_views`、`live_effective_views`、`messaging_total_conversation_tiktok_direct_message`、`follows`、`profile_visits`、`engaged_view_15s`、`likes`、`comments`、`shares` 放进主叙述。

## 代理商/多类型

Sections:

1. Per-account user type table.
2. Core cross-account health: spend, clicks, result, cost.
3. Vertical-specific findings by account group.
4. Coverage and fallback notes.

Rule: do not rank all accounts by a single CPA/ROAS when user types differ.

TikTok 代理商/多类型报告还应把 `campaign_automation_type`、`placement_type`、`billing_event`、`cash_spend`、`voucher_spend` 作为账号分层字段。
