---
name: market-intel
description: >-
  监控市场情报并发现潜在客户（需求源：市场情报内参闭环需求文档）。四大版块：① 潜在客户挖取——
  把融资/招聘/业务暴增/榜单新晋等事实聚合到企业档案，按"三维域限定（八行业+中国企业+业务出海）
  + P0/P1 信号组"确定性匹配，P0（近1月融资/近3月业务暴增/新品发布/榜单新晋任一）优先建联推送，
  P1 入池进日报；② 重点资讯情报——Gemini 竞对模型/价格、Google Cloud、八行业趋势；
  ③ 活动情报——B2B 出海活动白名单筛选；④ 每日内参——需求文档版式卡片（活动日历/行业重点新闻/
  榜单监控/潜在客户与业务情报，indigo 头图；榜单段有数据才出现），确认线索对接「猎隼线索池」多维表格（预设待权限校准）。适用于："最近八行业
  有什么动态"、"帮我盯种子到B轮刚融资的出海公司"、"这类线索定时推给我"。触发举例："市场情报简报"、
  "发现了哪些潜在客户"、"把某公司加入监控"。
version: 0.4.4
author: Webeye CSBU Team
license: MIT
alwaysActive: false
status: 开发中
complexity: 高
reusability: 高
category: 业务自动化
---

# market-intel（市场情报雷达）

把行业情报**持续抓到、判断过、沉淀下来**，并把潜在客户**按规则筛出来、带着证据推出去**。通知统一走**飞书**。

**分工（沿用 change-watch 的核心设计）**

| 谁 | 做什么 | 为什么 |
|---|---|---|
| **脚本**（确定性） | 采集、差分、企业档案聚合、规则求值、落盘、产卡 | 可复现、不花钱、可无人值守、可回测 |
| **模型（你）** | 读变更集 → 判断情报价值 / 抽取企业事实 → 写 judged.json → 发送卡片 | 这是判断，不是查表 |

**只有变化进判断，不是全量。** 每轮跑完先看 `out/latest.json`，里面只有变化。

## 心智模型：一条流水线，四层

```text
公共源登记册(sources.json) → 采集适配器(rss/mp_account/web_page)
  → 事件差分(latest.json) → AI 判断(judged.json：情报记录 + 企业事实)
  → 企业档案(facts/*.json) → 规则匹配(rules.json：scope + signal_groups)
  → 线索(log/leads.jsonl) → 飞书卡片(群简报 / 私信线索 / 高分群@)
```

与既有技能的关系：**借鉴 change-watch 的工程模式，不共享代码**——差分/待判队列/健康三态/卡片纪律
与其语义一致（运维心智不换），数据模型与采集器按市场情报需求全新设计；企业归一与信源分级沿用
AIHOT 的词表与 tier 概念。**字段与结构能继承的一律继承，不另造一套。**

## 工作区

默认 `~/.market-intel/`（`--workspace` 可换）。首次运行 `pipeline.py --init` 会把
`config/*.example` 复制进去，**此后改工作区那份**：

| 路径 | 内容 |
|---|---|
| `sources.json` | 公共源登记册（两个需求共用，`serves` 标记 intel/lead）。RSSHub 类源用 `rsshub://路由` 写法，部署地址统一在 `settings.rsshub_base` 一处改；已核实渠道清单、部署与排错见 `references/rsshub-sources.md` |
| `entities.json` | 企业与行业词表（entity 归一依据，沿用 AIHOT 词表思路） |
| `rules.json` | 线索规则（三层模型，改规则不改代码） |
| `notify.json` | 通知目标（飞书 user_id/chat_id/mention）+ 猎隼池写入预设（bitable，已实装） |
| `state/streams/*.json` | 上一轮快照（差分对照物） |
| `state/health.json` | 源健康三态（ok/flapping/down） |
| `state/pending.json` | 待判队列：本轮待判断的变更，判完核销 |
| `out/latest.json` | 本轮变更集，**先看这个** |
| `out/leads-latest.json` | 本轮新线索 |
| `out/card-*.json` | 可直接发送的飞书卡片（**不要手拼**） |
| `facts/<entity_id>.json` | 企业档案（融资事件 / 招聘信号 / 行业标签） |
| `log/intel.jsonl` | 已判断的情报流水 |
| `log/leads.jsonl` | 线索流水（字段与多维表格预设一一对应） |
| `log/discarded.jsonl` / `unjudged.jsonl` / `rolled_out.jsonl` / `runs.jsonl` | 丢弃留痕 / 超时未判 / 窗口滚动 / 每轮小结 |

## 工作流

### 第 0 步：初始化

```bash
python3 <技能目录>/scripts/pipeline.py --init
```

复制配置模板并列出待办（飞书 ID 怎么查、哪些源还没启用）。**推送目标由 AI 协助确认后写回**
（连接器 / `lark-cli im +chat-search --query 群名`），脚本不强制绑定通道。

### 第 1 步：建基线（不推送）

```bash
python3 <技能目录>/scripts/pipeline.py --baseline
```

快照当前全部条目。**这一轮不产出变更、不通知**——否则把历史当新消息推。

### 第 2 步：常规一轮

```bash
python3 <技能目录>/scripts/pipeline.py --run     # 退出码: 0 正常 / 1 配置错 / 2 有源失败 / 3 锁冲突
```

