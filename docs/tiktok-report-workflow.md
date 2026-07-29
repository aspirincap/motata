# Motata TikTok 报告拉数流程

目标：让 TikTok 和 Meta 使用同一套 `period + depth` 报告拉数风格，在速度、完整性和素材预览效果之间保持可控取舍。

核心约定：

- `--period` 表示时间周期：`daily`、`weekly`、`custom`。
- `--depth` 表示拉数深度：`fast`、`standard`、`full`、`deep`。
- `depth` 不是日报/周报；日报和周报都可以选择不同深度。
- 默认周报建议 `--period weekly --depth standard`。
- 默认日报建议 `--period daily --depth fast` 或 `--period daily --depth standard`。
- 环比结论必须基于两个等长周期；缺少上一周期时，报告必须标记为 single-period。
- TikTok creative-retention 和素材预览只针对最终候选 top ads，不做账户全量扫描。

## 推荐命令

日报快速版：

```bash
motata report tiktok run \
  --advertiser-id "$ADVERTISER_ID" \
  --access-token "$TIKTOK_ACCESS_TOKEN" \
  --period daily \
  --depth fast
```

日报标准版：

```bash
motata report tiktok run \
  --advertiser-id "$ADVERTISER_ID" \
  --access-token "$TIKTOK_ACCESS_TOKEN" \
  --period daily \
  --depth standard
```

周报标准版：

```bash
motata report tiktok run \
  --advertiser-id "$ADVERTISER_ID" \
  --access-token "$TIKTOK_ACCESS_TOKEN" \
  --period weekly \
  --depth standard
```

周报完整复盘：

```bash
motata report tiktok run \
  --advertiser-id "$ADVERTISER_ID" \
  --access-token "$TIKTOK_ACCESS_TOKEN" \
  --period weekly \
  --depth full
```

自定义双周期：

```bash
motata report tiktok run \
  --advertiser-id "$ADVERTISER_ID" \
  --access-token "$TIKTOK_ACCESS_TOKEN" \
  --period custom \
  --since 2026-05-04 \
  --until 2026-05-10 \
  --previous-since 2026-04-27 \
  --previous-until 2026-05-03 \
  --depth standard
```

只查看计划，不请求 TikTok API：

```bash
motata report tiktok run \
  --advertiser-id "$ADVERTISER_ID" \
  --access-token "$TIKTOK_ACCESS_TOKEN" \
  --period weekly \
  --depth standard \
  --dry-run
```

`standard`、`full`、`deep` 深度默认会在最终/top ads 确定后补 targeted creative-retention / preview enrichment。若为了速度要跳过素材预览：

```bash
motata report tiktok run \
  --advertiser-id "$ADVERTISER_ID" \
  --access-token "$TIKTOK_ACCESS_TOKEN" \
  --period weekly \
  --depth standard \
  --no-previews
```

不要为了“完整”全量拉 creative-retention。正式 HTML 报告应先根据 insight、landing、audience 数据确定最终入表广告，再只对这些 top ads 做 targeted creative-retention / preview enrichment。

## 推荐输入

外部 agent 调用时，至少提供：

- `platform`: `tiktok`
- `advertiser_id`: TikTok 广告主 ID
- `access_token`: TikTok access token，或可通过 `motata-token` 获取
- `period`: `daily`、`weekly` 或 `custom`
- `depth`: `fast`、`standard`、`full` 或 `deep`
- `since` / `until`: 仅 `period=custom` 必填
- `previous_since` / `previous_until`: 自定义双周期时建议显式传入；未传时会自动推导等长上一周期
- `timezone`: 若最终报告展示业务时区，传给报告层保留上下文
- `known_user_type`: 如果用户已确认垂类，传给报告层；未知时使用 `user_type` source 判定

## 推荐输出

`motata report tiktok run` 的直接产物不是最终 HTML，而是报告数据源目录：

```text
build/report_runs/tiktok_{ADVERTISER_ID}_{period}_{depth}_{UNTIL}/
```

外部 agent 最终应该返回：

- `html_path`: `$motata-report` 生成的 HTML 文件路径
- `run_dir`: 本次 `motata report tiktok run` 的输出目录
- `manifest_path`: `run_dir/manifest.json`
- `period`: 当前周期和上一周期
- `depth`: 拉数深度
- `coverage`: full / batch / partial / degraded
- `degraded_sources`: 失败或降级的数据源列表
- `topline`: spend、revenue、ROAS、purchase/result、CPA/CVR 的摘要
- `notes`: 只写必要的数据口径说明，不把内部执行细节塞进 HTML

## Activities / Changelog

`motata report tiktok run` 默认会拉取 `activities` source，覆盖 previous + current 窗口。TikTok 使用 `/open_api/v1.3/changelog/task/create/` 创建 changelog task，并在短时间内轮询 task 状态；若任务尚未 ready，会把 `task_id`、`task_status`、`checks` 写入 source，报告主流程不被阻塞。

