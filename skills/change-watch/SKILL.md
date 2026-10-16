---
name: change-watch
description: 监控指定目标（公司/产品线/模型）的变更并沉淀为可查询的变更流水。适用于：想知道某公司最近发布了什么/下架了什么/改了什么价；要给某个产品线做持续变更监控并定时通知；要按时间线、类型、对象查看与检索历史变更并溯源到原文；需要"先分析判断再通知"，而不是把原文直接转发。触发场景举例："Google 最近有什么更新"、"盯着 Gemini 的模型上下架"、"这类变更定时推给我"、"上次那个模型的变更记录在哪"。
description_en: Monitor changes of a target (company / product line / model) and accumulate them into a queryable change ledger. Use when you want to know what a company recently released / discontinued / repriced; run continuous change monitoring for a product line with scheduled notifications; retrieve and trace historical changes by timeline, type, or object back to the source; and "analyze-then-notify" rather than forwarding raw text. Trigger examples: "What's new from Google", "Watch Gemini's model additions/removals", "Push these changes to me on a schedule", "Where's the change record for that model".
version: v20261016
license: MIT
agent_created: true
---

# change-watch（变更雷达）

把一个目标（公司 / 产品线 / 模型）的变更**持续抓到、判断过、沉淀下来**，并只在值得的时候通知。

**分工（这是本技能的核心设计）**

| 谁 | 做什么 | 为什么 |
|---|---|---|
| **脚本**（确定性） | 抓取、指纹、与上一轮比对、落盘、查询 | 可复现、不花钱、可无人值守 |
| **模型（你）** | 读变更集 → 按准则判断类型/重要性 → 写说明 → 决定是否通知 → 渲染视图 | 这是判断，不是查表 |

**只有变化进判断，不是全量。** 每轮跑完先看 `out/latest.json`，里面只有变化。

## 心智模型：两层状态

只订阅"新内容"发现不了**下架** —— 下架通常不发公告。所以有两层：

- **观测流（stream）**：看"谁发了新东西"。三种 kind：`rss`（RSS/Atom 订阅）；`json_ld_posts`（页面内嵌 schema.org JSON-LD 的 `SocialMediaPosting`，**LinkedIn 的 showcase 页免登录就是这个结构**，能拿到全文 + ISO 时间）；`x_timeline`（**X/推特免登录 guest 通道**：激活 guest token 后走 GraphQL，能拿到推文全文 + 精确时间 + 推文链接，默认跳过 RT）。
- **对象登记册（registry）**：网页里的清单表（模型清单、弃用清单），看"某个东西还在不在、状态变没变"。

对象从清单里消失 = 下架；`Retirement date` 出现 = 计划下线。这两类都只能靠 registry 拿到。

## 工作流

### 第 0 步：确认工作区

**装好技能后先跑一次初始化检查**（不跑也能用，但能提前暴露环境 / 配置问题，强烈建议）：

```bash
python3 <技能目录>/scripts/watch.py --init                 # 交互模式，末尾等你确认
python3 <技能目录>/scripts/watch.py --init --non-interactive   # CI / 自动化
```

它会：检测 Python 版本；确认工作区与 `targets.json` 就位；探测网络能否到 Google 源（国内 / 沙箱可能不通，会明确提示"是网络问题而非技能坏"）；检测飞书发送通道（`lark-cli` / 连接器），都没有时告知通知会落盘 `out/notify-unsent-*.md` 并提醒。

**推送目标（飞书 ID）的配置由 AI 协助完成，通道由 AI 依环境决定，脚本绝不写死**：

- **AI 驱动（推荐，也是本技能预期用法）**：AI 运行 `python3 scripts/watch.py --init --non-interactive`，读取脚本报告的「待办」（缺哪些 ID、各自怎么查）；然后 AI **自己判断**当前环境可用的通道（已绑定的飞书连接器 / 本机 `lark-cli` / 其他），**先和用户确认推送目标是什么、发给谁**，再用该通道查到 `open_id` / `chat_id`，最后运行 `python3 scripts/watch.py --set-user-id=<openId> --set-chat-id=<chat_id>` 写回（或直接编辑 `targets.json`）。**脚本在非交互模式下不会自行解析或写回任何飞书 ID**——这是 AI 的职责。
- **人类交互（无 AI 时）**：在终端直接运行 `python3 scripts/watch.py --init`，脚本会问「通知推送到哪里」——私信给我自己 / 发到指定群（按群名搜索）/ 两者都要 / 暂不配置；选定后**仅作为便利层**尝试用本机 `lark-cli` 解析并和你逐步确认后写回；群名搜到多个会列出让你选序号；无 `lark-cli` 时只给指引 + 生成 `out/notify-setup-needed.json` 待办。
- **关键边界**：无论哪种模式，**实际查询通道由 AI / 宿主环境决定**（飞书连接器 / `lark-cli` / 其他），脚本只负责「确认意图 + 在可用时便利尝试 + 查不到时产出待办与指引」，任何情况下都不强制绑定某一种方式，查不到也不报错 / 阻塞。

