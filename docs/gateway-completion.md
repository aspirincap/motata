# Gateway 六组能力补齐（2026-09-19）

本文是本轮实际源码的运行说明，不是实施计划。主接口仍然是完整 Motata CLI；
Gateway 保存 Meta/TikTok/Auth Center 凭证，客户端只使用 Gateway JWT 和受控引用。

**基线**：上一份真实交付 `motata_gateway_rebuilt_source.tar.gz`，Git 源码树
`698625788e485fdadb0c5de19d19b22d9c4930c5`。原 GitHub 基线仍为 `1709e4b`，
树 `6581324ed68b9e90b399d50b7335e0c1c4c1281a`。本地提交不等于远端提交。

## 1. 本轮完成范围

| 用户列出的缺口 | 实际实现及验收 |
|---|---|
| 剩余 SDK / BC | 对当前 TikTokClient 引用的 51 个 SDK builder 建立可重复检查：50 个走审核过的 Gateway 路由，OAuth advertiser inventory 1 个以服务端账户授权发现替换。另核对 32 个 raw HTTP 方法/路径组合。BC 资产、目录、身份、Portfolio、AIGC/avatar、定向工具、素材共享/删除等接入原客户端。 |
| Smart+ / GMV Max | Classic/Smart+ campaign→adgroup→ad 创建、更新、状态请求；Smart+ 素材 overview/breakdown 参数；GMV Max 店铺、商品、视频、campaign 和 report 参数；共享资产归属及多 advertiser 授权。 |
| 完整报告参数 | 保留已有 CLI 报告入口与参数传递。实际 CLI 非空数据测试覆盖 Meta full/deep（含对比窗、nested creative、Page 预览、应用元数据）、TikTok full auction/Smart+ 和 GMV Max（店铺、商品、素材、对比窗）。每份完整 fixture 的 manifest 均要求 `complete=true`。 |
| Page 派生凭证 | Page Token 只存在 Gateway 内存；按调用者/session/account/Page/父凭证版本与内容摘要绑定。原 landing-page story 回退改用 PageRef，支持只读到期续取；共享 Page 不再与广告对象的独占归属混淆。 |
| 下载 | 平台媒体 URL 转为 `motata-download:` 引用；只有 Gateway 能解引用。CDN 白名单、连接时 DNS/IP 检查、TLS 原始主机校验、有界重定向、私有暂存、长度/SHA-256/秘密扫描、原子本地文件写入和取消清理。HTML 仅在显式缓存图片时取回媒体。 |
| 完整素材迁移 | 实际 CLI `export → run → resume` 非空 fixture 包含图片、视频及封面、已有帖子和 Campaign/Adset/Ad 全层级。导出引用过期时重新取受控元数据；原图优先而非低清缩略图；恢复不重复上传/创建；不明广告写入仍 `needs_review`。另外补齐 TikTok copy 的视频封面下载→文件上传→广告创建链路。 |

这里“补齐”以**现有 CLI 已有能力**为边界，不是新增平台从未接入的任意 API，
也不是穷举所有外部业务配置。它不取消已有 CLI 对不支持创意形态的明确错误。

## 2. 必须先执行的状态库升级

新版本将共享资产放进 `asset_membership`，广告对象仍使用独占 ownership。
升级前由可信管理员停止 Gateway、备份整个私有状态目录（包括 master key、SQLite
及 WAL，或使用一致性备份），随后以原服务身份运行：

```bash
motata-gateway-admin --state-dir /var/lib/motata-gateway init
```

该命令是非破坏性 schema 升级：保留凭证、授权、撤销和 pending/confirmed receipts。
业务请求不会静默迁移状态；未升级时拒绝启动。新增回归用旧表结构验证保留数据。

不要删除旧 receipts 来让未知写入重新执行。新媒体/Page 引用是短期内存状态，
Gateway 重启后应重新拉取元数据，不把引用失效误认为平台对象被删除。

## 3. BC、共享资产与账户权限

BC 可被同一个平台 Token 访问，不等于可以向任意调用者披露 BC 内所有账户。
本轮保持两层授权：JWT + 账户 grant，以及可信的共享资产绑定。

```bash
# 在服务器可信管理员环境，不在 agent shell 中运行。
motata-gateway-admin --state-dir /var/lib/motata-gateway bind-asset \
  --platform tiktok --account 123 --object-id 800 --type bc

# 撤销某个账户对某 BC 的本地绑定。
motata-gateway-admin --state-dir /var/lib/motata-gateway remove-asset \
  --platform tiktok --account 123 --object-id 800 --type bc
```

