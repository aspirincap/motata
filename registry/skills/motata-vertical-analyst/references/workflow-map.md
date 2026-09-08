# Workflow Map

## Capability Map

| User intent | First reference | Motata capability |
| --- | --- | --- |
| "今天怎么样", "上周表现", "日报", "周报" | `daily-weekly-reports.md` | platform insights, batch profile, comparison windows |
| "账户是什么类型", "适合看什么指标" | `vertical-metric-playbooks.md` | `meta/tiktok user-type analyze`, `metrics presets recommend` |
| "哪些指标有数据", "全量指标探针" | `metric-probe-and-presets.md` | `meta/tiktok metrics probe` |
| "上周表现", "为什么掉量", "Meta/TikTok 复盘" | `platform-performance.md` | platform insights, campaign/adgroup/ad breakdowns |
| "受众", "定向", "版位/设备/地区质量" | `audience-optimization.md` | `meta/tiktok audience breakdown` for country, age/gender, placement, and device |
| "素材疲劳", "视频留存", "版位表现" | `creative-analysis.md` | ad-level insights, video metrics, placement breakdowns |
| "落地页", "Adjust/Appsflyer", "最终跳应用商店" | `landing-page-and-funnel.md` | landing-pages/apps/user-type analysis |
| "预算怎么调", "出价策略" | `budget-and-bid-optimization.md` | insights + safe write rules through motata-ad-ops |
| "Meta 和 TikTok 数据不一致", "SKAN", "SAN" | `measurement-and-attribution.md` | attribution windows, SKAN/SAN metrics, probe evidence |
| "按垂类出报告", "电商/W2A/游戏/金融报告" | `vertical-report-templates.md` | vertical-specific output templates |
| "限流", "失败", "降级", "empty/unsupported" | `error-cache-degradation.md` | partial coverage and fallback reporting |

## Step 0: Account And Classification Gate

Run this before any substantive Motata analysis:

1. Confirm explicit Meta ad account IDs or TikTok advertiser IDs.
2. Confirm user type/classification if the user knows it.
3. If account IDs are present but user type is missing, run `user-type analyze` silently and report the classification source.
4. If account IDs are missing, ask for the account scope. Do not run recent-spend sampling unless the user explicitly approves sampling.
5. For multi-account or agency work, classify each account independently.

## Default Sequence

1. Define scope: platform, account IDs, date range, and whether the user explicitly approved sampling.
2. Identify user type through the Step 0 gate.
3. Recommend metrics for the type:
   - Run `motata metrics presets recommend`.
   - Add `--w2a` when app-store redirect evidence exists.
4. Fetch only the required data for the question.
5. Escalate to metric probe only when a recommended metric is missing, empty, or likely gated. Use `--profile batch` for multi-account runs and `--profile full` only for connector QA.

## Coverage Rules

- Explicit account IDs mean account-specific analysis.
- Missing account IDs means ask for account scope first.
- Recent-spend sampling is allowed only after user approval; always label it as sampling.
- Probe result `unsupported` means the API rejected the field or combination, not that the business has no such event.
- Probe result `supported_empty` means the field can return but was zero/empty in the selected window.
- Capability Map batch results should distinguish token/access failures from platform-report failures; do not count an empty token as TikTok/Meta API instability.
- Meta `Application request limit reached` in a batch run should trigger low-request fallback, not mark the account unsupported. Retry with batch profiles and schedule full landing/placement/W2A follow-up only for affected accounts.
- Platform-only reports cannot claim first-party customer/order truth, profit, LTV, or true incrementality unless provided separately by the user.

## Date Defaults

| Task | Default |
| --- | --- |
| Incident / anomaly | Last 7 complete days vs previous 7 complete days |
| Creative fatigue | Last 14 complete days, compare to previous 14 |
| User type classification | Last 30 days |
| Metric probe | Last 7 complete days |
| Budget allocation | Last 14 or 30 days depending spend volume |

## Escalation Logic

- Performance issue + high frequency or CTR drop -> creative analysis.
- Geo/device/placement/audience mix issue -> audience optimization.
- High clicks but weak result -> landing page/funnel analysis.
- Good platform conversion but weak app/web evidence -> measurement attribution analysis.
- W2A detected -> use App/W2A presets even when the first landing URL is a web tracking URL.
