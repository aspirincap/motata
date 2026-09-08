# Vertical Metric Playbooks

## User Type Selection

Supported user types:

- 电商
- 工具
- 工具/W2A
- 短剧
- 休闲游戏
- 中重度游戏
- 金融借贷
- 小说
- 泛娱乐
- 搜索套利
- 社交
- 代理商/多类型

When type is unknown and explicit account/ad account/advertiser IDs are present, run `user-type analyze` silently and report the classification source. If account IDs are missing, ask for scope first. When W2A evidence exists, map web tracking domains that end at app stores to App/W2A.

## Universal Core

Use for every vertical:

| Bucket | Meta | TikTok |
| --- | --- | --- |
| Core spend | `spend`, `impressions`, `clicks`, `ctr`, `cpc`, `cpm`, `reach`, `frequency` | `spend`, `impressions`, `clicks`, `ctr`, `cpc`, `cpm`, `reach`, `frequency` |
| Result | `results`, `cost_per_result`, `result_rate`, `actions`, `cost_per_action_type` | `result`, `cost_per_result`, `result_rate`, `conversion`, `cost_per_conversion`, `conversion_rate_v2` |
| Structure | `account_id`, `campaign_id`, `adset_id`, `ad_id`, names, `objective`, `optimization_goal` | `advertiser_id`, `campaign_id`, `adgroup_id`, `ad_id`, names, `objective_type`, `smart_target`, `billing_event` |
| Placement | `publisher_platform`, `platform_position`, `device_platform`, `country`, `age`, `gender` | placement, country/region when available, device and app/web dimensions when supported |
| Creative/video | video quartiles, ThruPlay, avg watch time, outbound/inline clicks | video plays, 2s/6s views, p25/p50/p75/p100, engaged view |

## 电商

Goal: purchase value, catalog/product quality, ROAS, landing-page funnel.

| Bucket | Meta | TikTok |
| --- | --- | --- |
| Conversion | `actions:purchase`, `actions:add_to_cart`, `actions:initiate_checkout`, `actions:view_content`, `outbound_clicks`, `landing_page_view_per_link_click` | `complete_payment`, `total_complete_payment`, `add_to_cart`, `total_add_to_cart`, `view_content`, `checkout` |
| Value/ROAS | `action_values`, `purchase_roas`, `website_purchase_roas`, `conversion_values` | purchase value metrics, `total_purchase_value`, `total_checkout_value`, `total_view_content_value` |
| Vertical specific | product/catalog metrics, product breakdowns, website purchase | web event purchase, catalog/DPA indicators if present |
| Analysis focus | ROAS vs CPA, ATC->purchase leakage, product/landing page concentration | result quality, value, product page traffic, TikTok video hook-to-purchase |

TikTok 电商现在优先看 `onsite_purchases_roas`、`onsite_shopping_roas`、`shop_gross_revenue_by_order_submission`、`onsite_total_purchase`、`onsite_total_add_to_cart`、`onsite_total_checkout_initiation`、`onsite_total_product_details_page_view`，再把 `complete_payment` 作为兼容回退项。

## 工具 / 工具-W2A

Goal: install, activation, subscription, trial, app store redirect quality.

| Bucket | Meta | TikTok |
| --- | --- | --- |
| Conversion | `mobile_app_install`, `app_store_clicks`, `deeplink_clicks`, `start_trial_actions`, `subscribe_actions` | `app_install`, `real_time_app_install`, `start_trial`, `subscribe`, `registration`, `login` |
| Value/ROAS | `mobile_app_purchase_roas`, subscription values, action values | `total_subscribe_value`, `total_purchase_value`, app event values |
| Vertical specific | app install, trial, subscribe, app purchase, SKAN postbacks | SAN app events, SKAN install/registration/purchase/subscribe |
| Analysis focus | W2A redirect evidence, store click to install proxy, trial/subscribe CPA | Adjust/Appsflyer/self-domain-to-store path, SKAN delay, SAN availability |

TikTok 工具/W2A 的推荐顺序现在优先 `onsite_destination_visits`、`onsite_download_start`、`real_time_app_install`、`skan_app_install`、`app_install`、`start_trial`、`subscribe`，并保留 `onsite_form`、`onsite_total_subscribe_value` 作为补充路径。

## 短剧

Goal: app install, registration, subscribe/purchase, content engagement.

| Bucket | Meta | TikTok |
| --- | --- | --- |
| Conversion | app install, subscribe, purchase, trial, registration actions | `app_install`, `registration`, `subscribe`, `purchase`, `total_purchase_value` |
| Creative/video | video p25/p50/p75/p100, 2s/6s/ThruPlay, avg watch time | video 2s/6s, p25/p50/p75/p100, engaged view |
| Vertical specific | app events and SKAN postbacks | SAN/SKAN app event groups, subscribe/purchase |
| Analysis focus | hook retention, episode/offer CTR, install-to-subscribe quality | short-video completion and paid conversion quality |

TikTok 短剧现在优先 `paid_engaged_view_15s`、`paid_engagement_engaged_view_15s`、`engaged_view_15s`，并把 `onsite_subscribe_value_day0`、`onsite_subscribe_value_day1`、`onsite_subscribe_value_day6`、`onsite_total_subscribe_value` 放在订阅价值链的前排。

For TikTok 短剧, add `tiktok creative-retention report` as the creative layer. It must keep the five core metrics visible and use full probe supplementation when revenue or conversion fields are absent.

## 休闲游戏

Goal: install volume, registration/tutorial, day retention, ad monetization.