`bc/get/` 只返回绑定范围中的 BC；`bc/asset/get/` 的 advertiser 列表再与调用者的
当前账户 grants 取交集。返回的元数据**不生成账户 grant**。未绑定 BC 的请求在平台
凭证解析前拒绝。受控 BC 下发现的 catalog/pixel/store 等记录对应资产证据。

共享 Page/pixel/app/identity/image/video 可以属于多个明确授权账户；Campaign、
Adgroup/Adset、Ad、Creative 的跨账户冲突仍会拒绝。跨账户分享、多账户 report
逐一检查涉及的所有 advertiser，而不是只看最外层 `account_id`。

多账户环境下无 advertiser 参数的 BC 命令，需要配置明确的 Gateway TikTok
账户上下文，例如 `MOTATA_GATEWAY_TIKTOK_ACCOUNT_ID=123`；不要按 Token 的最大权限
自动猜测用户想操作哪个账户。

## 4. 报告参数与业务语义

原参数解析和业务编排留在 CLI。修改的是 transport、接口策略、账户发现和对象证据。

本轮专门验证：

- integrated report 的 advertiser_ids / bc_id / service_type / data_level / dimensions /
  metrics / start_date / end_date / query_lifetime / query_mode / sort / pagination /
  total_metrics / UTC / filtering 在传输后保持原始值及 JSON 类型。
- Smart+ overview/breakdown 的多维度、过滤、排序、分页。
- GMV Max store_ids / promotion_types / 商品与视频过滤 / creative dimensions / 对比窗。
- Meta 的 fields、嵌套 creative/object_story_spec、异步 insights 任务、breakdowns、
  账户元数据和分页；应用 metadata 根据已授权 promoted_object 证据读取。

报告数据的 `warnings / errors / truncated / complete / currency / time windows`
仍是原业务契约。部分数据不能用默认空列表装成完整成功。Changelog task/create
是读取报告的准备任务，允许 `tiktok:read`，但创建回执仍持久化，网络不明时不盲重放。

正常使用示例（需要既有 JWT、账户 grant 和实际平台权限）：

```bash
motata report meta run --account-id 123 --period weekly --depth full --include-previews
motata report tiktok run --advertiser-id 123 --period weekly --depth full \
  --tiktok-report-mode gmv_max --include-gmv-max always --gmv-max-store-id 901
```

## 5. Page Token 的处理

客户端原来的 Page token 字符串槽位改为 `GatewayPageRef`，其内容不是平台 Token。
服务器只按固定字段取 Page 列表，将它与当前 ad account 的 promote_pages 取交集。
敏感 Page token 字段和上游带 token 的分页 URL 永不返回。

引用绑定：`workspace / sub / client / sid / account / Page / parent_ref / revision /
parent token digest / expiry`。默认最长 300 秒，不超过已知父凭证过期时间。
父凭证轮换、当前 grant 撤销、session 撤销或引用到期都会阻止使用。

引用只能服务已审核的 Page/compound-post **读取**，不能在 body 中选择 Token，
不能跨 Page、跨账户或用作广告写入授权。读取到期可以重新取一次 Page 引用再重试，
不会对写入做这种自动重试。

## 6. 媒体引用、下载与大小限制

只读回来的受支持 CDN 媒体 URL 会变成 `motata-download:<opaque-id>`。
CLI 的下载、HTML cache 和素材迁移知道如何处理该引用；普通浏览器不能直接打开它。
这是刻意的接口变化，不是将凭证 URL 改成 Base64。

下载链路：

```text
已授权平台 metadata → 服务端媒体引用
CLI → JWT + 引用 → Gateway 重新授权
Gateway → 经审核 CDN（不携带 Gateway JWT、平台 Token、Cookie）
私有暂存 → 完整长度/哈希/秘密检查 → CLI 临时文件 → 原子替换目标
```

连接时解析 DNS 并将检查过的公网 IP 交给 socket；TLS 仍验证原始主机名。
拒绝私网/loopback/metadata 地址、混合公网私网 DNS、非 HTTPS、非 443、用户信息、
不可信域名后缀。重定向每一跳重新检查，最多 3 次。真实平台/Page 凭证即使被上游
放进 CDN URL，也不会发给 CDN。

当前服务构造器默认值：

| 项目 | 默认上限 |
|---|---:|
| 单个上传文件 / 单个下载文件 | 4,000,000,000 字节（4 GB 十进制） |
| 同时上传 / 同时下载 | 各 4 |
| 上传暂存 / 下载暂存配额 | 各 8,000,000,000 字节，独立计数 |
| 上传接收 deadline / 下载处理 deadline | 300 秒 / 600 秒 |
| 媒体引用 TTL / 数量 | 900 秒 / 4096 |
| 流式缓冲块 | 64 KiB |

