# Motata Starter Onboarding
# Motata Starter 使用说明

## Fast Start
## 快速开始

**EN**
If you are new, use this pattern:

0. Run `motata init` first if token source, account scope, or default metrics are not settled yet
1. Say the platform: Meta, TikTok, or both
2. Say the goal: research, launch, validate, or review
3. Provide account context if you have it
4. Provide API key or token if execution is needed

Good starter requests:

- "Research a TikTok launch plan for this product, then check whether my advertisers are ready."
- "Review all TikTok data for December 2025 and tell me the next actions."
- "Plan a Meta test for this landing page and prepare a paused launch."

**中文**
如果你第一次用，建议这样提：

0. 如果 token 来源、账户范围、默认指标还没定，先跑 `motata init`
1. 先说平台：Meta、TikTok 或两边都看
2. 再说目标：研究、启动、校验还是复盘
3. 如果有账户信息，直接给
4. 如果需要执行，再给 API key 或 token

推荐起手提法：

- “先研究这个产品的 TikTok 首发方案，再看看我现有 advertiser 能不能直接跑。”
- “复盘 2025 年 12 月全部 TikTok 数据，并告诉我下一步该怎么调。”
- “根据这个落地页规划一轮 Meta 首测，并准备一个暂停状态的 launch。”

## What This Is
## 这是什么

**EN**
`motata` is a Meta + TikTok ads workflow system.
This skill stack currently includes:

- `motata starter`: top-level workflow orchestration for strategy research, delivery execution, and post-campaign review
- `motata ad ops`: execution layer for safe Meta and TikTok operations through `motata`
- `motata token`: token retrieval through Auth Center API keys

This stack is meant for real advertising operations, not just demo command runs.
The published CLI is self-contained, so URL intake and token helper flows stay inside `motata` instead of depending on external skill-only scripts.

**中文**
`motata` 是一套面向 Meta + TikTok 的广告工作流系统。
当前 skill 体系包括：

- `motata starter`：顶层总编排，负责策略研究、交付执行、投后复盘
- `motata ad ops`：执行层，负责通过 `motata` 安全调用 Meta 和 TikTok 能力
- `motata token`：通过 Auth Center API key 获取 token

这套体系是给真实广告操作用的，不是只做演示命令调用。
发布后的 CLI 是自包含的，所以 URL intake 和 token helper 都可以直接在 `motata` 里完成，不需要再依赖只在 skill 内存在的脚本。

## When To Use
## 什么时候用

**EN**
Use `motata starter` when you want:

- a full ads workflow
- strategy research before launch
- execution plus review
- cross-platform planning across Meta and TikTok

**中文**
当你需要以下事情时，用 `motata starter`：

- 跑完整广告流程
- 先做策略研究再启动投放
- 执行投放并做复盘
- 做 Meta 与 TikTok 的跨平台规划

## Default Workflow
## 默认工作流

**EN**
The default workflow is:

1. run `motata init` first when token source, account scope, or metric defaults are still unknown
2. run product intake first when a product, app, or landing-page URL exists
3. research the strategy
4. discover available accounts and assets
5. validate the delivery setup
6. execute with `motata`
7. review performance and recommend next actions

If `motata init` has already been completed, later report commands may reuse the initialized account scope automatically. Tokens still need to come from CLI flags or environment variables at runtime.
Going from init straight into a daily report is only one valid path. After init, the agent and the user should choose the next scenario based on the business need: takeover, asset audit, prelaunch validation, diagnosis, reporting, migration, or strategy generation.

**中文**
默认工作流是：

1. 如果 token 来源、账户范围、指标默认值还没确认，先跑 `motata init`
2. 如果有商品页、App 页或落地页，先跑 product intake
3. 再做策略研究
4. 再发现可用账户和资产
5. 再校验投放配置
6. 再用 `motata` 执行
7. 最后做投后复盘并给出下一步建议

如果已经跑过 `motata init`，后续报表命令可以自动复用初始化时确认过的账户范围；但 token 仍然需要在运行时报给 CLI 或放在环境变量里。
初始化完成后直接接日报只是其中一种路径。init 之后，agent 和用户应该按实际目标选择场景：新账户接管、资产盘点、上线前校验、效果诊断、固定复盘、迁移重建，或新品冷启动策略生成。