产出 `out/latest.json`。`summary.total == 0` 且无失败/停更 → 本轮干净，结束；
`total > 0` → 进入第 3 步。失败与停更已由脚本维护（`summary.sources_down` / `stale_sources`），不用你算。

### 第 3 步：AI 判断（你来做）

读 `out/latest.json` 的 `changes`，对**每一条**给出结论，写成一个 `judged.json`：

```json
{
  "intel": [
    {"change_id": "原样带回", "type": "融资事件", "significance": "normal",
     "title": "…（公司/产品名必须在原文出现过）", "summary": "一句话说清发生了什么、影响谁",
     "url": "原文链接", "sources": [{"name": "量子位", "url": "…"}],
     "notify": true, "notify_reason": "为何值得看",
     "number_notes": {"300": "300亿美元 = 原文 $30B 单位换算"}},
    {"change_id": "…", "type": "活动情报", "significance": "normal",
     "title": "快手技术沙龙", "summary": "…", "url": "…", "sources": [],
     "activity": {"name": "…", "time": "…", "location": "…", "highlight": "…",
                  "signup_url": "…"},
     "notify": true, "notify_reason": "B2B 出海活动"}
  ],
  "facts": [
    {"entity_name": "月之暗面", "aliases": ["Kimi"], "industries": ["应用"],
     "origin": "CN", "going_global": true,
     "profile": "一句话业务描述（可选）", "opportunity": "最大机会点（可选）",
     "event_type": "funding",
     "event": {"date": "2026-10-20", "round": "B轮", "amount_text": "3亿美元",
               "investors": "腾讯、阿里", "source_name": "36氪", "source_url": "…"},
     "change_ids": ["…"], "confidence": "high"},
    {"entity_name": "某上榜公司", "industries": ["应用"], "origin": "CN",
     "going_global": true, "event_type": "other",
     "event": {"kind": "rank_new_entry", "detail": "新晋 AI产品榜 出海总榜 Top20",
               "source_url": "…"},
     "change_ids": ["…"], "confidence": "high"}
  ]
}
```

判断准则见 `references/intel-criteria.md`（情报四问 + 竞对关注）、
`references/lead-criteria.md`（事实抽取、八行业口径、kinds 约定、防张冠李戴）与
`references/activities-criteria.md`（活动情报白名单/黑名单）。三条铁律沿用 change-watch：

- **漏答一条 = 判断不完整**：每个 `change_id` 必须被 intel（含 discard / merged）或
  facts.change_ids 覆盖，`judge.py` 会核对，缺一条点名退出码 2；
- **判不出来写 `significance: "unknown"`**：落盘但不通知（脚本强制）；
- **不真的写 `discard: true` + 一句理由**：留痕，不悄悄消失。

**数字 grounding 三层判定**（防止说明里出现原文没有的数字）：① 直接出现在原文 → 通过；
② 常见换算（`$30B`→300亿美元、30,000 人→3 万人、×7.2 汇率等）→ 脚本自动兜底通过并留档；
③ 兜底认不出的换算 → AI 在 `number_notes` 里申报依据后通过（备注留档供人工审计）；
三层都不满足 → 标 `unverified` 并告警。**申报必须属实**——`number_notes` 是留痕通道，
不是洗白虚构数字的通道。

### 第 4 步：落盘、匹配、产卡

🔴 CHECKPOINT（落盘前必做）：首次判定或复用判定组装后，先加 `--dry-run` 在沙箱
临时副本上预检（真实工作区零写入），报错当场修正重跑，**预检通过才执行本步**。

```bash
python3 <技能目录>/scripts/judge.py --input judged.json
```

脚本依次：覆盖度校验 → 情报流水落盘（grounding 数字核对）→ 企业事实并入档案
（web_page 招聘关键词确定性直录）→ 规则匹配出线索（scope 三维硬筛 + 组间 OR；
任一 tier=P0 组命中 → P0 线索实时通知，仅 P1 组命中 → 入池进日报不实时打扰）→ 产出卡片：

| 卡片 | 内容与作用 | 何时出现 | 发给谁 |
|---|---|---|---|
| `card-intel-<时间>.json` | **情报卡（按需点播）**：本轮 notify 情报逐条列出——默认不发，情报随内参大卡带出；用户点名"逐条推"时用 | 用户明确要求 | 本人私信/群 |
| `card-intel-<时间>-group.json` | 情报卡群@版（红头 + 首行 @）——同上按需 | 用户明确要求且含 high | 群 @ |
| `card-leads-<时间>.json` | **线索卡**：本轮新产 P0/P1 线索，P0 红标「优先建联」，含画像/机会点/「查看线索」直达猎隼池记录——市场建联行动清单。**已在线索池的公司（exists）中性标注「已存在」且不用红块**——不是新线索，是重复提示 | `--bitable --lead-card` 入池后重产（此版为准，链接可直达） | 本人私信 |
| `card-leads-<时间>-group.json` | 线索卡群版（首行 @）——同步 P0 客户信号。**只含全新 P0 线索**：exists（此前已入池）的绝不进群 @ 催建联 | 仅当有全新 P0 线索（`--lead-card` 产出，commit 后状态已知） | 群 @ |
| `card-daily-<时间>.json` | **每日内参**：需求文档版式四段卡（活动日历/行业重点新闻/榜单监控/潜在客户与业务情报），窗口内增量，全空不产空卡 | `judge.py --digest` | 群 |
| `card-digest-<时间>.json` | 内参渲染失败时的 simple 降级版——兜底保送达 | 仅渲染失败时 | 群 |
| `card-status-<时间>.json` | **状态卡**：源失败/异常/无变更需知会——系统健康告知 | 管线检测到异常（`--status-card`） | 本人私信 |