这只是代码中的大小/并发边界，不是已实测 4 GB 传输吞吐。部署磁盘需预留两类暂存
合计以及其他服务空间；低带宽长传输还受 HTTP 超时、JWT 到期与上游自身限制。
这些媒体额度当前是受控构造器参数，尚未新增不受限的用户端调参接口。

上传和下载都使用**私有完整暂存**，不是零落盘。下载必须全部扫描后才能释放任何
字节，因此不能一边接收未检查数据一边发给 agent；测试证明截断、大小超限、秘密
跨块出现、取消和哈希错误均不留下用户可见的半成品。

静态 HTML 默认不联网；需要离线图片时使用原有显式 cache-images 选项。
普通商品/落地页抓取仍是无平台凭证的本地业务访问，不通过 Gateway 充当 SSRF 代理。

## 7. 素材复制与迁移

Meta 用例已经串起真实 CLI 的 export/run/resume，而非仅直接调用底层 helper：

```bash
motata meta migrate export --source-account-id 123 --campaign-id 111 --export-dir ./export
motata meta migrate run --export-dir ./export --source-account-id 123 \
  --target-account-id 456 --page-id 800 --pixel-id 900 --job-id migration-001
motata meta migrate resume --job-id migration-001
```

测试将导出时所有下载引用清空，再执行 run，验证会重新取得授权元数据。
图片采用重新查询的 original image_url，只有没有原图时才采用已有回退；视频和封面
分别上传。生成 Creative、Campaign、Adset、Ad 后保留 source→target ledger。
已确认的步骤在 resume 中不重复写入；模拟广告写入响应丢失后保持 `needs_review`。

测试涵盖当前迁移器已有的三种形态：普通链接图片、视频+封面、已有帖子。
没有声称新增原始 CLI 未实现的任意动态素材/轮播自动重建能力。

TikTok copy 的视频封面过去按 URL 上传；现在 metadata 返回的是私有引用，
因此上传适配器会先通过 Gateway 下载校验，在 CLI 私有临时目录保存，再按文件上传。
平台收到实际图片字节而不是无法解析的 `motata-download:` 字符串。复制出的三级对象
仍默认 DISABLE。TikTok copy 原先没有持久化 resume ledger，本轮没有把它描述成支持
完整断点恢复；部分失败仍返回已创建对象与恢复提示。

## 8. 测试与验收边界

本轮增加 **56 个独立测试**，Gateway 总计 **213 个**；原始 CLI 回归仍单独运行。
完整最终命令、返回码与日志以交付包 `verification/results.json` 为准。

```bash
python scripts/run_offline_tests.py
node --test tests/runtime.test.js tests/runtime-lock.test.js
python scripts/check_gateway.py
python scripts/check_gateway_endpoints.py
python scripts/check_release.py --pack-only
python scripts/build_skill_registry.py
python scripts/check_gateway.py --release
```

- `check_gateway.py` 真正执行全部 Gateway 测试，并在测试进程通过 audit hook 禁止
  真实 socket 网络；平台、CDN、issuer、JWKS 仅使用 mock。普通产品页面另有无凭证 mock。
- endpoint gate 自动从当前 TikTokClient 和 vendored SDK 生成路径关系，核对遗漏与漂移。
  它证明当前源码 route coverage，不证明所有平台业务参数组合均被线上接受。
- 原全项目 `--release` 门禁仍保持严格，未将所有命令的 coverage 改为 complete。
  `fixture_verified` 仅代表列出的实际端到端 fixture，并非逐选项穷举和生产验收。

## 9. 不在本轮伪装成已完成的事项

本轮修复了上面六组源码缺口，但不会把以下事项写成已验收：

1. 外部 issuer 的真实设备授权/签发/刷新后端和 Auth Center 权威会话、事件、远程账户绑定。
   客户端仍依赖真实可用的 issuer；没有修改其他仓库或部署服务。
2. 全 CLI 逐参数组合、未审核 Graph traversals、原先未实现创意类型、任意未来 API。
   Gateway interactive init/安装 UX、人工写回执核验等独立产品工作仍在总状态中跟踪。
3. 真实广告账户写入、线上 CDN/TLS 兼容、服务账户/Windows ACL、长期 RSS/FD soak、
   多 worker / 多副本分布式限流以及生产吞吐认证。
4. GitHub push、PR 合并、npm 发布或服务器上线。本地代码和本地测试不冒充远端 CI。

来源是当前交付源码、`gateway-endpoint-coverage.json` 和实际测试，而不是聊天里的
完成声明。后续增加接口必须补充 policy、对象证据与回归，不可以改成任意鉴权代理。