## Init Contract
## Init 契约

**EN**
`motata init` is an onboarding step, not a secret store.

It saves:

- default platform
- default account for the platform
- selected account list for the platform
- detected user-type summary
- recommended analysis metrics
- suggested daily report command

It does not save:

- platform access tokens
- Auth Center API keys
- any runtime secret copied from environment variables

Commands that automatically reuse init state today:

- `motata report meta run`
- `motata report tiktok run`

That automatic reuse currently includes:

- default account fallback when the account flag is omitted
- initialized account list when `--all-init-accounts` is used
- recommended analysis metrics saved by init

Multi-account rule:

- when multiple Meta accounts are selected during init, recommended metrics are determined from the full selected Meta account set
- when multiple TikTok advertiser accounts are selected during init, recommended metrics are determined from the full selected TikTok account set
- the first selected account is still stored as the default follow-up account

**中文**
`motata init` 是 onboarding 步骤，不是密钥保险箱。

它会保存：

- 默认平台
- 该平台的默认账户
- 该平台的已选账户列表
- 识别出的账户类型摘要
- 推荐分析指标
- 推荐日报表命令

它不会保存：

- 平台 access token
- Auth Center API key
- 任何来自运行时环境变量的 secret

当前会自动复用 init 状态的命令：

- `motata report meta run`
- `motata report tiktok run`

当前自动复用的内容包括：

- 未显式传账户参数时的默认账户回退
- 使用 `--all-init-accounts` 时的初始化账户列表
- init 保存的推荐分析指标

多账户规则：

- init 时如果选择多个 Meta 账户，推荐指标会基于所选全部 Meta 账户联合判断
- init 时如果选择多个 TikTok advertiser 账户，推荐指标会基于所选全部 TikTok 账户联合判断
- 首个选中账户仍会被保存为后续命令的默认账户

## Scenario Routing
## 场景路由

**EN**
After init, choose the next scenario deliberately instead of defaulting to daily reporting every time.

1. New account takeover, asset inventory, and vertical recognition
   - scenario: taking over a new client or brand account; first confirm whether it can run, what can be used, and what advertiser type it belongs to
   - modules: `motata-ad-ops` + `motata-token` + `motata-vertical-analyst`
   - entry: assets discover, account inventory/token queries, user-type analyze
   - output: usable asset list, missing pieces, abnormal bindings, account-type labels, next analysis path
2. Prelaunch risk check
   - scenario: launch is near but page/pixel/app/creative relationships may be invalid
   - modules: `motata-ad-ops`
   - entry: `meta validate ad-link`, `tiktok validate promoted-object`
   - output: explicit go/no-go result with exact failure points
3. New product cold-start strategy generation
   - scenario: only a product page or app page exists and a same-day test plan is needed
   - modules: `motata-starter`
   - entry: `product scrape`, `product intake`
   - output: campaign brief, value props, audience hypotheses, budget split, testing matrix
4. Ecommerce landing-page and product-page diagnosis
   - scenario: many SKUs run together and the team needs to know which page is losing money and which should scale
   - modules: `motata-ad-ops` + `motata-vertical-analyst`
   - entry: landing-page/product spend analysis, detailed insights pulls
   - output: spend, click, conversion ranking by URL or SKU plus page-priority recommendations
5. Daily Pulse anomaly checks
   - scenario: every morning, review whether yesterday’s account behavior looks abnormal
   - modules: `motata-vertical-analyst` + `motata-report`
   - entry: `report meta/tiktok run --period daily`
   - output: anomaly ad list, key change explanations, same-day priority actions
6. Weekly client reporting
   - scenario: weekly client or leadership review
   - modules: `motata-report`
   - entry: `report meta/tiktok run --period weekly`
   - output: HTML report, KPI summary, week-over-week changes, issue list, next-week recommendations
7. Creative fatigue detection and replacement ranking
   - scenario: the creative team needs to know which ads should be replaced first
   - modules: `motata-vertical-analyst` + `motata-report`
   - entry: creative-level performance analysis, report runner
   - output: fatigue leaderboard, high-spend low-conversion watchlist, replacement priority
