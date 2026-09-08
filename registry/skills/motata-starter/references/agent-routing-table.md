# Agent Routing Table
# Agent 场景路由表

Use this table after `motata init` or whenever the next step is ambiguous.
The goal is to let an agent map a business request to the correct module, command entry, and expected output shape without re-deriving the workflow each time.

在 `motata init` 之后，或者下一步动作还不明确时，优先看这张表。
它的目标是让 agent 直接把业务需求映射到正确的模块、命令入口和输出形态，而不是每次重新推导流程。

## Routing Rules
## 路由规则

**EN**

1. First classify the request by outcome, not by command syntax.
2. If the user needs account access, token visibility, or asset readiness, prefer `motata-ad-ops` plus `motata-token`.
3. If the user only has a product or app URL and wants a plan, prefer `motata-starter`.
4. If the user needs diagnosis, ranking, anomaly detection, or account tiering, bring in `motata-vertical-analyst`.
5. If the user needs recurring summary outputs or polished review artifacts, bring in `motata-report`.
6. If the request spans multiple accounts, prefer account inventory plus batch report flows over one-account-first manual loops.

**中文**

1. 先按“要得到什么结果”分类，不要先按命令名分类。
2. 如果用户要确认账户访问、token 可见性或资产可用性，优先 `motata-ad-ops` + `motata-token`。
3. 如果用户只有商品页或 App 页面，并且要的是投放方案，优先 `motata-starter`。
4. 如果用户要诊断、排序、异常识别或账户分层，要引入 `motata-vertical-analyst`。
5. 如果用户要固定复盘、汇总输出或更完整的交付物，要引入 `motata-report`。
6. 如果需求覆盖多个账户，优先账户 inventory + 批量 report 流，而不是一个账户一个账户地手动推。

## Table
## 路由表

### 1) New Account Takeover / 新账户接管

- **When to route here**
  - EN: Taking over a new client or brand account and first needing to know whether it can run, what assets are usable, and what advertiser type it belongs to.
  - 中文：接手新客户或新品牌账户，先要确认能不能投、拿什么投、属于什么投放类型。
- **Modules**
  - `motata-ad-ops` + `motata-token` + `motata-vertical-analyst`
- **Primary entries**
  - asset discovery
  - account inventory / token lookup
  - `user-type analyze`
- **Typical command path**
  - `motata init`
  - token or inventory lookup
  - `motata meta assets discover ...` / `motata tiktok accounts list ...`
  - `motata meta user-type analyze ...` / `motata tiktok user-type analyze ...`
- **Expected outputs**
  - usable asset list
  - missing prerequisites
  - abnormal bindings
  - account-type label
  - next analysis path

### 2) Prelaunch Risk Check / 上线前风险预检

- **When to route here**
  - EN: The campaign is about to launch but the team is worried the page/pixel/app/creative relationships may be invalid.
  - 中文：广告准备上线，但担心 page / pixel / app / creative 关系不合法。
- **Modules**
  - `motata-ad-ops`
- **Primary entries**
  - `meta validate ad-link`
  - `tiktok validate promoted-object`
- **Typical command path**
  - confirm target platform and account
  - run the relevant validate command
  - inspect exact failure payloads before any write
- **Expected outputs**
  - explicit go / no-go result
  - exact failure point
  - next safe fix step

### 3) Cold-Start Strategy Generation / 新品冷启动策略生成

- **When to route here**
  - EN: Only a product page or app page exists and a same-day testing plan is needed.
  - 中文：只有一个商品页或 App 页面，要求当天给出测试方案。
- **Modules**
  - `motata-starter`
- **Primary entries**
  - `product scrape`
  - `product intake`
- **Typical command path**
  - `motata product scrape <url>`
  - `motata product intake <url>`
  - strategy synthesis and testing plan generation
- **Expected outputs**
  - `campaign_brief`
  - value propositions
  - audience hypotheses
  - budget split
  - testing matrix

### 4) Ecommerce Page Diagnosis / 电商商品页与落地页效果诊断

- **When to route here**
  - EN: Multiple SKUs run together and the team wants to know which page is losing money and which should scale.
  - 中文：多个 SKU 一起投，想知道哪个页面在亏钱、哪个页面更该扩量。
- **Modules**
  - `motata-ad-ops` + `motata-vertical-analyst`
- **Primary entries**
  - landing-page / product spend analysis
  - detailed insights pulls
- **Typical command path**
  - pull landing-page or product performance
  - enrich with URL / SKU grouping
  - rank by spend, clicks, conversions, and efficiency
- **Expected outputs**
  - URL or SKU ranking
  - spend / click / conversion comparisons
  - page-priority recommendations

### 5) Daily Pulse / 日常异常巡检

- **When to route here**
  - EN: Every morning, check whether yesterday’s account behavior looks abnormal.
  - 中文：每天早上先看昨日账户是否异常。
- **Modules**
  - `motata-vertical-analyst` + `motata-report`
- **Primary entries**
  - `report meta run --period daily`
  - `report tiktok run --period daily`
