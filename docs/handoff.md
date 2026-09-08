# Motata 更新交接｜四阶段加固

更新时间：2026-09-08（0.2.0 npm 发布）
项目目录：`/Users/mvgz0331/Desktop/motata`
基线提交：`b2d7dc1` — Release motata v0.1.9（2026-08-21）

## 1. 接手先看

- 四阶段实施、审查、源码提交与实际 Linux/Windows CI 已完成；用户确认的 **0.2.0 已发布 npm**，`latest` 为 `0.2.0`。发布源码提交 `1593e63372c22b31387fcf539d9397ee7288e182` 已快进合并至 main，标签为 `v0.2.0`。
- `package.json`、`pyproject.toml`、`motata_cli/__init__.py` 为 **0.2.0**，skills bundle 为 `motata-skills-2026-09-08`。PyPI 与 Cloudflare registry 尚未发布。
- 最终 556 个候选源码文件已完成独立复制与检查；common、services、CI、registry 和新增测试与原有修改一起纳入提交。
- 用户另行授权后已完成真实 Meta/TikTok 只读抽样验证：33 次 GET、0 次写入，32 项读取成功、1 项像素权限失败。脱敏详情仅保留于本机忽略目录 `outputs/live-readonly-20260908-163651/`；未执行 Cloudflare 部署或修改全局安装。
- **不要使用 `git reset --hard`、`git clean` 或覆盖式拉取来“清理”工作树。**本机旧产物、运行目录与历史文件也不属于本轮可删除范围。
- 本文是最新交接入口。根目录 `RECENT_REFACTOR_HANDOFF.md` 中的 2026-05 历史发布与真实账户记录不代表本轮状态，尤其“当前不是 Git 仓库”和旧版本发布建议已过时。

## 2. 本轮已完成

### 阶段一：安全与数据正确性

- `common/security.py`：统一 URL、headers、嵌套/转义 JSON、完整 Cookie 头与异常脱敏；`AuthContext` repr 隐藏 token。
- `auth_center/fetch_token.py`：指定账户跨页定位，缺失/失败不再打印包含其他账户 token 的完整 inventory。
- Meta/TikTok client：有限请求超时、安全错误信息；读请求有限重试，指标降级仅针对明确不兼容错误。
- TikTok landing pages：分页截断元数据、币种隔离、未知币种按账户隔离、分币种 top-N；不同币种不再混算全局金额。
- bootstrap 与报告：失败、部分成功和进程退出码一致；保留已创建对象和失败详情。
- Meta live validation：新增显式 `--allow-live-probe`；PAUSED/DISABLE 不再被描述为 dry-run。

### 阶段二：安装与交付

- wheel/sdist/npm 包含兼容性 JSON、必要源码与第三方声明，增加产物清单、版本和敏感文件检查。
- `lib/runtime.js`：Python >=3.11 检查、半安装恢复、健康/版本标记、跨进程初始化锁、保留调用目录、显式 npm 更新渠道。
- `requirements-release.txt`：固定构建/测试依赖版本。
- `.github/workflows/release-check.yml`：Linux 禁网测试/打包安装门禁，Windows 迁移与锁回归配置。
- `registry/skills/`：五个 skill 的唯一发布源码；构建校验格式、符号链接、秘密特征并生成文件 SHA-256。
- `.gitignore` 使用精确放行；本机根目录 skill 副本、`skills/` symlink、运行数据和生成的 public 不作为发布源码。

### 阶段三：迁移恢复

- `meta/services/migration_state.py`：schema v1、原子 UTF-8 checkpoint、源→目标 ledger、POSIX/Windows 锁。
- 每次远端写入前保存 `pending`，取得明确目标 ID 后保存 `confirmed`。
- 写入超时、中断、无目标 ID或无法持久化确认时停止为 `needs_review`，不盲目重试。
- `resume` 跳过已确认写入；校验任务配置与 export 指纹；损坏/旧格式 job 拒绝不安全恢复。
- `cleanup_plan` 仅列清理计划，不执行删除；迁移不再运行 ledger 外的隐式 create/delete probes。
- 视频上传 start/single/finish 等写入不自动重试；无法核验的 transfer 同样停止。

### 阶段四：模块化与产品边界

- 新共享层：`common/{auth,config,errors,utils,security,display,output}.py`。
- Meta 业务下沉到 `meta/services/`；service/payload/preflight 不反向导入 commands；平台包改为惰性注册 CLI。
- TikTok parser、copy/bootstrap 与 134 个 helper 抽离到 `command_groups/`、`payloads.py`、`services/`。`commands.py` 约从 8,161 行降为 4,143 行，仍保留 handlers、兼容适配器和命令级编排。
- `report/gmv_max_html.py` 随安装包交付，新增 `motata report render-gmv-max`；默认离线生成，复用已有缓存，展示数据降级警告。
- `update.py` 增加 registry snapshot 指纹；普通命令提示只读本地状态，不隐式联网或安装 skills CLI。
- README 与 skills 明确区分：数据采集、诊断、HTML 渲染、投放写入。Product intake 仅输出事实与策略提示，不声称自动完成预算/实验方案。

## 3. 必须保留的兼容性变化