**发送纪律（行为边界）**：

- **默认通知 = 一张每日内参大卡**（「市场团队内参：近期热点与活动简报」，四段版式，
  热点/活动/榜单/线索全部内嵌）。巡检与人工触发（"跑一下"、"看看有什么新东西"）统一
  只发这一张，发群（可选同步私信），**不默认叠加情报卡/线索卡/群@多张刷屏**。
- **情报卡（card-intel）与群@版是按需点播卡**：仅当用户明确要求"逐条实时推"、"单独推
  这条到群"时才发；平时情报内容随内参大卡的「行业重点新闻」段带出。
- **线索闭环不受本纪律约束，正常推进**：新线索 → AI 调研补全（14 必研字段硬门槛）→
  `--bitable --commit` **自动写入猎隼池（无需人工确认）** → `--lead-card` 发线索卡
  （私信，P0 加群@，带「查看线索」直达按钮）。该卡是闭环交付物，不属于刷屏；
  P0 线索在大卡的「潜在客户与业务情报」段红标带出。人工改为**事后在表内核查修正**，
  不把写入卡在确认步骤。
- **测试卡只发临时测试群**且头部标注「链路测试」，验证渲染后即弃，绝不进正式群。

**群卡 @ 规则**：`*-group.json` 首元素自动带 @ 提醒行，由 notify.json `notify.mention`
驱动（`"all"` = `<at id=all></at>` @所有人，需群主开"任何人可@所有人"权限；
`["ou_xxx"]` = 只 @ 指定人；空 = 不 @）。群消息**一律 `--as user`**（沿用 change-watch
实测纪律：bot 发群卡 @ 不生效且易被折叠）；当前 chat_id=临时测试群，正式启用前切换
notify.json。

**卡片直接发送脚本产出的 JSON，不要手拼**；发送降级链见 `references/notify-card.md`
（飞书连接器 bot 身份 → 本地 lark-cli → 落盘 `notify-unsent-*.md` 并会话内明确提醒）。

### 第 5 步：线索入池闭环（自动写入，人工事后核查）

**默认自动入池（2026-10-26 起，用户拍板）**：调研命中即写，**不卡「人工确认预览」断点**——
新线索 → AI 调研补全（14 必研字段硬门槛）→ `judge.py --bitable --commit` 一条命令
自动写入（先重建预览吸收最新调研再写）→ `--lead-card` 发线索卡（带「查看线索」
直达链接）。给出提示和链接即可，人工**事后**在表格里核查修正。

安全性依据：写入链路自带五重护栏——调研 14 字段硬门槛（不齐全 blocked 拒写）、
公司名去重（主名 + 别名候选精确检索，exists 不重写、不重复建行）、**exists 通知隔离**
（已在线索池的线索私信卡标注「已存在」、绝不进群 @ 催建联——已存在的公司不是新潜在线索）、
select 选项校验（无效值丢弃留痕）、幂等可重跑（写入 failed 的线索保留待写状态自动重试，
不会一次网络抖动就静默丢单）。

`status`（new/confirmed/rejected）保留为**事后核查字段**：人工在表内/leads.jsonl
复核后改 `confirmed` / `rejected`，`rejected` 的记录是规则调优的依据。
它与 `bitable_status`（pending/pushed/exists/filtered/failed/blocked，脚本回写
写入结果用）是两个字段：写入由 `--bitable --commit` 驱动、选 `bitable_status=pending`
的线索，**不要求先改 `status`**。

### 第 6 步：检索（随时）

```bash
python3 <技能目录>/scripts/query.py --leads                    # 最近线索
python3 <技能目录>/scripts/query.py --entity 月之暗面            # 企业档案 + 相关线索
python3 <技能目录>/scripts/query.py --intel --since 2026-10-01  # 情报流水
python3 <技能目录>/scripts/query.py --stats                    # 计数统计
python3 <技能目录>/scripts/query.py --rolled-out               # 窗口滚动留痕
```

## 定时与自动化（宿主建两条任务）

**巡检通道（间隔触发，建议每小时）**：

```text
你正在按计划执行 market-intel 巡检。依次：
1. 运行 `python3 <技能目录>/scripts/pipeline.py --run`。
   退出码 1=配置错（报告，不静默）；2=有源失败（继续）；3=锁冲突（跳过本轮，不重试）。
2. 读 <工作区>/out/latest.json 的 summary：
   - total==0 且无失败/停更 → 结束（不打扰）；
   - total==0 但有异常 → `python3 <技能目录>/scripts/judge.py --status-card` 后发本人私信；
   - total>0 → 下一步。
3. 按 references/intel-criteria.md 与 lead-criteria.md 对 changes 逐条判断，写 judged.json
   （每个 change_id 必须有结论）。
4. `python3 <技能目录>/scripts/judge.py --input judged.json`（漏判会报错点名，必须补全）。
5. 发送**每日内参大卡一张**：`judge.py --digest` 产 card-daily → 发群（可选同步私信）→
   发送成功后 `judge.py --digest --commit` 推进锚点。**不默认发情报卡/线索卡/群@**
   （按需点播才发，见第 4 步「发送纪律」）。
6. 本轮有新线索 → 继续闭环：AI 调研补全（references/bitable-fields.md，
   14 必研字段齐全，可用 `--research-check` 审计）
   → `--bitable --commit` **自动入池（无需人工确认）** → `--lead-card` 发线索卡
   （私信，仅全新 P0 加群@——已存在（exists）的只私信标注，不进群；带直达链接）。
   人工事后在表内核查。
工作区默认 ~/.market-intel/。
```

