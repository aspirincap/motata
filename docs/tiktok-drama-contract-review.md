# Drama Series 与 Smart+ 广告组更新核对

日期：2026-11-12。基于本次 Firecrawl 无缓存抓取的正文及同次 HTML 中的请求示例；修改在当前工作区，尚未发布。本轮未调用真实广告写入接口，认证仍为 Auth Center v0.3.9 API Key + Direct HTTP，不增加 JWT。

## 来源与可复核输入

| 来源 | 本地快照 | Markdown SHA-256 |
|---|---|---|
| [Drama Series 创建指南](https://business-api.tiktok.com/portal/docs/create-an-upgraded-smart-mini-series-feed-campaign/v1.3) | `.firecrawl/tiktok-drama-series-create-v1.3-20261112.md` | `b8990c0b61bad374cb06eecdd25a96ccdaf26221cd9961529a4c7bdcf0d3c0c5` |
| [Upgraded Smart+ 广告组更新](https://business-api.tiktok.com/portal/docs/update-an-upgraded-smart-ad-group/v1.3) | `.firecrawl/tiktok-smartplus-adgroup-update-v1.3-20261112.md` | `d2af9f81e2776e421a02cce93e2ee51c47b4191fd3c3bba32a8a9ae8894359df` |

抓取时间为 `2026-11-12T09:30:24.784464+00:00`；CLI 1.19.27，`maxAge=0`，等待 JavaScript 渲染 5 秒。完整原始 JSON、HTML 与 manifest 留在忽略打包的 `.firecrawl/`。更新文档请求参数表为 64 行（含嵌套字段），响应表为 144 行；身份和剧集出现在响应表，不属于更新请求字段。

可提交的 [官方示例 fixture](../tests/fixtures/tiktok_smartplus_20261112.json) 保存三个创建请求及一个增量更新请求、来源和摘要。只将占位符替换为虚构 ID、预算、日期，不包含 token。测试不依赖 `.firecrawl/` 或外网。

## 已修复差异及验证

| 范围 | 修复后的行为 | 主要回归 |
|---|---|---|
| 广告名 | `ad_name` 省略或空字符串使用平台生成名称，结果返回 `resolved_names` | 创建、resume 不重复写入；自动名称 reconcile 核对素材、身份、CTA |
| 素材身份 | `creative_info` 支持与广告组不同的完整授权身份；未选其他身份时继承广告组 | 按身份类型/BC 分组查询授权，分别验证，拒绝错层级/缺失身份 |
| 剧集模式 | native_series 与分销设置按字段是否存在互斥，null/空值不能绕过 | 双分支、空分支、有效 HTTP(S) 分销 URL；真实授权仍由平台验证 |
| 创建约束 | 保留 CBO/非 CBO 预算与 ROAS 约束；共享 bid strategy；年龄 18/25、子版位枚举、1–50 素材 | 边界、错误类型、数值相等、独立创建排期规则、官方完整请求 |
| 读取与恢复 | 区分空集合与缺失集合；兼容列表型 data；校验对象 ID、账号、父级、DISABLE | 分页重复/缺失、授权失败无写入、父级/账号不符停止 |
| 复制 | Smart+ 将平铺语言/年龄/设备等定向归入 targeting_spec，保留已有嵌套字段、子版位和 dayparting | 普通与 Smart+ 复制分别验证，普通组保持平铺格式 |
| 更新字段 | 只有 advertiser_id/adgroup_id 必填；拒绝已确认的创建/响应字段和错层级定向 | identity/series/预算模式/出价策略/pacing/status 等不能直接更新；改名示例原样发送 |
| 条件更新 | 预算、出价、排期、定向、Search 等先读取当前组及 Campaign，仅用来核对条件 | 读失败或条件不符时没有 POST；出站 body 不混入查询字段；未知字段、false/空集合/null 保留 |
| 出价和预算 | 检查目标/出价类型、ROAS 0.01–1000、出价低于预算、CBO 预算层级、自动预算增长条件 | 上下界、非法数值、当前策略不符；出价影响同 Campaign 其他组的提示走 stderr |
| 定向与排期 | 校验增量嵌套类型、枚举、数量、设备互斥、RTA、目录再营销、已知版位条件、UTC 时间与 336 位 dayparting | 不要求重新提交 locations；已有排期参与比较但不被重新发送 |
| CLI | 新增自然评论、受众建议、预算自动增长控制；布尔开关支持显式 false | `--no-*` 能覆盖 true；仅 Smart+ update 移除 `--pacing`；JSON 输出保持原响应 |

实现入口：`motata_cli/tiktok/services/drama_series.py`、`drama_rules.py`、`smartplus_adgroup_update.py`。测试入口：`tests/test_tiktok_drama_series.py`、`test_tiktok_drama_copy.py`、`test_tiktok_smartplus_adgroup_update.py`；原 HTTP 兼容测试仍保留。

## 文档差异的处理

- **创建与更新排期不同。** Drama 创建指南允许日预算配 `SCHEDULE_START_END`，官方示例也这样使用；更新表限制显式设置该 schedule_type 时必须是生命周期预算。两者分开校验。仅修改既有结束时间时，不把未提交的 schedule_type 当成新值重复校验；保留现有开始时间用于先后顺序比较。
- **增量语义。** 仅修改语言无需再次提交地区，省略字段保持省略。现有状态只用于条件判断，平台最终决定嵌套更新语义及合法性。未匹配定向模式的可选字段可能被平台忽略，不自动删改用户输入。
- **分销 URL。** 文档要求 Drama Center 中版权方生成的链接，没有 HTTPS-only 的限定。客户端接受绝对 HTTP(S) URL，不尝试从链接格式判断分销授权。
- **地区规则。** 通用更新表中 zip 的 TRAVEL_ENTERTAINMENT 限定，与 Drama 专项指南允许 location/zip 的描述存在差异；不据此在本地全面禁止 Drama zip。已明确的特殊类别/RF_REACH 禁用规则仍检查。
- **出价联动。** 更新文档将 conversion_bid_price/roas_bid 的联动目标写成其他组的 bid_price，疑似复制错误。CLI 提示可能影响兄弟广告组，保持用户提交的字段，不自动构造 bid_price。
- **dayparting。** 更新文档把全零和全一均视为全天；保留原始 336 位字符串，客户端不做归一化。

## 验证结果及边界

本轮新增 32 项测试，总计 420 项，Python 3.11 与 3.14 离线运行通过。SDK 门禁仍为 74 个原公共方法、51 个原 SDK 操作、83 个注册端点。发行物验证结果见 [实施验证记录](tiktok-http-validation.md)。

本地校验不是平台 schema 的完整副本。账户白名单（分销、子版位、自动预算增长等）、完整创意组合/资产资格、币种预算下限、语言/OS/设备等动态枚举、emoji/关键词审核、地区层级与美国不可全部移除的约束，以及 UK/HFSS/LHF 的地域适用性仍需平台或专项发现接口确认。不会自动填写法律合规声明，也不会把只读验证报告为已获投放资格。

预检 GET 与更新 POST 之间存在并发状态变化窗口，TikTok 的最终验证仍是权威。未知字段可透传，不代表客户端已经理解其新增条件；新增接口规则需要后续文档核对和测试。真实 Auth Center API Key 联调以及 Drama 禁用创建→查询→更新验收尚未完成，本轮不扩大此前的只读实测结论。