> 手动亦可：`notify.user_id` = 私信目标 openId（空=不发私信）；`notify.chat_id` = 额外发到的群（占位符 `REPLACE_WITH_YOUR_GROUP_CHAT_ID` / 空 = 不发群）。两者都空则完全不推送，仅落盘 `out/notify-unsent-*.md`。

默认工作区 `~/.change-watch/`。用户要换位置就加 `--workspace <目录>`；**要用多个 AI 工具共享同一份沉淀，就让它们指到同一个目录**（例如放在同步盘里）。

首次运行会自动把 `config/targets.json.example`（脱敏模板）复制成 `<工作区>/targets.json`。**此后改工作区那份**（增删目标/信源不用改代码）。

> **模板与 live 的关系**：仓库只收脱敏模板 `config/targets.json.example`；`config/targets.json` 已被 `.gitignore` 永久排除——live 配置里的真实推送 ID（群 `chat_id`、个人 `openId`）永不入库，也不再需要 assume-unchanged 之类的本地标记（那种标记是粗粒度屏蔽，会连带挡住模板的正常更新）。改完 live 后跑 `python3 scripts/sync_template.py` 即可把新增的源/设置同步进模板（脚本自带真实 ID 泄漏扫描，命中即拒绝写出）。

工作区里有什么：

| 路径 | 内容 |
|---|---|
| `targets.json` | 目标 / 信源配置（改这份生效） |
| `state/streams/*.json`、`state/registries/*.json` | 上一轮快照（差分的对照物） |
| `state/health.json` | **源健康**：连续失败次数、状态（`ok`/`flapping`/`down`）、首次失败时间（用于"已持续多久"） |
| `state/pending.json` | **待判队列**：本轮待判断的变更，`log_change.py` 判完核销 |
| `out/latest.json` | 本轮结果（`summary` / `health` / `stale_sources` / `changes`），**先看这个** |
| `log/changes.jsonl` | 已判断并落盘的变更流水（检索的对象） |
| `log/discarded.jsonl` | 判为「不是真变更」的条目（带理由，不进流水但留痕） |
| `log/unjudged.jsonl` | 未判超过 24 小时、被放弃的变更（**沉默必须有据**，不允许悄悄消失） |
| `log/rolled_out.jsonl` | 被判为「窗口滚动」的条目（见下），不是变更、不占判断配额，但留痕可查 |
| `log/runs.jsonl` | 每轮扫描的小结（只有计数） |

#### 有限窗口流：滚动 ≠ 消失

X 时间线、LinkedIn 展示页这类源只暴露「最近 N 条」窗口。新内容进来时，最旧的几条会被挤出窗口——
它们**不是被删除**，但按朴素差分会报成 `removed`，于是每次滚动都制造一批"下架"假告警要 AI 判。

已声明 `windowed: true` 的源，脚本会把这类条目单独识别出来（三条件同时成立才判为滚动：
**源声明 windowed** + **本轮确实有新条目进来** + **消失条目早于当前窗口最旧的条目**），
它们不进变更集、不占判断配额，只追加到 `log/rolled_out.jsonl`。
任一条件不成立（比如本轮没新增、或消失的是中间条目、或时间比不出来）就**按真消失处理**——
宁可多报，也不把真下架当成滚动吃掉。用 `query.py --rolled-out` 可查。

**配置写错会直接中止**：`watch.py` 启动时校验字段白名单，不认识的键（比如把 `stale_after_hours`
拼错）会点名报错并退出 —— 静默忽略会让"看起来在跑、其实没生效"，必须让它失败。

### 第 1 步：首次 —— 建基线（不推送）

```bash
python3 <技能目录>/scripts/watch.py --baseline
```

把每个信源当前全部条目、每张清单表当前全部行写进 `state/`。**这一轮不产出变更、不通知** —— 否则会把历史当新消息推。

同时检查输出里的**停更告警**：标了 `<STALE>` 的源很久没出新条目了，告诉用户。

### 第 2 步：常规一轮

```bash
python3 <技能目录>/scripts/watch.py
```

各信源**并发抓取**（26 源实测 38.7s → 12.5s），但结果与串行等价：结果按配置顺序返回，
X 通道串行（共用 guest token、限流最严），单域名限流 `host_concurrency`，整轮有壁钟上限。
想退回串行做对照排查，把 `settings.collect_concurrency` 设成 `1` 即可。
同一工作区同时只允许一轮在跑：拿不到锁会**跳过本轮并返回 3**（不是故障，别重试覆盖）。