- **Typical command path**
  - reuse init defaults when available
  - run daily report pull
  - classify anomalies and propose same-day actions
- **Expected outputs**
  - anomaly ad list
  - key change explanations
  - same-day priority actions

### 6) Weekly Report / 周度客户复盘

- **When to route here**
  - EN: A weekly review for a client or leadership team is needed.
  - 中文：每周固定给客户或老板做账户复盘。
- **Modules**
  - `motata-report`
- **Primary entries**
  - `report meta run --period weekly`
  - `report tiktok run --period weekly`
- **Typical command path**
  - run weekly pull
  - compare windows
  - package KPI summary plus problem list
- **Expected outputs**
  - HTML report
  - KPI summary
  - week-over-week changes
  - issue list
  - next-week recommendations

### 7) Creative Fatigue / 创意疲劳发现与换新排序

- **When to route here**
  - EN: The creative team has many assets and needs a replacement priority list.
  - 中文：素材量大，创意团队不知道先替换哪几条。
- **Modules**
  - `motata-vertical-analyst` + `motata-report`
- **Primary entries**
  - creative-level performance analysis
  - report runner
- **Typical command path**
  - pull creative-level data
  - rank high-spend / low-conversion or retention-decay cases
  - produce replacement order
- **Expected outputs**
  - fatigue leaderboard
  - high-spend low-conversion watchlist
  - replacement priority

### 8) Bottleneck Troubleshooting / 多层级卡点排查

- **When to route here**
  - EN: Spend drops and the team does not know whether the block is budget, review, status, creative, identity, or platform constraints.
  - 中文：账户花费异常下降，不知道卡在哪一层。
- **Modules**
  - `motata-ad-ops`
- **Primary entries**
  - campaigns / adgroups / ads list/get
  - account detail inspection
- **Typical command path**
  - inspect account state
  - inspect campaign / adgroup / ad states
  - trace the lowest failing layer
- **Expected outputs**
  - clear bottleneck location
  - root-cause category
  - next diagnostic or fix step

### 9) Multi-Account Standardization / 多账户横向对比与标准化运营

- **When to route here**
  - EN: An agency manages many clients and wants account tiering plus SOP standardization.
  - 中文：代理商管理多个客户，希望做账户分层和团队 SOP 标准化。
- **Modules**
  - `motata-starter` + `motata-token` + `motata-vertical-analyst` + `motata-report`
- **Primary entries**
  - account inventory
  - batch report runs
  - unified analysis and reporting flow
- **Typical command path**
  - `motata init`
  - batch inventory or token checks
  - `motata report meta run --all-init-accounts ...`
  - `motata report tiktok run --all-init-accounts ...`
  - normalize outputs into tiers
- **Expected outputs**
  - scale / stable / risk tiers
  - standardized delivery chain
  - repeatable team workflow

### 10) Migration and Rebuild / 跨账户迁移与重建

- **When to route here**
  - EN: The account is restricted, the business entity changed, or migration and rebuild are required.
  - 中文：账户受限、主体更换、迁户重建。
- **Modules**
  - `motata-ad-ops`
- **Primary entries**
  - `meta migrate export`
  - `plan`
  - `run`
  - `resume`
- **Typical command path**
  - export source state
  - validate dependencies
  - run migration in resumable mode
  - inspect failures and replay as needed
- **Expected outputs**
  - migration plan
  - precheck results
  - resumable execution log
  - failed asset list

## Fast Mapping Heuristic
## 快速判断法

**EN**

- If the user says “Can this account run?” -> route to takeover / inventory.
- If the user says “Can this ad launch safely?” -> route to prelaunch validation.
- If the user says “We only have a URL, give me a plan.” -> route to cold-start strategy.
- If the user says “Which page or SKU is wasting budget?” -> route to ecommerce diagnosis.
- If the user says “What broke yesterday?” -> route to Daily Pulse.
- If the user says “Prepare the weekly review.” -> route to Weekly Report.
- If the user says “Which creatives should we replace first?” -> route to creative fatigue.
- If the user says “Spend dropped, where is it blocked?” -> route to bottleneck troubleshooting.
- If the user says “Standardize operations across many accounts.” -> route to multi-account standardization.
- If the user says “We need to move or rebuild this account.” -> route to migration and rebuild.

**中文**

- 用户说“这个账户现在能不能跑？” -> 走新账户接管 / 资产盘点。
- 用户说“这条广告能不能安全上线？” -> 走上线前风险预检。
- 用户说“只有一个链接，先给我方案。” -> 走新品冷启动策略生成。
- 用户说“哪个页面或 SKU 在烧钱？” -> 走电商页面诊断。
- 用户说“昨天哪里异常了？” -> 走 Daily Pulse。
- 用户说“准备本周复盘。” -> 走 Weekly Report。
- 用户说“先换哪几条素材？” -> 走创意疲劳发现。
- 用户说“花费掉了，不知道卡在哪。” -> 走多层级卡点排查。
- 用户说“要把多个账户运营标准化。” -> 走多账户横向对比与标准化运营。
- 用户说“要迁户或重建。” -> 走跨账户迁移与重建。