| Bucket | Meta | TikTok |
| --- | --- | --- |
| Conversion | mobile app install, registration, purchase, app events | `app_install`, `registration`, `complete_tutorial`, `day7_retention`, `in_app_ad_impr`, `in_app_ad_click` |
| Value/ROAS | mobile app purchase ROAS, action values | purchase value, in-app ad value, custom app event value |
| Creative/video | video hook and completion | video 2s/6s/quartiles |
| Analysis focus | CPI vs retained/monetized users, creative hook | tutorial/retention/ad-impression proxies |

TikTok 休闲游戏的推荐顺序现在优先 `real_time_app_install`、`skan_app_install`、`app_install`、`complete_tutorial`、`day7_retention`、`achieve_level`，并保留 `in_app_ad_impr`、`in_app_ad_click`、`total_purchase_value` 作为变现层。

## 中重度游戏

Goal: quality install, role creation, level, purchase, retention.

| Bucket | Meta | TikTok |
| --- | --- | --- |
| Conversion | app install, registration, purchase, custom app events | `create_gamerole`, `achieve_level`, `unlock_achievement`, `purchase`, `day7_retention` |
| Value/ROAS | mobile app purchase ROAS, action values | purchase value, level/achievement values, custom app event value |
| Vertical specific | deep app events where available | game-specific SAN events |
| Analysis focus | downstream quality over CPI, creative/audience by role/level proxy |

TikTok 中重度游戏的优先顺序是 `real_time_app_install`、`skan_app_install`、`create_gamerole`、`achieve_level`、`unlock_achievement`、`day7_retention`，再往下看 `purchase`、`total_purchase_value` 和 `custom_app_events_value`。

## 金融借贷

Goal: lead/apply/credit/disbursement quality, not cheap clicks.

| Bucket | Meta | TikTok |
| --- | --- | --- |
| Conversion | lead, submit application, contact, app install if app path | `loan_apply`, `loan_credit`, `loan_disbursement`, `sales_lead`, `registration` |
| Value | qualified lead/application value when available | loan approval/disbursement counts and costs |
| Vertical specific | lead quality and application funnel | loan event chain |
| Analysis focus | CPL vs approval/disbursement rate, compliance-safe optimization |

TikTok 金融借贷建议优先 `form`、`onsite_form`、`button_click`、`messaging_total_conversation_tiktok_direct_message`、`loan_apply`、`loan_credit`、`loan_disbursement`、`sales_lead`，把快点击和真线索分开看。

## 小说

Goal: install/registration/subscribe/purchase/reading engagement proxies.

| Bucket | Meta | TikTok |
| --- | --- | --- |
| Conversion | app install, registration, subscribe, purchase, trial | `app_install`, `registration`, `subscribe`, `purchase`, `start_trial`, custom app events |
| Creative/video | video/story hook metrics | video 2s/6s/quartiles, engaged view |
| Analysis focus | hook quality, install to registration/subscription, value per user |

TikTok 小说优先 `paid_engaged_view_15s`、`engaged_view_15s`、`onsite_subscribe_value_day0`、`onsite_subscribe_value_day1`、`onsite_subscribe_value_day6`、`total_subscribe_value`，再看 `search` 和 `day7_retention`。

## 泛娱乐

Goal: install/registration/engagement/subscription or content monetization.

| Bucket | Meta | TikTok |
| --- | --- | --- |
| Conversion | app install, registration, subscribe, purchase, custom events | `registration`, `subscribe`, `purchase`, `launch_app`, custom app events |
| Creative/video | video engagement, placement | video engagement, social engagement |
| Analysis focus | creative resonance, cheap installs vs meaningful engagement |

TikTok 泛娱乐现在把 `live_views`、`live_effective_views`、`messaging_total_conversation_tiktok_direct_message`、`follows`、`profile_visits`、`engaged_view_15s` 放到前面，适合先看直播和私信承接，再看点赞评论分享。

## 搜索套利

Goal: outbound click quality, landing engagement, revenue proxy if available.

| Bucket | Meta | TikTok |
| --- | --- | --- |
| Conversion | outbound clicks, inline link clicks, website clicks, landing page view | clicks, result, conversion, web events if configured |
| Value | custom conversion/action values if configured | custom web/app event value where available |
| Vertical specific | CTR, CPC, landing-page quality, conversion proxy | click-to-result efficiency |
| Analysis focus | traffic arbitrage margin, placement/device CPC, invalid/low-quality clicks |

TikTok 搜索套利重点看 `button_click`、`clicks`、`search`、`ctr`、`cpc`、`placement_type`，再补 `onsite_destination_visits` 观察点进站后的真实到达质量。

## 社交

Goal: install/registration/login/subscribe, high-quality engagement.

| Bucket | Meta | TikTok |
| --- | --- | --- |
| Conversion | app install, registration, subscribe, login/custom events | `registration`, `login`, `subscribe`, `launch_app`, custom app events |
| Creative/video | social proof, engagement, video retention | engaged view, follows/likes/shares where available |
| Analysis focus | registration CPA, activation proxy, audience quality |

TikTok 社交建议优先 `live_views`、`live_effective_views`、`messaging_total_conversation_tiktok_direct_message`、`follows`、`profile_visits`、`engaged_view_15s`，再把 `likes`、`comments`、`shares` 作为社交扩散层。

## 代理商/多类型

Goal: classify each account/client first, then route to the right vertical.

Rules:

- Do not collapse all clients into one KPI standard.
- Run user-type analysis per account/advertiser when possible.
- Use core metrics for cross-client comparison.
- Use vertical-specific metrics only within the same client/user type.
- Label sampling vs explicit account coverage.

TikTok 代理商/多类型账户还要优先把 `campaign_automation_type`、`placement_type`、`billing_event`、`cash_spend`、`voucher_spend` 放到账号分层和客户对比里。
