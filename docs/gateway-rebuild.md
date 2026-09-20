# JWT Gateway 本地重做增量（2026-09-18，历史快照）

> **2026-09-19 更新**：本文保留上一次交付的历史证据。这里提到的 BC/Smart+/GMV Max、Page、下载和完整素材迁移缺口，以及旧的 512MiB 上限，已由 [本轮补齐说明](gateway-completion.md) 更新。请以新文档为运行范围说明，不将历史限制与当前状态混用。

> 本文描述实际落盘、可复测的重做代码，不是此前失效补丁的恢复声明。
> 基线为 GitHub `1709e4bee9264ee2e84ebdfe49a2861690387153`。
> **仍是实验性分支，未完成全部 CLI 参数/接口/工作流兼容，不能作为已验收生产版本。**

## 1. 可核验的基线与交付

保留的 GitHub CI 源码快照已重建为本地 Git 仓库。本地基线提交只是快照导入，
不是远端的新提交；它的树必须等于原提交的树：

```text
remote commit: 1709e4bee9264ee2e84ebdfe49a2861690387153
baseline tree: 6581324ed68b9e90b399d50b7335e0c1c4c1281a
```

交付补丁以该树为基准。交付前必须在另一份干净基线上执行 `git apply --check`、
实际应用、`git write-tree`，并确认结果与重做源码的 HEAD 树一致。
归档中的 `verification/results.json`、原始日志和 SHA256SUMS 是结果依据。
本地提交不代表已经推送 GitHub，旧 CI 通过也不代表本次代码通过远端 CI。

## 2. 本次已接通的实现

| 模块 | 实际新增内容 | 验证范围/限制 |
|---|---|---|
| TikTok SDK | 用 `GatewaySDKClient` 接管生成 SDK 的请求构造，不创建原 SDK 的 ApiClient、REST transport 或持有真实 token 的实例 | 测试 campaign create、Smart+ update、图片/视频上传及参数拒绝；不是全部 SDK endpoint 兼容声明 |
| Meta 写入 | 对象 update/delete、图片上传、单段/分片视频、异步 insights 创建和轮询的响应证据 | 只放行服务端审查过的字段、路径；未知响应仍保留不确定写入记录 |
| 二进制上传 | CLI 64KiB 分块；Gateway 验证 descriptor、大小、SHA-256 和账户授权后，用临时文件转发 multipart | **先在服务器私有目录暂存完整文件，再发送平台**；不是零落盘边收边转发 |
| 对象查询 | 数字 Meta 对象 ID 的独立所有权探测接口；只请求固定 `id,account_id` | 有效 JWT + 账户 grant 后才进行探测；无效 JWT/grant 不查平台凭证。无法证明归属时拒绝 |
| 账户发现 | 根据当前调用者的服务端 grants 返回账户 ID | 不读取全量 token inventory；尚不保持账户发现所有 metadata/filter 参数语义 |
| 报告 | 精简 Meta 报告与 TikTok auction 报告通过真实 CLI 编排，使用 gateway-backed clients | 已测试配置为 fast、无对比/预览；空数据和有限 fixture 不等于所有非空报告/垂类全量兼容 |
| 迁移 | Meta 小型 campaign+adset 迁移和重复 run/resume；写入超时停在 needs_review | fixture 不含 creative/ad/media；不是完整跨账户素材重建验收 |
| JWT 登录/刷新 | 外部设备授权签发端的 public-client 登录，私人 session 文件和跨进程刷新锁，轮换前持久化不确定状态 | **仅实现 CLI 客户端**；本次没有新增/部署 Auth Center device/token 签发端 |
| JWKS | 固定 HTTPS 地址，缓存上限、未知 kid 冷却、并发抓取合并、原子 key-set 替换 | 网络失败且缓存失效时关闭访问；不根据 JWT 的 jku/x5u/jwk 取 key |
| 速率 | 全局/账户 token bucket，区别于并发 semaphore；取消、容量、deadline | 管理员设置值，不是平台官方额度；单进程共享，不是分布式额度 |
| Skills | 正式 SKILL 源增加 Gateway 模式优先规则，token skill 明确限管理侧 | 旧 direct-mode 参考资料保留，不意味着 Gateway init 已完成 |

真实平台 access token 与 Auth Center API key 仍只在服务端。`GatewayAuthRef` 是本地
非秘密上下文。Gateway JWT 和 refresh credential 属于另一种受限 bearer capability，
同用户进程仍可能复制它；本次不声称把客户端自身的 bearer capability 隔离于客户端。

## 3. 网关部署参数

参照 `gateway.md` 安装服务端 extra、初始化受保护状态和配置 grants。
继续使用已存在的 direct/Auth Center 两种服务端凭证来源，Auth Center 原有绑定与
15 秒默认缓存的语义不变。