8. Multi-level bottleneck troubleshooting
   - scenario: spend drops and the team does not know whether the block is budget, review, status, creative, identity, or platform constraints
   - modules: `motata-ad-ops`
   - entry: campaigns/adgroups/ads list/get, account detail inspection
   - output: a clear bottleneck location and cause
9. Multi-account comparison and standardized operations
   - scenario: an agency manages many clients and wants account tiering plus SOP standardization
   - modules: `motata-starter` + `motata-token` + `motata-vertical-analyst` + `motata-report`
   - entry: account inventory, batch report runs, unified analysis and reporting flow
   - output: scale/stable/risk tiers plus a standardized delivery chain
10. Cross-account migration and rebuild
   - scenario: the account is restricted, the entity changed, or migration and rebuild are required
   - modules: `motata-ad-ops`
   - entry: `meta migrate export`, `plan`, `run`, `resume`
   - output: migration plan, precheck results, resumable execution logs, failed asset list

**中文**
init 之后，不要默认每次都直接去跑日报；应先判断下一步场景。

1. 新账户接管、资产盘点与垂类识别
   - 场景：接手新客户或新品牌账户，先确认能不能投、拿什么投，以及该账户属于什么投放类型
   - 模块：`motata-ad-ops` + `motata-token` + `motata-vertical-analyst`
   - 入口：assets discover、账户 inventory/token 查询、user-type analyze
   - 输出：可用资产清单、缺失项、异常绑定项、账户类型标签、后续分析路径
2. 上线前风险预检
   - 场景：广告准备上线，但担心 page/pixel/app/creative 关系不合法
   - 模块：`motata-ad-ops`
   - 入口：`meta validate ad-link`、`tiktok validate promoted-object`
   - 输出：明确可上线/不可上线，并指出失败点
3. 新品冷启动策略生成
   - 场景：只有一个商品页或 App 页面，要求当天给出测试方案
   - 模块：`motata-starter`
   - 入口：`product scrape`、`product intake`
   - 输出：campaign brief、卖点提炼、受众假设、预算拆分、测试矩阵
4. 电商商品页与落地页效果诊断
   - 场景：多个 SKU 一起投，想知道哪个页面在亏钱、哪个页面更该扩量
   - 模块：`motata-ad-ops` + `motata-vertical-analyst`
   - 入口：落地页/产品消耗分析、insights 明细拉取
   - 输出：按 URL 或 SKU 排序的花费、点击、转化表现和页面优先级建议
5. Daily Pulse 日常异常巡检
   - 场景：每天早上先看昨日账户是否异常
   - 模块：`motata-vertical-analyst` + `motata-report`
   - 入口：`report meta/tiktok run --period daily`
   - 输出：异常广告列表、关键波动解释、当天优先处理动作
6. Weekly Report 周度客户复盘
   - 场景：每周固定给客户或老板做账户复盘
   - 模块：`motata-report`
   - 入口：`report meta/tiktok run --period weekly`
   - 输出：HTML 报告、KPI 摘要、环比变化、问题清单、下周建议
7. 创意疲劳发现与换新排序
   - 场景：素材量大，创意团队不知道先替换哪几条
   - 模块：`motata-vertical-analyst` + `motata-report`
   - 入口：creative-level 表现分析、报告 runner
   - 输出：疲劳素材榜单、高花费低转化 watchlist、换新优先级
8. 多层级卡点排查
   - 场景：账户花费异常下降，不知道卡在哪一层
   - 模块：`motata-ad-ops`
   - 入口：campaigns/adgroups/ads list/get、账户详情查询
   - 输出：明确卡点位置，是预算、审核、状态、素材、身份还是平台限制
9. 多账户横向对比与标准化运营
   - 场景：代理商管理多个客户，同时希望做账户分层和团队 SOP 标准化
   - 模块：`motata-starter` + `motata-token` + `motata-vertical-analyst` + `motata-report`
   - 入口：账户 inventory、批量 report runs、统一分析与报告流程
   - 输出：扩量组/维稳组/风险组分层清单，以及标准化交付链路
10. 跨账户迁移与重建
   - 场景：账户受限、主体更换、迁户重建
   - 模块：`motata-ad-ops`
   - 入口：`meta migrate export`、`plan`、`run`、`resume`
   - 输出：迁移计划、预检结果、可恢复执行记录、失败资产清单