产出 `<工作区>/out/latest.json`。**先看 `summary`**：

- `total == 0` → 本轮无变更，到此为止，不要调用模型判断，也不要通知。
- `total > 0` → 进入第 3 步。

**无论有没有变更，都顺手看这三块**（它们本身就是有价值的信息）：

| 字段 | 含义 | 怎么处理 |
|---|---|---|
| `health.down` / `summary.sources_down` | 源**连续失败 ≥3 次**，疑似真失联 | 要**点名**告诉用户。偶发一次失败（`health.flapping`）不必报，避免噪音 |
| `health.recovered` | 前几轮失败、本轮恢复的源 | 一句话提一下，让用户知道「源活了」 |
| `stale_sources` | 停更源（含 `label` 中文名与所用阈值） | 用里面的 `label` 点名；这是**信源侧**的静默，不是脚本故障 |

失败与停更的判定已由脚本维护（`state/health.json`：连续失败计数、状态分级、恢复识别；停更按源阈值判定），**不用你算**。默认全局阈值 7 天，单个源可用配置里的 `stale_after_hours` 覆盖（写 `null` = 不检查）。

### 第 3 步：逐条判断（你来做）

读 `references/criteria.md`，对 `changes` 里**每一条**回答四个问题：是不是真变更、什么类型、多重要、要不要通知。

- **不真的（营销稿、传闻、只发预告、例行小版本）→ 写 `"discard": true` 加一句 `discard_reason`**。
  别用"我不写它"来表达丢弃：什么都不写 = 漏判，它会被反复带回重问，超过 24 小时未判才进 `log/unjudged.jsonl`。
- **同一事件多家报道 → 合并成一条**，`sources` 列全，并把被合并掉的 `change_id` 写进
  `merged_change_ids`（否则覆盖度校验会把它们当成漏判）。
- **判不出来（材料不足）→ 写 `significance: "unknown"`**：落盘但不通知。别硬判。
  注意「判不出来」≠「判为无关」：前者是 `unknown`，后者是 `discard`。
- **凡是要通知的，都必须判断过。**

**每条判断都要原样带回 `change_id`**。这是本技能防"漏判即永久丢失"的关键：

- `watch.py` 一跑完状态就推进了，某条变更**这轮没判，下轮就不会再出现在 diff 里**。
- 所以未判的会留在 `state/pending.json`，下轮 `watch.py` **重新带回来**再问一次。
- 过期按**未判年龄**算（默认 24 小时，见 `MAX_PENDING_AGE_H`），**不按采集轮次**——
  所以采集跑得再频繁（cron/launchd 高频）也不会把未判的变更提前吃掉。
- 超过 24 小时仍没判 → 写进 `log/unjudged.jsonl` 并点名，不再重问。沉默必须有据。
- **`log_change.py` 会核对覆盖度，漏一条就点名告诉你缺哪个 `change_id`。**

> **漏答不是"判断为无关"，是失败。** 别用"先记录已判的、剩下的以后再说"糊过去——
> 剩下的会自己回来找你。

把结论写成一个 JSON 文件（字段见 `references/criteria.md` §七），然后：

```bash
python3 <技能目录>/scripts/log_change.py --input judged.json
```

它会校验字段、核对 `change_id` 覆盖度、核销待判队列、追加到 `log/changes.jsonl`，
并把要通知的内容写成 `<工作区>/out/notify-<时间>.md` 与 `out/card-<时间>.json`。

**判断完但落盘失败时，不要重新判断** —— `judged.json` 就是那份已经拿到的结论，重跑
`log_change.py` 即可（重跑是幂等的：已记的会按 `change_id` 跳过）。

### 第 4 步：通知

**卡片不要手拼**：第 3 步跑完 `log_change.py` 已经产出 `out/card-<时间>.json`（标题、中文标签、来源名、末尾统计都在代码里写好），**直接发这份 JSON**。只有「无变更但需要知会」这类变体才需要照 `references/notify-card.md` 自己拼。

然后按 `references/notify-card.md` 的**发送降级链**依次尝试：

1. **本工具自己的连接器**（飞书等）发 interactive 卡片 —— 优先 bot 身份（用户身份常缺发消息权限）；
2. 连接器不可用 → 本地 `lark-cli`（bot 身份，命令模板在文档里）；
3. 都失败 → 简报全文写入 `<工作区>/out/notify-unsent-<时间>.md`（含失败原因），并在会话内**明确提醒用户**，不允许静默吞掉。

写卡片时守三条（细节见 `notify-card.md`）：