| 变化 | 接手注意 |
|---|---|
| 退出码 | `0` 成功/明确的计划模式；`1` 失败或 needs_review；`2` 参数错误；`3` 部分成功/降级 |
| Meta validation | `creative`、`ad-link`、`promoted-object` 真实创建对象，必须显式 `--allow-live-probe`；`--cleanup` 仅 best effort |
| TikTok copy/bootstrap | Campaign、Adgroup、Ad 默认全部 DISABLE；激活需要显式参数 |
| 混合币种 | 全局金额/ROAS 可能为 `null`，不要当作零；排名和 top-N 限于币种/未知账户范围 |
| npm 相对路径 | 按用户调用目录解析，不再按 npm 包目录 |
| migration resume | 使用原 job ID；不能按名称认领对象，不能换新 job 绕过 needs_review |
| HTML | `report ... run` 的 manifest 不是 HTML；GMV Max 另行 `render-gmv-max`，下载图片需 `--cache-images` |
| skills 状态 | snapshot 相同不等于已安装文件逐字节一致；无法核验返回 unknown/None |

不要为了让旧调用“看起来成功”而恢复退出 0、名称复用或写入盲重试。

## 4. 已完成的验收

本轮验收环境：macOS、Python 3.14.2 与真实 Python 3.11.9；独立候选源码测试与完整打包安装验证已执行。

| 项目 | 结果 |
|---|---|
| Python 全套禁网测试 | **339 项通过** |
| Node runtime/跨进程竞争测试 | **11 项通过** |
| 排除本机凭据、输出、runtime 后的独立源码测试 | 通过 |
| registry 构建 | 5 个 skills 通过，包含文件哈希 |
| wheel/sdist/npm 清单、资源、版本、秘密扫描 | 通过 |
| 从 sdist 重建 wheel | 通过 |
| 使用本地 wheelhouse 的临时 wheel/npm 安装、CLI help、npm 渠道识别 | 通过 |
| 发布后从公共 npm 下载并隔离安装 0.2.0、runtime 标记、版本与 Meta/TikTok CLI help | 通过 |
| `git diff --check` | 通过 |

公共依赖下载到临时目录后，安装 smoke 使用 `PIP_NO_INDEX`。自动化门禁中的广告 API 均为 mock；后续真实账户抽样仅使用 GET，未运行真实写入探针。**本次已在独立候选源码目录重跑全套测试、registry 构建及完整 wheel/npm 安装验证。**新增审查修复详见 `hardening-validation.md`。

发布源码 `1593e63` 的 [GitHub Actions](https://github.com/aspirincap/motata/actions/runs/34204990637) 已通过：Ubuntu Python 3.11/3.13 各 339 Python + 11 Node；Windows Python 3.11 为 339 Python + 10 Node 通过，1 项 POSIX Node fixture 跳过。npm 发布使用该提交的原始审核包，440 个分发文件逐一匹配受控源码。

## 5. 接手复现命令

在项目根目录执行，优先使用源码命令，不假设全局 `motata` 已更新：

```bash
cd /Users/mvgz0331/Desktop/motata
git status --short
git diff --check
python3 scripts/run_offline_tests.py
node --test tests/runtime.test.js tests/runtime-lock.test.js
python3 scripts/check_release.py --pack-only
python3 scripts/build_skill_registry.py
```

完整安装 smoke：

```bash
python3 scripts/check_release.py --wheelhouse /path/to/preprovisioned/wheels
```

wheelhouse 按 `requirements-release.txt` 准备；依赖预备需要网络，不应与禁网测试混淆。临时验收目录可能被系统回收，不要把它们作为持久交付物。

## 6. 下一位的推荐顺序

1. **已完成审查并收齐源码**：保留 common、services、renderer、registry、CI、scripts 与 tests，排除凭据和本机运行产物。
2. **已完成本地门禁**：339 项 Python、11 项 Node、registry 构建与完整 wheel/npm 安装验证通过。
3. **已提交并运行实际 CI**：完整源码与跨平台修复已 push 到工作分支，Linux Python 3.11/3.13 与 Windows Python 3.11 验证通过。
4. **已发布 npm 0.2.0**：[npm 包](https://www.npmjs.com/package/motata/v/0.2.0) 的 `latest` 已指向 `0.2.0`；发布包与 `dist/0.2.0/` 审核包一致。发布源码由 manifest 和 `v0.2.0` 标识，升级说明见 [release notes](releases/0.2.0.md)。构建 manifest 中的发布前状态是历史审计快照。
5. **真实账户只读抽样已完成**：账户、对象与非空历史指标可读取并通过一致性核对；像素权限和身份分页边界见下节。任何后续 live probe、创建、更新、删除、PyPI 发布或 Cloudflare 部署仍须获得对应授权。

## 7. 已知边界与后续事项

- 真实账户验证覆盖抽样读取，不代表全部资产或写入权限通过：一个 Meta 引用像素返回 `Missing perms`；TikTok 身份列表返回条数与全零分页元数据不一致。未验证写入、迁移执行或在线投放效果，未公开账户响应和凭据。
- 不确定迁移写入需要人工核验请求回执、目标归属与 payload；没有自动 adoption/reset/delete 来绕过保护。
- TikTok copy/bootstrap 有部分成功清单，但并未具备 Meta migration 那套恢复 ledger。
- TikTok commands 仍可继续按业务域收敛，但应先保住现有兼容性测试，不再做无边界大搬迁。
- SDK 的版权/MIT 声明已随包提供；准确上游 revision 和本地改动来源仍需在下次 vendor 更新时补全。
- snapshot 验证不是本地安装内容校验；其他分析模块的时区、归因窗口和业务解释仍需使用者确认。
- 核心发布源码在 `registry/skills/`，不要继续编辑根目录重复副本并误以为会随 registry 发布。

## 8. 关联文档

- [实施计划](hardening-plan.md)
- [详细验收记录](hardening-validation.md)
- [执行与恢复契约](execution-contracts.md)
- [架构与模块边界](architecture.md)
- [构建发布工程](release-engineering.md)
- [0.2.0 发布说明与升级注意](releases/0.2.0.md)
