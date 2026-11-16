# TikTok Direct HTTP 实施验证

核验日期：2026-11-13。工作区候选版本：0.3.0，本机修订，尚未发布。改造前基线：339 项离线测试通过。

## 已完成验证

| 验证项 | 结果与范围 |
|---|---|
| Python 离线回归 | Python 3.11 与 3.14，各 475 项测试；禁止网络并隔离用户配置。包含 Meta/共享模块回归；文档核对新增 32 项，首次限流退避新增 29 项，本轮 QPS 识别与共享节流再新增 26 项 |
| 限流退避 | 40100 与精确的 40002 + OVER_QPS_LIMIT 分类为 rate_limit；HTTP 层统一只读重试；SQLite 跨请求/跨进程按端点节流与冷却。覆盖 Retry-After、指数抖动、次数及共享等待预算、5 进程竞争、中断、逐次用量与等待期间 lease 刷新；全部注册写端点和上传不重试；报表不重放成功页或整个数据源 |
| 请求兼容 | 51 个原 SDK 操作、31 个原 HTTP 用例的请求 fixtures；74 个公共方法保留，83 个注册端点 |
| Auth Center | 固定 v0.3.9 的 mock 契约、Key header、账号隔离、回退 Token 写前证明、来源失败不回退、用量未知不重报，以及 CLI→mock Auth Center→mock TikTok |
| 写入恢复 | 成功步骤复用、明确失败后显式恢复、未知写入禁止重放、缺失对象 ID、崩溃遗留 claim、同 job 互斥、payload 冲突 |
| Drama | 自有/分销模拟创建与恢复；CBO/广告组预算、ROAS/日预算、排期、动态 CTA、独立素材身份、自动广告名及恢复、父级/账号回读、分页和同剧同地区 ID 冲突；复制保留嵌套定向 |
| Smart+ 广告组更新 | 仅两个必填 ID、增量请求体、条件字段读前核验、ROAS/预算/排期/定向/Search 限制、显式 false、只读失败后不写入；保留未知字段。四段官方请求示例入库为脱敏 fixture |
| 上传 | 50/500 MiB 本地稀疏文件通过流式编码；tracemalloc 检查额外 Python 堆内存峰值及两组差值均小于 64 MiB，不等同于全进程 RSS 测量 |
| Node 运行时 | 11 项测试通过，包含运行时锁、版本变化重装、失败健康检查和用户 cwd 保留 |
| SDK 门禁 | 无 SDK import、vendor、adapter、外部回退；客户端所有平台出口使用统一 HTTP transport |
| 干净发行物 | macOS Python 3.11：wheel、sdist 重建、npm 打包/冷启动安装、HTTP mock 冒烟，无 SDK/JWT 模块；使用固定离线 wheelhouse |

执行命令：

```sh
python scripts/run_offline_tests.py
python scripts/check_tiktok_http.py
node --test tests/runtime.test.js tests/runtime-lock.test.js
python scripts/check_release.py --wheelhouse /path/to/offline/wheels
git diff --check
```

## 指定测试账号

使用用户指定测试账号及平台 Token，未经过 Auth Center 取 Token。公开记录不包含账号 ID 和平台请求 trace；原始联调记录保留在本地忽略目录。凭据经隐藏输入进入进程，未写入仓库。

7 项只读请求均成功：账户信息、普通 Campaign、Upgraded Smart+ Campaign、Upgraded Smart+ Ad Group、身份、集成报表、Native Series。检查结果和页内数量见 [脱敏联调记录](tiktok-live-validation.json)。身份列表返回 17 条；报表该日返回 0 行；所选身份的剧集列表返回 0 条。这些空结果是成功响应，不是资格证明。

没有真实创建、修改、激活或上传广告资产；没有产生任何测试广告对象。

## 官方规则核对

2026-11-12 按 Firecrawl 重新抓取的 [Drama Series 创建指南](https://business-api.tiktok.com/portal/docs/create-an-upgraded-smart-mini-series-feed-campaign/v1.3) 与 [Upgraded Smart+ 广告组更新请求](https://business-api.tiktok.com/portal/docs/update-an-upgraded-smart-ad-group/v1.3) 完成逐项核对。规则实现分别见 `motata_cli/tiktok/drama_rules.py` 与 `motata_cli/tiktok/smartplus_adgroup_update.py`，调用与恢复见 `services/drama_series.py`。已区分创建输入、更新请求及响应字段，不直接把响应模型当作更新模型。源快照摘要、修复映射及未自动化的验证见 [文档核对记录](tiktok-drama-contract-review.md)。

文本抓取器可能只能拿到页面壳；这种情况标记 `source_unavailable` 并保留上一版基线，不宣称已自动完成规则核对。CI 配置尚未推送或在远端运行。

## 验收边界

- 尚无真实 Auth Center API Key 联调；本地契约测试不能替代部署配置和租户数据验收。
- Drama 真实禁用创建→查询→允许字段更新→查询需要具体授权剧集/分销链接、创意、CTA、预算和排期。当前没有可用于此流程的确定输入。
- ROAS 冲突预检比较相同地区/邮编 ID；地理层级交叠、平台白名单、分销授权及全部素材资格仍由 TikTok 判断。扫描失败或超过 50 页不会标为完整通过。
- 单步命令无透明写入重试；跨进程恢复限于保留 job 数据库的复制/Drama 工作流。复制未知对象的自动识别与对账不在当前实现内。
- 未运行远端 Linux/Windows CI，未发布候选包、执行生产升级或开展跨版本回滚演练。

完整使用方式见 [0.3.0 迁移说明](releases/0.3.0.md)，总体状态见 [plan.md](../plan.md)。

本轮自动退避验证使用受控假响应和模拟等待，未主动触发真实账户限流；策略与参数说明见 [TikTok 只读限流与退避](tiktok-read-retries.md)。

2026-11-13 已将 QPS 识别与共享节流修复更新到本机 Python 可编辑安装和全局 npm 安装，版本均保持 0.3.0。从仓库外分别验证两个启动入口、报表和 Drama 帮助命令；每套安装均通过 55 项退避/共享节流/认证回归，112 个 Python/JSON 源文件与当前工作区逐字节一致，`pip check` 通过。重新核验 wheel 117 文件、sdist/npm 各 122 文件及干净安装。记录位于本地忽略目录 `buildcheck/local-install-0.3.0-throttle-20261113/`。本轮没有真实 TikTok API 调用，未执行 npm/GitHub 发布。