- **标题写清在监控什么**：`<目标短名> 监控简报`（例「Google 监控简报」），不要写成技能名。
- **一律用中文人话**：`normal`→常规、`low`→次要、`removed`→已下架；源的 `x-google` 用中文 `label`；不上脚本名、文件路径、机器枚举值。
- 同一条变更不重复推（`log_change.py` 已去重）；**无变更时不发卡**（不打扰）；测试性质的卡片必须在头部标注「链路测试」。
- 卡片已发出但内容要改 → 用 `lark-cli im messages patch` **原地更新**，不要重发一张。

**无变更但需知会（扫描状态简报）**：当本轮无变更、但用户主动询问，或扫描有明显异常
（失败 / 停更多起来）需要知会时，用 `python3 scripts/log_change.py --status-card` 生成
`out/card-status-<时间>.json`（notify-card.md §五 变体），直接发送即可。它从最近一次
`out/latest.json` 取扫描结论与异常源，无需手拼，标题按单/多目标自动处理。

**群聊强提醒（高优变更额外 @ 群）**：在 `targets.json` 顶层配 `notify` 块（`chat_id` + `mention`），
当某目标含高优（high）变更时，`log_change.py` 会**额外**产出 `out/card-<目标>-<时间>-group.json`
（红头 + 顶部「⚠️ 重要变更，请关注 @」横幅），按降级链发到该群即可（发送命令把
`--user-id <openId>` 换成 `--chat-id <chat_id>`）。私信卡照常发，群卡只在有高优变更时出现 ——
实现「常规私信 + 高优群 @」两段式提醒。状态简报若本轮有抓取失败的源，同样会额外产出
`*-group.json` 并发群 @。`mention` 取值 `all`（@所有人，需群主在群设置开「@所有人」权限）
或 `[open_id, ...]`（@ 指定人）。群 `chat_id` 用 `lark-cli im +chat-search --query 群名` 查。

**通知纪律（双通道 + 三刀）**：本技能把"通知"拆成两条相互独立的通道，互不干扰：

1. **变更通知（实时 · 全级别）**：`log_change.py` 落盘时，凡 `notify:true` 的条目**当场发**（按目标分别发卡）。`high`/`normal`/`low` 默认都通知（实时性：变更不拖延，摘要与变更分开、变更全级别正常发）；`unknown` 强制不通知；AI 显式写 `notify:false` 才抑制。级别只影响卡片样式（红头 / @），不影响"发不发"。
2. **每日变更摘要（默认开启 · 增强项）**：`python3 scripts/log_change.py --digest` 聚合**自上次已交付摘要以来**新入账的变更（delta 模式：窗口 = (上次锚点, 本次运行]，按严重度 / 目标 / 类型统计），并附当前不可用源，单卡回顾；有无变更都发（间隔够长，不作心跳）。默认每天 **08:30** 由宿主自动化跑一次（配置见 `targets.json` 的 `digest` 块：`enabled` 默认 `true`、`mode` 默认 `delta`（`day` 可回退旧自然日行为）、`seed_hours` 首跑无锚点时的回溯时长、`include_low` 控制是否含次要变更）。delta 下 `--digest` **只产卡 + 写 pending，不推进锚点**，须发送成功后再跑 `--digest --commit`（宁重勿漏：发送失败不 commit，下次自动重覆盖）。**发给谁（本人私信 / 群）由自动化提示词决定——脚本只产出 `out/card-digest-<stamp>.json`，不负责发送。**
3. **源健康一次性告警**：`python3 scripts/log_change.py --health` 只对**源健康跃迁**发一次——`ok→down`（不可用）与 `→ok`（已恢复）各一次；持续失联（稳定态无跃迁）**静音**，不会每轮刷屏；`flapping` 等中间态不告警。去重靠 `state/health_alerts.json`，对同一份 `latest.json` 重复跑也不会重发。

三刀（防打扰，不是防通知）：**不重复发**（变更按 `change_id` 幂等去重；健康告警按跃迁去重，同一持续故障只响一次）、**空跑别发**（0 条可通知变更 → 完全静默，连状态卡都不发）、**噪声别发**（只有判为「非真变更」`discard` 才不进流水；`low` 是真·小变更，照常通知，`low`≠噪声）。

> `python3 scripts/log_change.py --status-card` 仍保留：它是「无变更但你想看完整健康全景」时的按需变体（停更 + 抓取失败分两节独立呈现，已修过飞书 lazy-continuation 渲染坑）。日常无需主动跑它。

### 第 5 步：查看与检索（随时）

