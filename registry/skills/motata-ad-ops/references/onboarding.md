# Motata Ad Ops Onboarding
# Motata Ad Ops 使用说明

## Fast Start
## 快速开始

**EN**
If the task is already concrete, use this pattern:

1. say the platform
2. say the account scope
3. say whether you want read, validate, or write
4. provide token or API key if needed

Good starter requests:

- "List all TikTok campaigns under advertiser `7444033053753835536`."
- "Validate whether this promoted object is launch-ready."
- "Review December 2025 TikTok delivery for these advertisers."

**中文**
如果任务已经很明确，建议这样提：

1. 先说平台
2. 再说账户范围
3. 再说你要查、校验还是写
4. 如果需要执行，再给 token 或 API key

推荐起手提法：

- “列出 advertiser `7444033053753835536` 下的全部 TikTok campaign。”
- “校验这个 promoted object 能不能投。”
- “复盘这些 advertiser 在 2025 年 12 月的 TikTok 投放。”

## What This Skill Does
## 这个 Skill 做什么

**EN**
`motata ad ops` is the execution layer for Meta and TikTok operations.
Use it when the task is operational rather than strategic.

It is responsible for:

- token path selection
- account and asset discovery
- validation before writes
- safe execution through `motata`
- troubleshooting and recovery

**中文**
`motata ad ops` 是 Meta 和 TikTok 的执行层。
当任务偏操作而不是偏策略时，就应该用它。

它负责：

- 选择 token 获取路径
- 发现账户和资产
- 写前校验
- 通过 `motata` 安全执行
- 排错与恢复

## When To Use
## 什么时候用

**EN**
Use this skill when you want:

- to list accounts, campaigns, adgroups, ads, creatives, or assets
- to validate promoted objects, creatives, or ad-link assumptions
- to run reporting or diagnostics
- to perform controlled writes

**中文**
当你需要以下事情时，用这个 skill：

- 查询账户、campaign、adgroup、ad、creative、asset
- 校验 promoted object、creative 或 ad-link
- 拉报表、看 insights、做诊断
- 做可控写操作

## Default Execution Policy
## 默认执行策略

**EN**
This skill defaults to:

- read-first
- validate-first
- smallest possible scope
- paused or equivalent safe state for create flows
- Auth Center token retrieval if direct token is absent

**中文**
这个 skill 的默认策略是：

- 优先只读
- 优先校验
- 尽量缩小操作范围
- 创建类操作默认用暂停或等价安全状态
- 如果没有直接 token，则优先走 Auth Center

## What To Provide
## 最好提供什么信息

**EN**
For Meta:

- ad account ID
- access token or Auth Center API key
- object IDs if already known

For TikTok:

- advertiser ID
- access token or Auth Center API key
- object IDs if already known

**中文**
Meta 场景下，最好提供：

- ad account ID
- access token 或 Auth Center API key
- 如果已知，也可以直接给 object ID

TikTok 场景下，最好提供：

- advertiser ID
- access token 或 Auth Center API key
- 如果已知，也可以直接给 object ID

## Example Requests
## 示例提法

**EN**

- "Use `motata ad ops` to list all TikTok campaigns under advertiser `7444033053753835536`."
- "Use `motata ad ops` to validate whether this promoted object is launch-ready."
- "Use `motata ad ops` to review December 2025 TikTok delivery."

**中文**

- “用 `motata ad ops` 列出 advertiser `7444033053753835536` 下的全部 TikTok campaign。”
- “用 `motata ad ops` 校验这个 promoted object 能不能投。”
- “用 `motata ad ops` 复盘 2025 年 12 月 TikTok 投放数据。”

## Expected Outputs
## 常见输出

**EN**
Typical outputs include:

- `asset_inventory`
- `command_results`
- `missing_prerequisites`
- `next_safe_action`

**中文**
常见输出包括：

- `asset_inventory`
- `command_results`
- `missing_prerequisites`
- `next_safe_action`

## Important Boundaries
## 重要边界

**EN**

- This skill does not replace top-level orchestration.
- It should not invent object IDs or assume assets exist.
- It should not default into copy or AIGC flows.

**中文**

- 这个 skill 不能替代顶层编排。
- 不能凭空编造 object ID，也不能假设资产一定存在。
- 不应默认进入 copy 或 AIGC 流程。

## One-Line Summary
## 一句话总结

**EN**
Use `motata ad ops` when you already know the task should become safe, concrete `motata` execution.

**中文**
当你已经明确任务要落成安全、具体的 `motata` 执行时，就用 `motata ad ops`。

## Short Agent Prompt
## 给 Agent 的短提示

**EN**
Use `motata ad ops` for concrete Meta or TikTok operations. Prefer read-first discovery, validate before writes, use Auth Center token retrieval when direct tokens are absent, avoid copy and AIGC by default, and return asset inventory, command results, missing prerequisites, and the next safe action.

**中文**
当任务已经明确要落成具体的 Meta 或 TikTok 操作时，用 `motata ad ops`。默认先只读发现，再做校验，再决定是否写入；没有直接 token 时优先走 Auth Center；默认不进 copy 和 AIGC；最后返回资产清单、命令结果、缺失前提和下一步安全动作。