**每日摘要（固定时刻，如 08:30）**：跑 `judge.py --digest` 产卡发群；**发送成功后**再跑
`judge.py --digest --commit` 推进锚点（宁重勿漏：失败不 commit，下次自动重覆盖）。

**线索入池「猎隼线索池」多维表格**（notify.json `bitable` 块驱动，已按真实字段校准）：

1. `judge.py --bitable` —— 把待写线索构建为 `out/bitable-pending.json` 预览（离线）。
   构建时会用**当前规则重算匹配**：旧规则遗留、现已不满足"中国企业+出海"硬筛的线索
   自动标记 filtered 排除；记录只填机器可靠字段（公司名称/业务描述/赛道/标签细分/
   融资轮次/资方/曝光媒体/GCP切入点/跟进状态/抓取时间/官网兜底链接）。
2. **AI 调研补全（入池硬前置，代码强制）**：按 `references/bitable-fields.md` 字段标准，
   用已有数据源（facts 档案/情报流水）+ 联网调研补齐 **14 个必研字段**（业务/融资/资方/
   经营规模/技术底座/CEO/规模/年限/城市/联系方式/官网/GCP切入点/破冰话术等），
   写 `log/enrichments.jsonl`（契约见该文档）；查不到填规定兜底值（未查明/未公开/
   未查询到有效信息），**不猜**——查不到≠可以不写，兜底值本身就是调研结论。
   审计：`judge.py --research-check`（未达标退出码 2，逐企业点名缺口）。
   **调研不齐全的线索 `--commit` 会被拒绝写入（exit 4，result=blocked）**——残缺画像
   会污染线索池、让破冰话术失真、让后续潜在线索判断失去依据。补齐后直接跑
   `judge.py --bitable --commit`（会重建预览吸收补全字段）。select 值写入前仍过选项校验。
3. `judge.py --bitable --commit` —— **自动入池（默认，无需人工确认预览）**：一条命令
   先重建预览吸收最新调研、再按「公司名称 + 别名候选」精确检索去重（任一命中即
   exists 跳过，防同公司异写重复建行），按最新 field-list 校验选项值后写入，
   结果回写 leads.jsonl 与 `log/bitable.jsonl`。重复执行幂等（exists 不重写）。
   输出带表格视图链接。人工**事后**在表内核查修正。
   （`--bitable` 单独跑仍可用于只看预览不写入。）
4. `judge.py --lead-card` —— 写入后重产最近一轮线索卡（私信 + 群卡两版，**发送这版**）：
   每条线索带「查看线索」按钮 record 级直达表格记录；exists（此前已入池）的线索
   标注「已存在」且**不进群卡**——群卡只催全新 P0 建联。`--input` 阶段产出的线索卡/
   群卡是写入前快照，一律以本命令产出的最新时间戳版本为准。

## 洁净验证标准 SOP（验证"新环境"效果时按此执行，勿即兴发挥）

每轮洁净验证都是新场景，历史上每轮都冒新问题的根因是**步骤即兴 + 问题只在落盘时暴露**。
标准顺序（2026-10-27 定稿，配套 `--dry-run` 预检）：

1. **备份**：`cp -r ~/.market-intel ~/.market-intel.bak-clean-<日期-时刻>`；
   删除更旧的备份（只留最新一份，它是复判源兼安全网）与工作区 `*.bak*` 残留。
2. **清基线**：删 `state/streams/*`、`state/digest_anchor.json`、`out/*`、
   `log/intel.jsonl`、`log/discarded.jsonl`；**保留** rules/sources/notify/entities、
   `facts/`、`log/leads.jsonl`、`log/enrichments.jsonl`（防重复入池）。
3. **全量首扫**：`pipeline.py --run`（14 源实测 224-244 条）。
4. **构建判定（零幻觉复判）**：按 change_id 从备份 `log/intel.jsonl`/`log/discarded.jsonl`
   原样复用历史判定（含 merged_change_ids 的主条记录要整个拷回——被合并条目换源
   重现时靠它防重发），**只逐条判定真新增**；url/名称一律从 latest.json 带回。
5. **沙箱预检（必做）**：`judge.py --input judged.json --already-sent <备份>/log/intel.jsonl
   --dry-run` —— 临时副本上完整演练（白名单/覆盖度/grounding/sent-guard/产卡），
   真实工作区零写入；**预检通过才进行下一步**，失败按报错修正后重跑预检。
   🔴 CHECKPOINT：预检未通过禁止进入第 6 步。
6. **正式落盘**：同命令去掉 `--dry-run`；核对输出与预检一致。
7. **发通知**：有 high 情报先发情报群卡；`--digest` 产内参发群 + 私信；
   **发送成功后** `--digest --commit` 推进锚点（失败不 commit）。
   **重置后首跑 digest 是 anchor=None 的全量窗，发送前必须核对卡内不含
   已通报旧闻**（应全被 suppressed_by_guard 过滤；leads 段允许带"此前已
   登记"标注的历史线索）。
   🛑 STOP：发送前人工核对卡片内容是第三层断点（dry-run 预检 → 脚本护栏 →
   人工核对），核对不通过则不发卡、不推锚。
