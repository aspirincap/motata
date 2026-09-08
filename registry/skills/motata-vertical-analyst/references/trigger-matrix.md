# Trigger Matrix

## Purpose

Use this matrix to route Motata analysis requests to the right reference. Before any analysis route, enforce the account and classification gate.

## Account And Classification Gate Triggers

Any Motata request involving analysis, review, Capability Map, metric presets, landing/app diagnosis, audience, creative, budget, bid, or measurement must resolve:

- account/ad account/advertiser IDs;
- user type/classification.

If account IDs are missing, ask for them. If account IDs are present but user type is missing, silently run `user-type analyze` and report the classification source.

## Intent Routing

| Intent | Example triggers | Reference |
| --- | --- | --- |
| Daily pulse | "今天怎么样", "昨天投放", "daily pulse", "pacing", "日常监控", "今日风险" | `daily-weekly-reports.md` |
| Weekly report | "周报", "上周表现", "weekly report", "环比", "复盘这周" | `daily-weekly-reports.md` |
| User type / metrics | "账户类型", "适合看什么指标", "指标预设", "user type", "metric preset" | `vertical-metric-playbooks.md`, `metric-probe-and-presets.md` |
| Metric probe | "哪些指标有数据", "全量指标探针", "probe metrics", "capability map" | `metric-probe-and-presets.md` |
| Platform performance | "为什么掉量", "表现如何", "复盘", "Meta/TikTok 深挖", "campaign performance" | `platform-performance.md` |
| Audience | "受众", "定向", "人群", "placement", "版位", "geo", "device", "audience quality" | `audience-optimization.md` |
| Creative/video | "素材疲劳", "视频留存", "hook", "CTR下降", "完播率", "creative" | `creative-analysis.md` |
| Landing/W2A | "落地页", "Adjust", "Appsflyer", "OneLink", "跳商店", "app path", "W2A" | `landing-page-and-funnel.md` |
| Budget/bid | "预算怎么调", "加预算", "暂停", "出价", "cost cap", "bid" | `budget-and-bid-optimization.md` |
| Measurement | "数据不一致", "归因", "SKAN", "SAN", "modeled", "attribution window" | `measurement-and-attribution.md` |
| Vertical report | "按垂类出报告", "电商报告", "W2A报告", "游戏报告", "金融账户分析" | `vertical-report-templates.md` |
| Formal HTML report | "生成报告", "重新生成报告", "HTML报告", "最终报告", "客户报告", "report", "weekly report" | `$motata-report` |
| Failure/fallback | "限流", "失败", "unsupported", "empty metric", "rate limit", "降级" | `error-cache-degradation.md` |

## Multi-Intent Rules

- Weekly report with creative issue: start `daily-weekly-reports.md`, then load `creative-analysis.md`.
- Budget request without stable vertical metric: load `metric-probe-and-presets.md` before budget recommendation.
- W2A evidence overrides web-looking landing URLs: use App/W2A template.
- Agency or multi-account request: classify each account separately before any vertical-specific ranking.