Activities 只作为解释上下文使用：例如预算调整、启停、广告/广告组/推广系列变更造成的 spend、ROAS、转化量波动；不要把 changelog 当成 KPI 真值。

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
- current `advertiser / campaign / ad` insights
- previous `advertiser / campaign` insights
- `audience_country`
- `landing_pages`

特点：

- 请求少，速度快。
- 不默认补结构数据。
- 不默认跑 creative-retention。
- 适合每天自动跑。

### standard

默认推荐深度，适合大多数日报和周报。

数据源：

- `user_type`
- `apps`
- current `advertiser / campaign / adgroup / ad` insights
- previous `advertiser / campaign / adgroup / ad` insights
- `audience_country`
- `audience_age_gender`
- `audience_placement`
- `landing_pages`
- top campaign/adgroup/ad structure
- `targeted_creative_retention`

特点：

- 双周期完整。
- 关键层级完整。
- creative-retention 只针对 current ad insights 选出的 top ads。
- `standard/full/deep` 默认只对最终/top ads 补 preview URL；`fast` 默认跳过，可用 `--include-previews` 显式开启。
- TikTok Smart+ ID 路由遵循最新 v1.3 规则：`ad_id` 用于 creative 级报表和 `/ad/get/`；`current_ad_v2_insights` 使用 `dimensions=["ad_id_v2"]` 专门服务 landing/asset 分析；`ad_id_v2` 不和 `ad_id` 混用；`smart_plus_ad_id` 等价于 `ad_id_v2`，用于 `/smart_plus/ad/get/` 的 `smart_plus_ad_ids` 批量查询。落地页优先使用报表 `ad_url` 或 Smart+ 详情 `landing_page_url_list`，不要在 `/report/integrated/get` 请求 `ad_url_list`，减少无效重试和重复详情请求。

### full

用于正式周报复盘或客户问数较多的场景。

数据源：

- standard 的全部数据源
- `audience_device`
- 更多 landing rows
- 更多 targeted creative-retention candidates

特点：

- 比 standard 更完整。
- 仍然避免全量素材扫描。
- 适合周报复盘、素材诊断、预算重分配前的分析。

### deep

用于排障、审计或一次性深挖。

数据源：

- full 的全部数据源
- 全量 campaign/adgroup/ad structure
- 更大的 targeted creative-retention candidate 集合

特点：

- 最完整，也最慢。
- API 失败面最大。
- 不建议作为日报或默认周报。

## 输出目录结构

典型 `weekly + standard` 输出：

```text
build/report_runs/tiktok_123_weekly_standard_2026-05-12/
├── manifest.json
├── user_type.json
├── apps.json
├── activities.json
├── current_advertiser_insights.json
├── current_campaign_insights.json
├── current_adgroup_insights.json
├── current_ad_insights.json
├── previous_advertiser_insights.json
├── previous_campaign_insights.json
├── previous_adgroup_insights.json
├── previous_ad_insights.json
├── audience_breakdown.json
├── audience_country.json
├── audience_age_gender.json
├── audience_placement.json
├── landing_pages.json
├── campaign_structure.json
├── adgroup_structure.json
├── ad_structure.json
└── targeted_creative_retention.json
```

`manifest.json` 是 `$motata-report` 的主要入口，记录：

- platform
- advertiser_id
- period
- depth
- current/previous window
- run_dir
- 每个 source 的状态、路径、行数、重试次数、降级原因

## 与最终 HTML 的衔接

`motata report tiktok run` 只负责拉数和记录 provenance。最终 HTML 由 `$motata-report` 负责：

1. 读取 `manifest.json`。
2. 校验 current/previous 是否等长、关键 KPI 是否可解析、source 是否 degraded。
3. 合并 campaign/adgroup/ad name + id。
4. landing Revenue/ROAS 缺失时，用绑定 Ads 的 value/ROAS 汇总回填。
5. Audience 分段同时展示 spend、revenue、ROAS。
6. Creative 表只对最终入表广告显示 preview；Preview URL 不单独成列，放在 preview hover/focus 操作里。
7. Tags 和判断使用多语言友好标签，正向绿色、负向红色、中性黄色。

## 速度和完整性建议

- 日报监控：`daily + fast`。
- 日报需要可落地动作：`daily + standard`。
- 默认周报：`weekly + standard`。
- 客户复盘或素材专题：`weekly + full`。
- 排障、审计、Connector QA：`custom/weekly + deep`。

不要为了“完整”默认全量拉 creative-retention。TikTok 的 smart plus、Spark Ads item、preview URL、`tt_video/list`、`smart_plus/ad/get` 都可以作为保底，但这些保底应该在最终候选广告确定后批量执行，避免无意义地拖慢整份报告。