8. **收尾**：删临时 judged.json；教训若为新坑，代码化或入陷阱清单后再结束
   （"预检拦住的问题"也要评估是否值得代码化——复用场景的坑一律代码化）。

## 已知边界与陷阱（一期实测）

1. **中文媒体原生 RSS 大多已死**：36氪/机器之心等传统 RSS 地址实测返回 HTML（2026-10-23）。
   已验证可用：量子位、雷峰网、极客公园、TechCrunch。其余走自部署 RSSHub（公共实例
   rsshub.app 被 Cloudflare 拦截，不可依赖）。
2. **公众号（mp_account）必须低频**：依赖搜狗通道，时灵时不灵；建议 ≥120 分钟轮询、
   `windowed: true`；解析到 0 条脚本会显式报错（按源失败处理），不会静默空结果。
   不要把它当唯一信源。
3. **RSS 键规则**：链接唯一用链接；多条共用链接用 Atom id/guid（change-watch 陷阱19 的移植）。
   给新源配 RSS 前先看条目是否共用链接。
4. **web_page 监测有两种模式**（`config.mode`）：`keywords`（默认，招聘页：关键词出现/消失，
   粒度是关键词不是岗位条目，改关键词后需重建该源基线）；`links`（资讯列表页/榜单页：
   提取文章链接差分，新出现链接=added，天然实现"排除霸榜只看新晋"的榜单语义）。
   换模式或改 `link_pattern` 后需重建该源基线（删 `state/streams/<source_id>.json` 重跑 --baseline）。
5. **融资金额是近似值**：`parse_amount_cny` 只用于门槛比较（美元按近似汇率 7.2 折算，
   模糊表述"数千万"按 1000 万下限）。卡片与档案保留原文 `amount_text`，不要拿近似值对外引用。
   英文写法（`$30B` / `$200M` / `$1.5billion`）已支持（实测 TechCrunch 源首次运行曾因
   只认中文单位导致大额融资线索漏报）；无美元符号的 bare "35million" 不折算汇率。
6. **judge.py 幂等**：按 change_id 去重，同一 judged.json 修复后重跑不会重复落盘 intel/
   discarded（实测发现修复前重跑会双写，已修复并加回归测试）。但**卡片会重新生成**——
   重跑后以最新时间戳的卡为准发送，旧卡作废。
7. **规则匹配的是企业档案，不是原始事件**：同一公司在多渠道的事件会聚合到同一档案；
   这正是 `within_days` 类条件能成立的原因。公司识别靠词表 aliases + 域名，认不出进
   `unknown:*` 档案待归并——定期归并 unknown 档案并充实词表。
8. **unknown 档案的悬置**：`unknown:*` 企业不参与通知（匹配可以命中但 AI 应在判断阶段
   尽量给出可归一的公司名）；词表里补上别名后档案自动归并。
9. **「JS 渲染抓不到」先复核再下结论**：AI产品榜（aicpb.com）2026-10-23 曾判"前端渲染
   抓不到"而禁用，10-26 复核发现实为 Nuxt SSR、links 模式可正常解析各分榜头名（21 条）——
   误判原因未用带 UA 的完整请求验证。AppMagic 则实测确认纯 SPA（仅 cookie 横幅），
   登记停用留档；列表页类源（智东西/品牌方舟/投资界/活动行）links 模式实测可用。
10. **活动行时间/地点靠详情页富化**：活动行 RSS feed 只有标题+链接（description 为空）。
   源配 `config.detail_enrich: true` 后，管线对新增条目自动抓活动详情页解析内嵌
   `ativityJson`（站点源码就少拼一个 c），把时间/地点/主办方/报名规模写进变更 summary；
   解析失败静默保留原条目（AI 才可写「见报名页」并注明）。单轮抓取上限
   `settings.detail_enrich_max_per_round`（默认 12）。
11. **调研 entity_id 必须与线索 entity_id 完全一致**：调研补全写 `log/enrichments.jsonl` 时
   `entity_id` 要用 facts/leads 里的原值（如 `unknown:文远知行`、`moonshot`），自造 ID 会导致
   调研静默匹配不上、入池记录字段全空。管线已加双保险：entity_id 匹配不上时按 entity_name
   兜底（stderr 提示修正），完全未被消费的调研会告警点名。调研前先
   `grep entity_id log/leads.jsonl` 确认原值。
12. **P0/P1 分级口径**（需求文档）：P0 = 近1月融资 / 近3月业务暴增 / 发布新产品 /
    榜单新晋，任一命中即 P0（市场优先建联，实时通知）；仅满足画像（半年内融资、
    出海相关招聘）为 P1（入池+日报）。`notify_p1: false` 时 P1 不实时推送。
11. **grounding 对"B2B"类字母数字缩写会误报**：说明文字里的 B2B、T1 等缩写中的数字
    会被当成数字核对（实测 2026-10-23），只告警不阻断；在 `number_notes` 里申报
    （如 `{"2": "B2B 为缩写"}`）即可消除，二期可加缩写白名单。
12. **卡片 markdown 行内粗体：闭合 `**` 后必须跟空格**：`**时间：**值` 这种
    "全角标点收尾+紧贴正文"的写法不满足粗体判定，星号原样显示（实测 2026-10-23）；
    写成 `**时间：** 值` 即可。独占一行的 `**标题**` 不受影响。
