# Motata Meta 报告拉数流程

目标：在兼顾速度、完整性和 Meta API 稳定性的前提下，为日报、周报和自定义周期生成可用的报告数据源。

核心约定：

- `--period` 表示时间周期：`daily`、`weekly`、`custom`。
- `--depth` 表示拉数深度：`fast`、`standard`、`full`、`deep`。
- `depth` 不是日报/周报；日报和周报都可以选择不同深度。
- 默认周报建议 `--period weekly --depth standard`。
- 默认日报建议 `--period daily --depth fast` 或 `--period daily --depth standard`。
- 环比结论必须基于两个等长周期；缺少上一周期时，报告必须标记为 single-period。

## 推荐命令

日报快速版：

```bash
motata report meta run \
  --account-id "$ACCOUNT_ID" \
  --access-token "$ACCESS_TOKEN" \
  --period daily \
  --depth fast
```

日报标准版：

```bash
motata report meta run \
  --account-id "$ACCOUNT_ID" \
  --access-token "$ACCESS_TOKEN" \
  --period daily \
  --depth standard
```

周报标准版：

```bash
motata report meta run \
  --account-id "$ACCOUNT_ID" \
  --access-token "$ACCESS_TOKEN" \
  --period weekly \
  --depth standard
```

周报完整复盘：

```bash
motata report meta run \
  --account-id "$ACCOUNT_ID" \
  --access-token "$ACCESS_TOKEN" \
  --period weekly \
  --depth full
```

自定义双周期：

```bash
motata report meta run \
  --account-id "$ACCOUNT_ID" \
  --access-token "$ACCESS_TOKEN" \
  --period custom \
  --since 2026-05-04 \
  --until 2026-05-10 \
  --previous-since 2026-04-27 \
  --previous-until 2026-05-03 \
  --depth standard
```

只查看计划，不请求 Meta API：

```bash
motata report meta run \
  --account-id "$ACCOUNT_ID" \
  --access-token "$ACCESS_TOKEN" \
  --period weekly \
  --depth standard \
  --dry-run
```

## 周期规则

`daily`：

- 当前周期：昨天。
- 上一周期：前天。
- 适合日常监控和轻量日报。

`weekly`：

- 当前周期：最近 7 个完整自然日。
- 上一周期：再往前 7 个自然日。
- 适合默认周报。

`custom`：

- 必须传 `--since` 和 `--until`。
- 如果未传 `--previous-since` 和 `--previous-until`，系统会自动推导等长上一周期。
- 如果传 `--no-compare`，则只拉当前周期。

## 深度规则

### fast

用于速度优先的日报。

数据源：

- `user_type`
- `activities`（覆盖 previous + current 窗口，用于解释操作导致的波动）
- current `account / campaign / ad` insights
- previous `account / campaign` insights
- `audience_country`
- `landing_pages` batch

特点：

- 请求少，速度快。
- 不默认补结构数据。
- `standard/full/deep` 默认补 targeted creative preview；`fast` 默认跳过。
- 适合每天自动跑。

### standard

默认推荐深度，适合大多数日报和周报。

数据源：

- `user_type`
- `apps`
- `activities`（覆盖 previous + current 窗口，用于解释操作导致的波动）
- current `account / campaign / adset / ad` insights
- previous `account / campaign / adset / ad` insights
- `audience_country`
- `audience_age_gender`
- `audience_placement`
- `landing_pages` batch
- top adset/ad/creative structure

特点：

- 双周期完整。
- 关键层级完整。
- 只补最终分析最可能用到的 top 对象结构。
- landing 使用 batch，避免大量逐广告上下文深挖。
- `standard/full/deep` 默认只对最终/top ads 补 creative preview；正式 HTML 报告不要对全账户所有广告深挖 preview。

### full

用于正式周报复盘或客户问数较多的场景。

数据源：

- standard 的全部数据源
- `audience_device`
- landing 使用 full profile
- top structure enrichment 保持开启

特点：

- 比 standard 更完整。
- 仍然不默认全量结构拉取。
- 适合周报复盘、素材诊断、预算重分配前的分析。

### deep

用于排障、审计或一次性深挖。

数据源：

- full 的全部数据源
- 全量 adset/ad/creative structure

特点：

- 最完整，也最慢。
- API 失败面最大。
- 不建议作为日报或默认周报。

## 输出目录结构

默认输出到：

```text
build/report_runs/meta_{ACCOUNT_ID}_{period}_{depth}_{UNTIL}/
```

`motata report meta run` 默认会拉取 `activities` source，覆盖 previous + current 窗口，底层使用 Meta ad account `activities` edge。Activities 只作为解释上下文使用：例如预算调整、启停、广告/广告组/推广系列/素材变更造成的 spend、ROAS、转化量波动；不要把 Activities 当成 KPI 真值。

典型 `weekly + standard` 输出：

```text
build/report_runs/meta_123_weekly_standard_2026-05-12/
├── manifest.json
├── user_type.json
├── apps.json
├── activities.json
├── current_account_insights.json
├── current_campaign_insights.json
├── current_adset_insights.json
├── current_ad_insights.json
├── previous_account_insights.json
├── previous_campaign_insights.json
├── previous_adset_insights.json
├── previous_ad_insights.json
├── audience_breakdown.json
├── audience_country.json
├── audience_age_gender.json
├── audience_placement.json
├── landing_pages.json
├── adset_structure.json
├── ad_structure.json
└── creative_structure.json
```

`manifest.json` 是报告生成层的主要入口，记录：

- platform
- account_id
- period
- depth
- current/previous window
- run_dir
- 每个 source 的状态、路径、行数、重试次数、降级原因

## 限流和降级策略

执行器对每个数据源独立重试：

- 默认 `--retry 2`
- 默认 `--retry-wait 60`
- 失败后写入同名 JSON，内容包含 `status: degraded` 和 error
- 继续拉后续数据源

推荐原则：

- insights 的重型层级默认使用 async。
- breakdown 维度由执行器串行拉取。
- landing batch 默认不做 per-ad deep context。
- preview 在 `standard/full/deep` 默认拉数阶段只做 targeted enrichment，避免 HTML 最终不会展示的广告也消耗请求。

## 报告生成层要求

`motata-report` 使用该 run directory 生成 HTML 时必须：

- 读取 `manifest.json` 判断 coverage。
- 仅在 current/previous 窗口等长且数据源均可用时输出环比结论。
- 对 degraded source 给出简短数据质量说明。
- Campaign、Adset、Ad、Creative 表格使用 current 数据为主，previous 数据只做比较。
- landing 的 Revenue/ROAS 缺失时，使用绑定 ads 的 `action_values` 和 spend 汇总回填。
- Creative preview 只对最终入表 ad/creative 单独补拉。

## 不再推荐的做法

- 不推荐日报默认全量拉 adset/ad/creative structure。
- 不推荐拉所有 ad content previews；默认只补最终/top ads。
- 不推荐用固定 `last_14d` 做周环比结论。
- 不推荐并行跑多个 heavy insights 或多个 breakdown。
- 不推荐把 `--depth` 当成日报/周报含义。