```bash
python3 <技能目录>/scripts/query.py --timeline          # 时间线，按天分组
python3 <技能目录>/scripts/query.py --type 模型下架      # 按类型
python3 <技能目录>/scripts/query.py --object gemini     # 按对象（子串匹配）
python3 <技能目录>/scripts/query.py --since 2026-10-01 --grep 定价
python3 <技能目录>/scripts/query.py --stats             # 各类型/目标/重要性计数
python3 <技能目录>/scripts/query.py --rolled-out        # 被判为窗口滚动、未当作变更的条目
python3 <技能目录>/scripts/log_change.py --show         # 最近 20 条
```

"各类产品情况"就是 `--object` + `--stats`；不需要额外页面。

### 定时（本技能不自带调度）

"及时通知"靠宿主工具的定时能力。本技能跑通需要「脚本抓取 → 模型判断 → 发送」三段，**定时任务必须把三段都串起来**——只跑 `watch.py` 会漏掉判断与通知（变更只会积压在 `state/pending.json`）。

**两种节奏（按需求选一种或并存）**

| 模式 | 适用 | 频率示例 |
|---|---|---|
| **固定时刻巡检**（日报型） | 每天固定时间出一份汇总 | 每天 05:00、每天 09:00 |
| **间隔巡检**（实时型） | 想要"变了尽快知道" | 每 30 分钟、每 1 小时、每 10 小时 |

节奏理想值 **15~30 分钟**：脚本很轻（26 个 HTTP GET，并发抓取约 12 秒），判断只在有变更时才跑。**间隔越长，最坏延迟 = 间隔长度**，别用"每天一次"当主巡检（最多做每日汇总）。固定时刻 + 间隔可并存（例如每 30 分钟轻巡检 + 每天 05:00 日报）。

**自动化 prompt 模板（粘贴到任意 AI 工具的「定时 / 自动化」能力，替换占位符即可）**

```text
你正在按计划执行 change-watch 巡检。依次做：

1. 抓取与差分：运行 `python3 <技能目录>/scripts/watch.py`。
   退出码 1 = 配置错误（看 stderr 并报告，不要静默）；退出码 2 = 有源失败但仍有结果，或 `log_change.py` 本轮判断不完整/有非法记录（覆盖度缺失、判断记录格式非法，pending 已保留、下轮再问）——这两种都继续后续，不要中止整条流水线；退出码 3 = 另一轮 watch 正在运行（单实例锁未拿到），本轮跳过，**不是故障，不要重试覆盖**，等下一轮即可。
1.5 源健康一次性告警：运行 `python3 <技能目录>/scripts/log_change.py --health`（读本轮 latest.json 的跃迁；不可用/已恢复各发一次，持续失联静音；无跃迁则静默）。按 references/notify-card.md 降级链**发本人私信**（群聊不重复发，避免刷屏）。
2. 读 <工作区>/out/latest.json 的 summary：
   - total == 0 且 health.down 为空、stale_sources 为空 → 本轮干净，结束（默认不发卡、不打扰）。
   - total == 0 但存在失败/停更源 → 运行 `python3 <技能目录>/scripts/log_change.py --status-card` 生成状态简报卡，按 references/notify-card.md 降级链**发本人私信**（若有抓取失败源会额外产出群卡，一并发群 @）。
   - total > 0 → 进入第 3 步。
3. 逐条判断：读 references/criteria.md，对 changes 中每一条给结论（真变更 / discard / unknown），**必须原样带回每条的 change_id**。写 judged.json（字段见 criteria.md §七）。
4. 落盘与生成卡片：运行 `python3 <技能目录>/scripts/log_change.py --input judged.json`。漏判会报错点名，必须补全再发。
5. 按接收方路由发送（卡片已由脚本产好，直接发，不要手拼）：
   - 实时变更卡 `out/card-<目标>-<stamp>.json` → **本人私信**（`--user-id`）。
   - 高优群强提醒卡 `out/card-<目标>-<stamp>-group.json`（仅 high 变更时出现）→ **群**（`--chat-id` + @）。
   - 无变更时不发（脚本已保证）。
   - 每日「变更摘要」卡 `out/card-digest-<stamp>.json` 由本技能的「固定时刻摘要任务」单独产出并**发群**（见下方推荐组合），本巡检不重复发。

工作区默认 ~/.change-watch/。不要手拼卡片，直接发脚本产出的 JSON。测试卡必须在头部标「链路测试」。
```

> 占位符：`<技能目录>` = 技能解压路径；`<工作区>` = 默认 `~/.change-watch/`（若 `--workspace` 改过就填实际值）。

**纯脚本兜底（无 AI 时也能跑抓取）**：用 `cron` / `launchd` 定时跑 `watch.py`（只抓取与差分，很轻），变更再人工 / 半自动唤起判断：