13. **猎隼线索池字段要抄表内惯例**：技术底座查不到填"未查明"、联系方式填
    "未查询到有效信息"、企业规模/成立年限/城市填"未公开"——不空着、不猜、不造选项；
    各字段定义与调研渠道见 `references/bitable-fields.md`（按 100 条历史记录反推校准）。
14. **调研完整度是入池硬门槛（2026-10-26 起）**：`fields` 必须 14 必研字段齐全
    （缺兜底值也算缺），`--commit` 对未齐全线索拒绝写入（exit 4，bitable.jsonl
    result=blocked）；`--research-check` 逐企业审计。存量教训：早期契约只要求 8 个
    字段、且"可省略"，导致 11 家已入池企业缺融资动态/经营规模/官网等——残缺画像
    让破冰话术失真、后续判断失据（Homeika GMV 还写错口径），已全部真实联网回补。
    经营数据必须注来源口径，多口径冲突取可溯源者并在 notes 记录纠错。
15. **情报卡族视觉差异纪律**：market-intel 情报卡统一**紫色系**（violet 头 +
    `report_outlined` 图标 + violet-50/100 描边块），与 change-watch 蓝色铃铛监控卡
    一眼区分（实测 2026-10-23 临时测试群）。语义色共用不改：红=强提醒/P0、
    绿=正常/P1、黄=异常。改样式必须先发临时测试群实测渲染，再发正式群。
16. **lark-cli base batch 响应结构是 `data.record_id_list`**（2026-10-26 实测）：
    `+record-batch-create/update` 成功响应不含 `records[].record_id`，直接解析会
    永远拿不到、每次新建都白白多跑一次检索（曾误判为"响应未含 record_id"）。
    用 `bitable._extract_new_rid()` 解析（兼容两种结构）。同理注意：`+record-delete`
    的 --json 要 `record_id_list` 键；`+record-search` 响应数据在 `data.data` 矩阵 +
    `record_id_list`（没有 `items` 键，人工验证时别读错）。
17. **exists 线索的通知语义必须分层（2026-10-26）**：已在线索池的公司（写入层
    `bitable_status=exists` 回写）不是新潜在线索——私信卡中性标注「已存在·已在线索池」
    （violet 块），**绝不进群 @ 催建联**（群卡只收全新 P0，`lead_card_split` 分流）。
    去重检索用主名 + 档案别名候选（`_dedupe_candidates`，上限 4 次），防同公司异写
    （如「月之暗面」vs「月之暗面科技有限公司」）重复建行；写入 failed 的线索保留
    待写状态下轮自动重试（终态仅 pushed/exists/filtered），一次网络抖动=静默丢单
    是踩过的坑。线索卡/群卡一律以 `--lead-card`（commit 后）产出的最新版为准发送。
18. **judged.json 顶层 `discarded` 键会被自动并入（2026-10-26 起，代码层兜底）**：
    构建判断文件时把弃权条目单独放 `{"discarded": [...]}` 而忘并入 `intel` 列表，
    会让覆盖度校验报缺失、退出码 2（实战踩坑两次）。现已由 `absorb_discards()`
    自动吸收并在 stderr 提示——构建脚本可直接只填 `intel`，不必手工合并。
19. **洁净重置流程（验证"新环境"效果时）**：先整目录备份（`cp -r` 工作区到
    `*.bak-clean-<日期>`）→ 再清 `state/streams/`、`state/digest_anchor.json`、
    `out/` 旧卡，保留 `rules/sources/notify` 配置与 `leads/enrichments/bitable`
    业务记忆（防重复入池）。**`log/intel.jsonl` 是「已发登记」**：清空它会让全部
    历史情报重新通知轰炸——重置后重判时用 `judge.py --input judged.json
    --already-sent <备份>/log/intel.jsonl`，命中的已发情报由代码强制
    `notify=false`（`demote_already_sent`，无需人工逐条标注）。差分基线清零后
    首轮即为全量首扫（14 源实测 244 条）。**复用旧登记记录进 judged 时**（按
    change_id 映射原样拷回历史判定，是零幻觉复判的推荐做法），记录会带落盘
    回写字段（logged_at/grounding/grounding_notes）——`strip_writeback()` 已
    代码层自动剥离（stderr 提示）；弃权登记是 `{change_id, reason}` 结构，与
    judged 契约不同，`strip_writeback()` 同步归一为 `{discard, discard_reason}`；
    其余未知字段仍被白名单点名拦截，属拼写错误防护，勿绕过。
    **组装常见笔误（2026-10-27 实测）**：构建了 `discarded` 列表却忘放进
    输出 dict 顶层——覆盖度校验会点名全部弃权 change_id，按报错把键补回
    `{"intel":…, "discarded":…, "facts":…}` 即可；dry-run 先行时此错误
    零污染拦截。另注意 macOS 自带 grep 是 BSD 版，`\|` 交替不生效，
    多模式排查用 `grep -E "a|b"`，勿凭 0 命中下「记录缺失」结论。
    **复判流程整体见「洁净验证标准 SOP」，配合 `--dry-run` 预检执行。**