在线 JWKS 配置示例见 `examples/gateway/config.online-jwks.example.json`。`jwks_path`
和 `jwks_url` 必须且只能配置其中一项；不能自动选用客户端提供的地址。

```json
{
  "state_dir": "/var/lib/motata-gateway",
  "issuer": "https://auth.example.com",
  "audience": "motata-gateway",
  "jwks_url": "https://auth.example.com/.well-known/jwks.json",
  "jwks_cache_ttl": 300,
  "algorithm": "ES256",
  "meta_version": "v23.0",
  "rates": {
    "global_rate": 20,
    "account_rate": 5,
    "burst": 20,
    "account_burst": 5,
    "wait_timeout": 30
  }
}
```

以上域名是占位符，Meta 版本是测试沿用值，不代表当下最新或适用于所有生产账户。
没有 `rates` 时 launcher 使用 20/5；显式空配置禁用速率限制，不应误当成安全生产设置。
`GatewayService` 作为测试/嵌入库直接构造时也必须明确传入 RateBudget，不能把 launcher
默认值假定为库构造函数默认值。并发配置仍默认 admitted=64、upstream=20、account=2。

速率等待在凭证获取和 `pending` 写入记录之前；等待后、凭证 I/O 后重新检查身份、
有效期和授权。已确认的写入结果回放不需要平台凭证，也不消耗新的平台速率额度。
对象归属探测同样计入平台请求额度。JWKS 网络认证本身也受 admission 上限约束。

上传限制目前由 `UploadManager` 默认控制：4 个上传、单文件 512MiB、暂存总配额 2GiB，
一次上传接收 deadline 300 秒。Meta chunked 模式按已有 5MiB 最大窗口发送，不必把
整个视频作为一个 JSON 或 bytes 对象装入内存。TikTok 单文件超过 512MiB 当前会拒绝，
不能声称所有原 CLI 文件尺寸已完全兼容。ASGI 输入 frame 上限 8MiB；CLI生产发送64KiB块。

服务器继续采用直接 TLS、单 worker、禁用不受信代理头。配置、状态、master key 和
临时目录必须在 agent 权限范围外。UDS/named pipe、实际 Windows ACL 和服务账户部署
仍未验收；不要用同用户可读 master key 的加密文件冒充权限隔离。

## 4. CLI 设备登录与续期

**前置条件**：外部 issuer 必须真正提供设备授权和 token endpoint，能够签发满足
Gateway 现有严格契约的短期 access JWT，并轮换 refresh token。不能把旧 Auth Center
浏览器 JWT 当作 Gateway JWT；本次没有声称该 issuer 已上线。

客户端配置示例：`examples/gateway/login.example.json`。配置和 session 目录在 POSIX
下分别要求 0600 / 0700；配置不含平台 secret，但仍限制权限防止认证目的地被替换。

```bash
install -d -m 700 "$HOME/.config/motata"
# 将审核后的 login 配置复制到此处，并修改为实际 issuer/gateway。
chmod 600 "$HOME/.config/motata/login.json"

export MOTATA_AUTH_MODE=gateway
export MOTATA_GATEWAY_URL=https://gateway.example.com:8443
export MOTATA_GATEWAY_LOGIN_CONFIG="$HOME/.config/motata/login.json"
export MOTATA_GATEWAY_SESSION_FILE="$HOME/.config/motata/session.json"
# 使用 session 登录时，不再同时设置 MOTATA_GATEWAY_JWT / MOTATA_GATEWAY_JWT_FILE。

motata gateway --config "$MOTATA_GATEWAY_LOGIN_CONFIG" \
  --session "$MOTATA_GATEWAY_SESSION_FILE" login

motata gateway --config "$MOTATA_GATEWAY_LOGIN_CONFIG" \
  --session "$MOTATA_GATEWAY_SESSION_FILE" status

motata meta campaigns list --account 123
```

`login` 只输出用户验证页面和 user_code，不输出 device_code、access JWT、refresh。
客户端 public `client_id` 不等于 OAuth identity ID，不包含 client_secret。
预注册客户端在签发端必须映射正确 audience 与 Gateway claims；当前 login 协议未实现
通用 discovery、跨 issuer 跳转、DPoP 或 OAuth 客户端注册。

JWT 推荐10分钟，客户端拒绝服务端返回超过15分钟的 lifetime/超范围scope。
Gateway仍独立验证JWT的签名、iss、aud、时间、scope及livegrants，不能信任CLI的检查。

刷新前将session原子写为 `refresh_pending`，移除旧refresh值，然后发送刷新请求。
收到完整合法的轮换结果才写入新凭证。网络不确定或进程中断后要求重新登录，
不能为了自动恢复反复发送可能已消费的旧refresh token。
同一session文件的多个CLI使用OS文件锁合并刷新；配置指纹变化时不转发旧session。