```cron
# 每 30 分钟抓取+差分（轻量，约 26 个 HTTP 请求，并发约 12 秒）
*/30 * * * * cd <技能目录> && python3 scripts/watch.py >> ~/.change-watch/log/runs.log 2>&1
# 每天 05:00 完整巡检（若你的工具支持命令行调起 AI 判断）
0 5 * * * cd <技能目录> && python3 scripts/watch.py >> ~/.change-watch/log/runs.log 2>&1
```

> 兜底方案只完成"抓取"段；判断 + 通知仍需 AI 工具读 `out/latest.json` 后跑上面的第 3–5 步。
>
> ⚠️ **两点提醒**：① 用系统调度单独跑采集时，`--health` 也要跟在**每次**采集后跑（跃迁会被下一轮覆盖，见下条），否则健康告警会丢；② 部分环境（如受管沙箱）**装不了** `cron` / `launchd`，此时就用上面的"巡检通道"同轮耦合方案，效果等价且延迟更低。

**推荐的自动化组合**（宿主工具按此建两条定时任务即可）：

- **巡检通道（间隔触发，建议每小时）**：跑一遍上面模板的第 1–5 步——`watch.py` → `--health` → 空跑网关 → 判断 → `log_change.py --input` → 发送。变更全级别实时发**本人私信**，高优额外发**群 @**；源健康跃迁一次性告警发**本人私信**。
- **每日摘要（固定时刻）**：每天 **08:30** 只跑 `log_change.py --digest`（**不要再跑 `watch.py`**，采集已由巡检通道完成）。**delta 模式**（默认）：聚合「自上次已交付摘要」到本次运行之间新入账的变更 + 当前不可用源；窗口首尾相接、多日间隔自动吸收、不重不漏（消除自然日零点的约 15.5h 盲区）。`--digest` **只产卡 + 写 pending，不推进锚点**；须在摘要卡**发送成功后**再跑 `log_change.py --digest --commit` 推进锚点（宁重勿漏：发送失败不 commit → 下次自动重覆盖重试）。`digest.mode=day` 可回退旧自然日行为。**按接收方分流：健康卡发本人私信，摘要卡发群**（群是定时汇总的接收方；本人只收实时变更卡与健康告警，不在群重复收摘要）。

> **调度粒度与延迟（实测）**：多数宿主定时任务的最细粒度是**每小时**（`FREQ=HOURLY;INTERVAL=1`；`MINUTELY` 普遍不支持，`BYMINUTE` 在 HOURLY 下会被忽略）。
> 由于采集与判断在**同一轮**内完成，端到端延迟 = 轮次间隔 + 采集耗时 ≈ **典型 30 分钟、最差 1 小时**。
> 注意：**通知延迟由 AI 轮次频率决定，与采集频率无关**——把采集单独高频化（cron）并不能让通知更快，反而因「采集间隔 + 判断间隔」叠加而更慢。
> 因此**采集与判断绑在同一轮是更优解**，不是妥协。

> **`--health` 必须紧跟采集、每轮都跑**：源健康跃迁只在状态变化**那一轮**的 `latest.json` 里出现，下一轮即被覆盖。若只在每天固定时刻跑 `--health`，两次之间的跃迁会**永久丢失**。

> 摘要默认开启（`targets.json` 的 `digest.enabled` 默认 `true`）；想关掉就把 `enabled` 改成 `false`。

## 已知陷阱（都是实测出来的，别踩）

1. **`ai.google.dev` 对普通 User-Agent 返回 302 无限循环** —— 看起来像"页面不存在"。脚本已内置完整浏览器头 + CookieJar，**不要**为了"简化"把它去掉。
2. **发布说明类的 RSS 是"活文档"，别被它的更新节奏吓到**：`cloud.google.com/feeds/gcp-release-notes.xml` 每条 = 一天的汇总，Google 会在当天反复回填编辑（实测单条一天内变更多次），且 **30 条 entry 共用同一个链接**。脚本已按 Atom `id` 建键（见陷阱 19）；看到该源报整批 `changed` 时，先怀疑键退化成了位置序号，别当成"内容真变了"。注意区分：**Vertex AI Release Notes 那个 feed 实测停更约 7 个月**（不要配置它）；GCP 发布说明 feed 是活的，今天仍在产出真实变更。
3. **时间戳不可跨源比较**：同一篇公告，`blog.google` 与 `cloud.google.com` 的自报时间实测差约 7 小时。记录时各源各记。
4. **`blog.google` 系列是唯一自我一致的时间基准**：RSS 时间戳 == 页面自报时间、带明确 `+00:00`。要判定"哪个更新"，以它为准。
5. **清单页会改版**：`html_tables` 依赖表格结构。若某 registry 突然报"整表消失"，先怀疑页面改版，再去人工看一眼，别直接当成"全部下架"。
6. **X（推特）免登录走 `x_timeline`（guest 通道），但有三个已知风险**：① X 对游客有**限流**——guest token 每次运行激活一个、同 token 别狂刷，几十个账号以内没问题，429 会以源失败的形式显式报错；② GraphQL 的 queryId 会**轮换**——失效时表现为全部 X 源报错（源会标 FAIL，不会误判成"推文消失"），届时从开源库 `@the-convocation/twitter-scraper` 提取新端点串，改源配置里的 `user_tweets_url` / `user_by_screen_name_url` 或更新脚本常量即可；③ 该通道是**非官方接口**，X 随时可能收紧，别把它当唯一信源。**不要**在配置里直接写 x.com 的页面 URL 让它当 RSS 抓——页面是 JS 渲染的，抓不到推文。
   - **guest 通道的抖动由脚本自愈**：实测冷启动首连握手要 15~16 秒（早期写死 20s 超时，余量只剩 4 秒，真实出现过一次 SSL 握手超时把整源判死并触发一次源健康告警）。现在 guest token 激活放宽到 30s，且对 **429 / 5xx / 连接与读超时退避重试 2 次**；401/403 会**作废 token 重取**再试。**404（queryId 失效）与 GraphQL 业务错误不重试**——重试也没用，要立刻让人看到。