20. **调研与修正必须用表内选项精确值（2026-10-26 实测）**：写 `所在城市/区域`、
    `成立年限`、`企业规模` 前**先拉 `+field-list` 核对选项**——「上海（徐汇区）」
    会被正确丢弃（表内是「上海」）、「2012年」表内实无（看串行会误判为缓存偏差，
    用同字段其他选项隔离测试可甄别）。选项被丢弃后用 `+record-batch-update` 修正：
    `--json '{"update_records":{rid:{"字段":[选项]}}}'`（select 值要数组；
    lark-cli 无 `+record-update`）。**多字段合并 update 可能报 record not_found
    （平台侧问题，实测复现），拆成单字段逐个写即成功**。
21. **情报摘要数字中性化（防 grounding 护栏误报重跑）**：summary 尽量不写精确
    数字（原文没有的换算值、报告名年份、品牌名「36氪」都会触发告警）；精确数字、
    换算口径、调研补充一律进 `number_notes` 申报。被护栏拦下时先定位对应条目，
    数字属实则补 number_notes，切勿删护栏。
22. **change_id 是内容寻址（2026-10-26 修复，`pipeline.make_cid`）**：源+种类+
    条目键哈希，**不掺运行时刻**。此前 id 含 `now`，同一内容每轮/每次洁净重置都
    拿新 id——已判记录无法复用、`--already-sent` 防重发失效、出窗回灌旧文被当
    新消息重复轰炸（量子位/36氪回灌轮只能靠 AI 逐条识别防重，实战负担重）。
    修复后同键同 kind 跨轮同 id：已判内容被 judge 幂等跳过，防重发天然成立。
    旧工作区升级本版后首轮全量 id 全新属预期（旧 id 无法映射），判一次即稳定。
23. **judged 里的 url/名称必须原文带回，禁止凭背景知识补全（2026-10-26 实测）**：
    凭记忆写活动行 event id 会造出**根本不存在的链接**（4 条全部写错，靠变更集
    `url` 字段逐条核对才发现）；activity.name 里凭知识补「CeMAT ASIA」原文没有、
    属未证实信息。正确做法：`change_id → latest.json changes` 逐条带回 `url`，
    名称只用原文出现过的子串。
24. **卡片文案前缀由模板加，judged 不自带**：`activity.highlight` 写"看点：…"
    会导致内参卡出现「看点：看点：」重复前缀（模板 `_activity_elements` 已加
    前缀）。同理数字报名规模不要写进 summary（grounding 校验会拦），参考性
    描述用「报名过半/规模大」等中性表述。
25. **活动日历截断前按 significance 排序（2026-10-26 代码化）**：`_activity_elements`
    此前按列表原序截取 limit=3，high 活动排后会漏。已修复为 high 优先再截断
    （附回归测试）。AI 判定时仍应把最值得去的活动放 intel 列表前部。
26. **merged_change_ids 具有已发登记语义（2026-10-27 代码化）**：合并报道只落
    主条记录，被合并条目没有自己的登记行——主条滚出窗口后，被合并版本换源
    首次差分入集（如 Anthropic IPO 的 36氪/钛媒体版）会被当全新内容重复通知。
    `--already-sent` 构造已发集合时已把旧登记的 merged_change_ids 一并计入
    （附回归测试）；复用历史判定时**主条记录必须整体拷回**，否则该防线失效。
27. **问题要拦在落盘前（2026-10-27 引入 `--dry-run`）**：洁净验证每轮都是新
    场景，白名单/覆盖度/grounding/复用结构等问题此前的暴露时机是正式落盘。
    现已提供沙箱预检：临时副本上完整演练、真实工作区零写入。洁净验证一律
    先 dry-run 通过再正式执行（见「洁净验证标准 SOP」）；给 judged 加新结构
    或新复用来源时，先补 dry-run 用例再上线。
28. **suppressed_by_guard 是"已通报"契约字段，digest 据此防旧闻重推
    （2026-10-27 代码化）**：洁净重置全量窗复判后，旧登记情报会被 sent-guard
    压实时通知，但 `--digest` 原先按窗口全量打包——旧闻重新进内参推群。
    修复：sent-guard 命中即打标（无论 notify 当前值；上一轮已压成 false 的
    记录同样要标），标记入 INTEL_KEYS 随落盘与复用继承，digest 过滤该标记；
    AI 判定 notify=false 的记录不带标记，保留内参归档展示语义。
29. **digest 全空不产空卡（2026-10-27 代码化）**：briefing 版式三段全空返回
    None 后，simple 降级分支原先仍产出"0 条动态"占位卡。已修：records 与
    leads 全空时直接不产卡。洁净重置后首跑 digest 属全量窗，发送前必须核对
    窗口内容（ SOP 第 7 步），anchor=None 的全量窗尤其要防旧闻重推。
30. **RSS 条目键批内漂移（2026-10-27 代码化，v0.3.9）**：键分配此前依赖「批内
    链接唯一性」——同一活动在列表页多板块重复展示时，跨批拿到 `guid:URL` /
    `URL#2` 等不同键，内容寻址 change_id 随之漂移，已通报活动每轮以新变更身份
    重复入窗（实测一轮 30 条变更里大半是此类假新增）。已修为内容正交键：归一
    链接 > guid > 标题哈希，同键重复条目由差分层 dict 化合并（`collect.entry_key_of`）。
    **升级存量工作区必须执行一次 `pipeline.py --rekey-baseline`**（把快照旧键
    统一重算，幂等可重跑），否则修复后首轮差分把全部现有条目当 removed+added
    重报。已发登记中 title/type 为空的"骨架记录"是 v0.3.2 前精简登记的墓碑
    （notify=false + suppressed，对通知链零影响，跨轮防重发仍有效），勿删勿修。