`logout` 只删除本地session能力，输出 `remote_session_revoked=false`。
它**不是远程注销**；服务器sid撤销仍通过现有可信管理操作或后续issuer集成完成。
登录/状态/续期全部是同步CLI功能，不需要MCP。

## 5. 幂等与恢复

当调用者未给出具体幂等键时，transport 为每次执行生成 run namespace，并从请求
指纹和调用序号生成独立键。两次刻意相同的写入不会被错误合并为一次。

- 新CLI进程默认是新的namespace，因此**不能把重跑命令当作安全重试**。
- 已有 `MOTATA_GATEWAY_IDEMPOTENCY_KEY` 保留给单一逻辑写入；不要用同一个值覆盖整个多步骤workflow。
- `MOTATA_GATEWAY_RUN_ID` 可以用于可信工作流提供稳定namespace，但顺序/步骤仍需持久化；它不是自动恢复引擎。
- Meta迁移仍依赖原有checkpoint和ledger；`confirmed`跳过，`pending/needs_review`不重发。
- 网关接收完整文件并验证hash后才开始实际上传；超时后仍可能存在不确定平台写入。
- 视频每个阶段的回执只确认该阶段，不代表整个视频完成。CLI验证offset连续和最终success。
- 本次没有agent可调用的“强制清空回执/重试”入口，也没有声称通用exactly-once。

## 6. 输出与安全边界

响应先识别可能新返回的credential字段，再执行递归字段过滤和已知秘密值检查。
嵌套JSON字符串也处理；新token被复制到普通业务字段时阻断整个结果，不返回秘密。

但派生Page token的**服务端可复用引用/缓存体系尚未实现**。当前仅能安全过滤，
不是既支持全部Page工作流又完整兼容的保证。新端点的返回schema和全部别名/嵌套关系
仍需要逐项审查。Graph禁止未审查关系、alias/modifier和敏感credential选择器。
上传会话ID是账户绑定的操作引用，不等于平台access token；它只能经Gateway使用。

文件上传读取发生在CLI侧；服务端只接收字节和元数据，不打开客户端提交的任意路径。
Signed URL下载、需要秘密的下载代理、报表所有远程图片/视频预览与跨账户素材下载
还没有完整迁移；相关命令可能失败或生成明确partial报告，不能隐藏这些缺口。

## 7. 验证与完成标准

重做新增63个独立测试，Gateway套件共157个；不要重复导入/继承原套件来增加计数。
最终以交付日志与test_cases.json的去重结果为准。

```bash
python scripts/run_offline_tests.py
node --test tests/runtime.test.js tests/runtime-lock.test.js
python scripts/check_gateway.py
python scripts/check_release.py --pack-only
python scripts/build_skill_registry.py
python scripts/check_gateway.py --release
```

最后一个命令**目前预期失败**：覆盖清单保守保留未完成状态，不通过放松门禁宣称全量迁移。
普通实验回归通过仅意味着列出的fixture通过，不是生产安全认证或真实平台吞吐结果。
没有访问真实广告账户、导入真实token、部署服务器、发布npm包或合并PR。

## 8. 仍然阻止全量发布的内容

1. 所有Meta/TikTok命令、字段组合和SDK端点逐项兼容；包括Smart+/GMV Max、BC、各种资产共享/查询。
2. 完整报告非空数据、预览下载、创意重建、Page派生凭证、TikTok copy/bootstrap和大文件的全链路恢复回归。
3. 实际issuer签发端（包含授权UI、安全refresh轮换、正确audience/session/grants）部署与Auth Center协作；客户端完成不等于issuer完成。
4. 权威的账户/OAuth身份绑定及远程撤销契约；本地grants仍权威，远端revocation可能受既有缓存TTL影响。
5. 平台真实app/credential/endpoint额度反馈、429调度、公平性压力、跨worker协调、长期RSS/FD测试。
6. 可信回执人工核验/修复界面、TLS及操作系统身份隔离实机验收、完整安装与线上CI。
7. 审核覆盖证据后逐项更新manifest，而不是批量改成complete；`init`的Gateway完整上手流程仍未开放。

## 9. 协议依据

这些是设计/实现依据，不代表外部服务已经实现或验证：

- RFC 8628，OAuth 2.0 Device Authorization Grant：`https://www.rfc-editor.org/rfc/rfc8628.html`
- RFC 9700，OAuth 2.0 Security Best Current Practice：`https://www.rfc-editor.org/rfc/rfc9700.html`
- PyJWT API（固定algorithms/issuer/audience等）：`https://pyjwt.readthedocs.io/en/stable/api.html`
- HTTPX异步请求与流：`https://www.python-httpx.org/async/`