7. **LinkedIn 只能走 `json_ld_posts`**：showcase 页免登录内嵌 JSON-LD（含全文 + ISO 时间 + 帖子 URL）。解析器在拿不到帖子时会**显式报错**（authwall 拦截 / 页面改版）——这是刻意设计：源会标 FAIL 进入失败统计，而不会被误读成"全部帖子消失"。别把它改成"容错返回空"。LinkedIn 页面加载慢，给该源单独配 `timeout_seconds: 60`。
   - **LinkedIn 的可见集合会抖动**：showcase 页每次返回的 10 条并不严格是"最新 10 条"，实测出现过"较新的帖子消失、较旧的帖子出现"。这种情况下消失条目**早于窗口最旧**这个条件不成立，所以不会被误判成滚动，仍会作为 `removed` 交给 AI 判断——这是刻意的保守。**X 与 LinkedIn 都建议配 `windowed: true`**，能消掉大部分"窗口滚动"型假告警，但消不掉集合抖动型噪声。
8. **停更阈值要贴着源的节奏，别一律用默认 7 天**：实测 `android-dev-blog`（167.1h）与 `blog-google-gemini`（168.1h）的最新条目正好压在 168 小时两侧，会**每轮来回跳变**，纯属噪音。周更来源单独放宽（配置加 `stale_after_hours: 336`），低频来源显式写 `null` 完全不检查。**加新源时先看它自己的更新节奏再定阈值。**
9. **`html_tables`（对象登记册）没有"停更"概念**：清单页自带时间戳的很少，脚本对 registry 不算 `stale_hours`，所以 registry 永远不会被标停更——这是已知取舍。别把"registry 没报警"理解成"清单没变化"；清单变化靠**条目差分**发现。
10. **未判的变更会自己回来，别嫌烦**：`state/pending.json` 里的条目下一轮会重新出现在 `changes` 里（带 `reopened: true`）。这是刻意设计——不这样做，漏判就等于永久丢失。会一直带回直到判定，超过 24 小时未判才进 `log/unjudged.jsonl`。
11. **配置里出现未知字段会直接中止运行**：`watch.py` 用字段白名单校验，拼错的键会被点名。别去"绕过"它，那是防止配置漂移变成"看起来在跑其实没生效"的闸门。
12. **说明里的版本号/日期/价格会被核对**：`log_change.py` 逐个比对原文，对不上会标 `grounding: unverified` 并在 stderr 点名。看到就回原文改，别放着不管。
13. **表格里的 colspan 分组行不是数据**：清单页常在每个分组之间夹一行单单元格的小节标题（实测 `ai.google.dev` 弃用清单有 14 行 "Preview models"）。它们第一列同名，会被加 `#2 #3 …` 后缀；页面只要增删一个分组，后缀就整体错位，凭空造出一批"变化/消失"。脚本现已跳过这类行（判据：本表典型列数 ≥2 且该行只有 1 列且文本很短）。**别把判据放宽成"列数少于典型"** —— 实测会误删 `gemini-api-models` 里 20 条 2 列的真实记录（名称 + 模型 ID）。
14. **`log_change.py` 的漏判点名依赖 `out/latest.json`**：覆盖度 = 本轮变更 id 减去已判 id。若该文件不存在，基准为空集，校验会**静默判定为全部覆盖**，漏判无人点名。脚本现已在这种情况下显式告警并回退出码 2。手工补录（不跑 `watch.py` 直接 `--input`）时知悉该校验被跳过。
15. **解析不出内容 = 源失败，不是"抓到了 10 条"**：RSS 解析器遇到"标题全空"会显式报错让源标 FAIL（LinkedIn / X 同理）。空条目最危险的地方是看起来抓到了，但差分只认 URL——正文改了发现不了。已实测过一次：`developer.chrome.com` 的 feed 用 CDATA 包裹正文，剥标签时把整段 `<![CDATA[...]]>` 当成一个标签删掉了，10 条条目标题与正文全空却报 ok。某源突然 FAIL 时，先人工看源是不是改版了。
16. **X 的纯媒体帖按约定直接忽略**：视频/图片帖常常没有任何正文，`full_text` 只剩一条指向**本条推文自己媒体附件**的 t.co 短链——无可读文本，判不了任何东西。脚本对"正文仅剩一个 t.co 短链 + 有媒体实体 + 没有文章链接"的推文**不进基线、不产变更**（用户明确要求"纯视频忽略"；曾每天制造 3 条"待确认"噪音）。**带了图说的视频帖不受影响**（有正文就照常保留）。这是产品决定不是 bug——若哪天要监控"无正文官宣视频"，把这段判断改掉并重新基线。
17. **X 长文（note tweet）的全文不在 `full_text` 里**：超过约 280 字的长帖，`legacy.full_text` 只是截断的展示版，完整正文在 `note_tweet.note_tweet_results.result.text`。只读 `full_text` 会静默丢内容——实测 Gemini 4 Argon 定价公告的后半段（$2/$10 每百万 token）就只在 note 里。脚本已优先取 note 全文。
18. **X 文章（Article）的正文也不在本接口里**：长文帖的 `full_text` 只剩一条指向 `x.com/i/article/…` 的短链，全文取不到（文章页对外 404、`withArticlePlainText` 开关无效），但推文自带 `article` 对象，有 `title` + `preview_text`——脚本用这两项拼出可判的正文（实测 @GoogleCloudTech 的长文仅凭标题+预览就能定性）。
19. **RSS 多条条目共用同一链接时，键不能是位置序号**：`parse_feed` 原来键 = 链接，链接冲突就按出现顺序加 `#2 #3 …`，键实际退化成"第几条"。GCP 发布说明流 30 条全指向同一个 URL，每日轮换（新一天插入顶部、最旧一天挤出）时所有条目位置整体偏移——**内容没变却整批报 changed**，实测单轮 30 条 phantom changed + 10 条 phantom added，全是空判噪音。现已改成：链接被多条共用时取 Atom `<id>` / RSS `<guid>` 作键（无 id 则退回标题+日期组合），链接唯一的流行为完全不变。**给新源配 RSS 时，先看看它的条目是不是共用一个链接。**

