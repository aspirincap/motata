# 四阶段实施与验收记录

## 交付状态

四阶段代码、测试和文档已审查并收齐，按用户授权提交到 `codex/hardening-release-0.2.0`。此轮验证基线暂保持 `0.1.9`，真实 CI 通过后准备用户确认的 `0.2.0`；未执行 npm/PyPI 发布或 Cloudflare 部署。

## 2026-09-08 提交前审查与重验

- Meta 视频上传拒绝不推进、跳跃、越界和提前结束的 offset，finish 必须明确成功，避免误确认与重复写入。
- 迁移 export 缺失或错配引用 creative 时，在首个远端写入前失败。
- GMV Max 报表与 campaign list 补充分页截断信息，coverage 由来源完整性推导。
- Windows npm/npx 通过 Node 入口执行，保留包含 shell 字符的参数；畸形兼容清单回落本地，远端读取有大小上限。
- Registry 拒绝隐藏/运行产物目录和凭据文件名大小写变体；脚本指导优先使用可跨安装渠道调用的 CLI。
- Windows CI 扩为全套 Python 测试，Python/Node 使用独立步骤；USERPROFILE 指向测试临时目录。
- 555 个候选源码文件通过路径、符号链接和常见凭据特征检查；在独立复制目录中完成下表验证。构建依赖按 `requirements-release.txt` 安装到临时 venv，安装验证仅使用本地 wheelhouse。

### 阶段一

- 统一凭据脱敏（分页 URL、嵌套/转义 JSON、Cookie 完整头、异常与 AuthContext repr）。
- Auth Center 指定账户跨页查询，失败不再打印全账户 token 响应。
- 请求超时、只读重试、严格指标降级；视频写入停止盲重试。
- bootstrap/报告失败及部分成功退出码；Meta live probe 显式确认。
- TikTok 落地页分页完整性、币种隔离、分币种排名及 top-N，混合币种总金额不伪汇总。

### 阶段二

- wheel 资源、第三方声明与 npm 内容门禁。
- npm 半安装恢复、版本/健康检查、跨进程初始化锁、调用目录及更新渠道识别。
- 默认禁网单测；固定版本依赖清单；Linux 发布门禁与 Windows 迁移/锁测试 CI 配置。
- canonical registry/skills 源码、精确 ignore 规则、发布格式/符号链接/秘密扫描及文件哈希。

### 阶段三

- 版本化迁移 checkpoint、原子落盘、源目标 ledger、POSIX/Windows 文件锁。
- 写前 pending、成功后 confirmed，未知结果停止 needs_review；已确认对象恢复不重复创建。
- export/config 漂移与损坏检测、失败记录、仅计划的清理清单。
- UTF-8 checkpoint 与中断、磁盘失败、跨进程竞争测试。

### 阶段四

- common 认证、配置、错误、JSON 校验、脱敏、展示与完整性协议。
- Meta 服务、媒体、资产/发现 helper 下沉，移除业务模块对 commands 的反向导入。
- TikTok parser、copy/bootstrap 和 134 个 helper 拆分；保留旧命令和 patch 兼容入口。
- 原约 8,161 行 TikTok commands 缩至约 4,143 行，仍保留 handlers、适配器及命令级编排，不把它描述成已无业务逻辑的纯 parser。
- 可安装的 `report render-gmv-max`，离线生成、复用缓存、展示 degraded 限制。
- skills 内容 snapshot 漂移检查；普通命令不因提示自动联网或安装 skills CLI。

## 验证结果

本机：macOS、Python 3.14.2。单测以临时 HOME/MOTATA_HOME 和 socket 审计钩子隔离，Python 子进程继承禁网保护。Node 并发测试使用假 Python/pip，不安装实际 runtime。

| 验证 | 结果 |
|---|---|
| Python 全套离线单测 | 339 项通过 |
| Node runtime 与并发测试 | 11 项通过 |
| 独立候选源码复制（仅 Git 可交付文件，排除本机凭据/输出/运行目录） | 全套单测通过 |
| 独立候选源码构建 registry | 5 个 skills 成功，含 SHA-256 文件清单 |
| wheel、sdist、npm tarball 清单/资源/版本/秘密扫描 | 通过 |
| 从 sdist 重建 wheel | 通过 |
| 使用固定公共依赖 wheelhouse 的临时 wheel 安装及 CLI help | 通过 |
| 临时 npm tarball 引导安装、CLI help 与 npm 渠道识别 | 通过 |
| `git diff --check` | 通过 |

公共依赖仅下载到临时目录；安装 smoke 使用 `PIP_NO_INDEX` 和本地 wheelhouse，不修改全局环境。所有广告 API 行为均为 mock，未调用真实广告平台。

## 复现

```sh
python3 scripts/run_offline_tests.py
node --test tests/runtime.test.js tests/runtime-lock.test.js
python3 scripts/check_release.py --pack-only
python3 scripts/check_release.py --wheelhouse /path/to/preprovisioned/wheels
python3 scripts/build_skill_registry.py
```

`requirements-release.txt` 定义 wheelhouse 所需版本。正常 CI 的依赖预备阶段需要网络，测试和安装验证阶段不需要网络。

## 仍需区分的边界

- 未进行真实账户读写验收，不能由 mock 通过推导当前平台权限、API 版本与资产可用性。
- Linux Python 3.11/3.13 与 Windows Python 3.11 的实际 CI 由本次分支 push 触发；远端结果在版本准备时记录。
- 迁移无法可靠关联的未知写入需要人工核验；不提供绕过保护的自动收养或自动删除。
- TikTok copy/bootstrap 有部分成功清单，但不是 Meta migration 的可恢复 ledger 工作流。
- skill snapshot 哈希一致不等于本机已安装内容逐字节验证；无法验证时返回 unknown。
- 第三方 SDK MIT 声明已随包提供，确切上游 revision 尚未确认。
- 已有其他分析模块的时区、归因和业务判断仍依赖上游数据及使用者确认；本轮完整性修复不构成所有平台统计的数学正确性证明。
- 发布源码包含新增 common/services、renderer、registry、CI、scripts 与 tests；本机凭据、缓存、运行目录及生成的 registry public 不进入提交。