For a compact scenario-to-module lookup, read [agent-routing-table.md](agent-routing-table.md).

如需一张更紧凑的“场景 -> 模块 -> 命令入口”速查表，请看 [agent-routing-table.md](agent-routing-table.md)。

## Safety Rules
## 安全规则

**EN**
By default, this stack is:

- read-first
- validate-first
- paused-by-default for creation flows
- Auth Center first for token retrieval when API keys are available
- no copy flows unless explicitly requested
- no AIGC flows unless explicitly requested

**中文**
默认安全规则如下：

- 优先只读
- 优先校验
- 创建类操作默认先建成暂停状态
- 如果已有 API key，优先走 Auth Center 获取 token
- 除非明确要求，否则不跑 copy
- 除非明确要求，否则不跑 AIGC

## What To Provide
## 最好提供什么信息

**EN**
For strategy work:

- product URL, app URL, or store URL
- business goal
- target market
- budget range
- competitor references if available

For execution work:

- platform: Meta or TikTok
- account ID or advertiser ID
- access token, or API key for Auth Center
- object IDs if already known

For review work:

- platform
- account scope
- date range
- campaign, adgroup, or ad scope if needed
- success metric if available

**中文**
做策略时，最好提供：

- 商品链接、App 链接或店铺链接
- 业务目标
- 目标市场
- 预算范围
- 竞品参考

如果已经有 URL，默认会先跑：

- `motata product intake <url>`

做执行时，最好提供：

- 平台：Meta 或 TikTok
- account ID 或 advertiser ID
- access token，或者 Auth Center API key
- 如果已经知道，也可以直接给 object ID

做复盘时，最好提供：

- 平台
- 账户范围
- 日期范围
- 需要看的 campaign、adgroup 或 ad 范围
- 如果有的话，成功指标

## Example Requests
## 示例提法

**EN**

- "Use `motata starter` to research a TikTok launch plan for this product, then check whether my current advertisers are ready."
- "Plan a Meta first-round test for this landing page, then prepare a paused launch."
- "Review all TikTok data for December 2025 and tell me the next actions."

**中文**

- “用 `motata starter` 先研究这个产品的 TikTok 首发方案，再看看我现有 advertiser 能不能直接跑。”
- “根据这个落地页规划一轮 Meta 首测，并准备一个暂停状态的 launch。”
- “复盘 2025 年 12 月全部 TikTok 数据，并告诉我下一步该怎么调。”

## Expected Outputs
## 常见输出

**EN**
Typical outputs include:

- `campaign_brief`
- `asset_inventory`
- `execution_plan`
- `command_results`
- `review_report`
- `next_actions`

**中文**
常见输出包括：

- `campaign_brief`
- `asset_inventory`
- `execution_plan`
- `command_results`
- `review_report`
- `next_actions`

## Non-Goals
## 当前非目标

**EN**
This stack does not treat creative production as a default capability.
It can define creative direction, asset requirements, and testing hypotheses, but it does not generate final creatives unless a separate creative capability is added later.

**中文**
当前这套 skill 默认不做创意生产。
它可以定义创意方向、素材要求和测试假设，但不会自动生成最终广告素材，除非后续单独接入创意生成能力。

## One-Line Summary
## 一句话总结

**EN**
Use `motata starter` to think and route. Use `motata ad ops` to execute safely. Use `motata token` to retrieve tokens cleanly.

**中文**
用 `motata starter` 做思考和路由，用 `motata ad ops` 做安全执行，用 `motata token` 干净地取 token。

## Short Agent Prompt
## 给 Agent 的短提示

**EN**
Use `motata starter` for end-to-end Meta or TikTok work. Start with strategy when the task is broad, route execution to `motata ad ops`, prefer Auth Center token retrieval, keep writes safe and paused by default, and finish with structured outputs and next actions.

**中文**
当任务是 Meta 或 TikTok 的全流程需求时，优先用 `motata starter`。任务宽泛时先做策略研究，再把执行路由到 `motata ad ops`，优先走 Auth Center 取 token，默认安全执行、默认暂停创建，最后输出结构化结果和下一步建议。