31. **覆盖度校验计入历史登记（2026-10-27 代码化，v0.4.0）**：覆盖度防的是
    「窗口内有条目从未被任何一轮判过」，不要求单次输入重答全量——`log/
    intel.jsonl` + `log/discarded.jsonl` 里已有的 change_id 视为已判。否则
    facts 补录、增量判定等合法输入被误拦退出码 2（全新隔离环境实测踩坑），
    且已判条目会滞留 pending 被下轮以 reopened 身份误回放（核销集合同步计入
    历史登记，两处一起修）。dry-run 与正式路径共用此逻辑，预检覆盖。

32. **判定 ≠ 已发：`--sent-before` 防漏发（2026-10-27 代码化，v0.4.1）**：
    上轮判定落盘 notify=true 但未发内参/未推进锚点即洁净重置时，这些内容
    从未真正发出。复用旧登记必须传 `--sent-before <旧锚点>`——锚点之后
    落盘的 notify=true 记录不算已发（强制保留通知，宁重勿漏）；锚点前的
    已随内参发出，照常压制；notify=false 归档无条件算已发。
33. **弃权复用的形态陷阱（2026-10-27 代码化，v0.4.1）**：`discarded.jsonl`
    的记录是分流后形态 `{change_id, reason, logged_at}`（**无 discard 键**）。
    组装脚本若按 `r.get("discard")` 分流会把弃权误剥成裸 `{change_id}` 骨架
    落进 intel.jsonl——内参产空条目、弃权理由永久丢失。三层防护：组装侧
    兼容两种形态（保留 reason 供归一）→ 落盘前护栏拦截**窗口内**裸骨架
    （窗口外 v0.3.2 前精简留痕合法放行）→ `_news_elements` 跳过无 title
    记录不占编号。另：活动日历 limit=3→8，截断的 notify=true 活动在锚点
    推进后会永久漏发（单行短文本不爆卡）。
34. **`--digest --commit` 连用静默不产卡（2026-10-27 代码化）**：旧实现
    commit 分支在产卡之前提前 return——连用时锚点被推进、摘要卡从未产出
    且无任何提示，窗口内容就此丢失（本轮洁净验证实测踩中，靠发送前核对
    断点发现）。已修：执行顺序改为「先产卡、后推锚」，连用语义 = 产卡 +
    推锚一步完成；全空窗口连用时推锚但明确提示不产空卡（附回归测试）。
    **恢复误推的锚点**：未产卡未发送时，把 `state/digest_anchor.json` 的
    anchor 置回 null 再跑 `--digest` 即可（合法：digest 从未随卡发出）。
35. **grounding 数字核对只查说明、不查 title（2026-10-27 代码化）**：旧实现
    probe 含 rec.title，两类复用场景误报——① 页面监测源 title 尾部带动态
    计数器（阅读/热度数，实测 449→463 逐轮漂移），拷回的历史 title 必触发
    「说明含原文没有的数字」误告警；② 变更滚出本轮窗口（change=None）时
    证据集为空，说明里的合法数字（如年份 2026）全部误报。已修：probe 只查
    `summary`（grounding 本意即防 AI 说明编造数字；title 是「原样带回」
    契约，陷阱 23）；change 为 None 时跳过核对（附回归测试 ×2）。
    **键稳定性附注**：title 动态尾巴不影响 change_id——页面监测源键=URL
    （内容正交键第一优先级），实证 4563ac 两轮 title 漂移仍同键、零假新增。

## 可移植性

- 全部脚本只用 **Python 3 标准库**（零依赖）；
- 不写死安装位置（`os.path.dirname(os.path.abspath(__file__))` 反推），全目录无 `/Users/xxx` 绝对路径；
- 状态、配置、日志都在工作区，是普通文件；多工具共享就让 `--workspace` 指向同一目录。

## 资源

- `README.md` —— **新环境/新会话上手入口**：现状快照、启动清单、文档地图（先读这个再读本文件）
- `config/sources.json.example` —— 公共源登记册模板（字段说明见 `references/sources-guide.md`）
- `references/rsshub-sources.md` —— 本地 RSSHub 部署/地址管理/已核实渠道清单与排错（动 RSSHub 相关配置前必读）
- `config/entities.json.example` —— 企业与行业词表模板
- `config/rules.json.example` —— 线索规则模板（三层模型见 `references/rules-guide.md`）
- `config/notify.json.example` —— 通知目标与多维表格预设
- `references/intel-criteria.md` —— 行业情报判断准则（改口味改这里）
- `references/lead-criteria.md` —— 企业事实抽取准则（八行业口径 / kinds 约定 / 防张冠李戴）
- `references/activities-criteria.md` —— 活动情报判断准则（B2B 白名单 / C 端黑名单）
- `references/bitable-fields.md` —— 猎隼线索池 20 字段标准（定义/惯例/兜底值/AI 调研契约）
- `references/notify-card.md` —— 卡片模板与发送降级链
- `scripts/pipeline.py` —— 初始化 / 基线 / 采集差分
- `scripts/judge.py` —— 判断落盘 / 规则匹配 / 产卡 / 摘要
- `scripts/query.py` —— 检索与统计
- `tests/` —— 离线验证（解析 / 金额 / 规则 / 差分矩阵）