## 可移植性

- 全部脚本只用 **Python 3 标准库**（零依赖），任意装了 Python 3 的机器都能跑。
- **不写死安装位置**：脚本用 `os.path.dirname(os.path.abspath(__file__))` 反推自己的目录，所以解压到任意路径都能跑；全目录**不含 `/Users/xxx` 之类的绝对路径**。文档里的 `<技能目录>` 一律指"本文件所在的那个目录"。
- **不依赖任何工具的私有目录**：技能不引用 `.workbuddy`、`.doubao` 之类路径。工作区默认 `~/.change-watch`，名字不带工具前缀，就是为了换工具时不用搬家。
- 状态、配置、日志都在**工作区目录**里，是普通文件，不绑定任何工具；换工具、换机器，带上这个目录即可。多个工具想共享同一份沉淀，把它们的 `--workspace` 指到同一目录。
- 若某个 AI 工具不支持技能格式：把本文件当指令集直接用，脚本照旧执行。

## 资源

- `config/targets.json.example` —— 脱敏配置模板（首次运行复制到工作区；`config/targets.json` 被 `.gitignore` 排除，防真实推送 ID 入库；`scripts/sync_template.py` 负责把 live 变更同步进模板）
- `references/criteria.md` —— **判断准则**（改判断口味改这里）
- `references/notify-card.md` —— **通知卡片模板与发送降级链**（清晰 / 精炼 / 可溯源的卡片结构）
- `scripts/watch.py` —— 抓取 + 差分，产出 `out/latest.json`
- `scripts/log_change.py` —— 记录判断结果、去重、生成通知草稿与**可直接发送的飞书卡片 JSON**
- `scripts/query.py` —— 时间线 / 按类型 / 按对象 / 检索 / 统计
- `scripts/sync_template.py` —— 把 live 配置同步为脱敏模板 `config/targets.json.example`（改完 live 跑一次；notify 块自动换占位符，写出前扫描真实 ID，命中即拒绝写出）
