---
name: market-intel
description: >-
  监控市场情报并发现潜在客户（需求源：市场情报内参闭环需求文档）。四大版块：① 潜在客户挖取——
  把融资/招聘/业务暴增/榜单新晋等事实聚合到企业档案，按"三维域限定（八行业+中国企业+业务出海）
  + P0/P1 信号组"确定性匹配，P0（近1月融资/近3月业务暴增/新品发布/榜单新晋任一）优先建联推送，
  P1 入池进日报；② 重点资讯情报——Gemini 竞对模型/价格、Google Cloud、八行业趋势；
  ③ 活动情报——B2B 出海活动白名单筛选；④ 每日内参——需求文档三段式卡片（活动日历/行业重点新闻/
  潜在客户与业务情报），确认线索对接「猎隼线索池」多维表格（预设待权限校准）。适用于："最近八行业
  有什么动态"、"帮我盯种子到B轮刚融资的出海公司"、"这类线索定时推给我"。触发举例："市场情报简报"、
  "发现了哪些潜在客户"、"把某公司加入监控"。
version: 0.2.4
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
| `sources.json` | 公共源登记册（两个需求共用，`serves` 标记 intel/lead） |
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

```bash
python3 <技能目录>/scripts/judge.py --input judged.json
```

脚本依次：覆盖度校验 → 情报流水落盘（grounding 数字核对）→ 企业事实并入档案
（web_page 招聘关键词确定性直录）→ 规则匹配出线索（scope 三维硬筛 + 组间 OR；
任一 tier=P0 组命中 → P0 线索实时通知，仅 P1 组命中 → 入池进日报不实时打扰）→ 产出卡片：

| 卡片 | 何时出现 | 发给谁 |
|---|---|---|
| `card-daily-<时间>.json` | 每日摘要（`--digest`），需求文档三段式内参卡 | 群 |
| `card-digest-<时间>.json` | 内参卡渲染失败时的 simple 降级版 | 群 |
| `card-intel-<时间>.json` | 本轮有 notify 的情报 | 群（简报） |
| `card-intel-<时间>-group.json` | 含 high 情报 | 群 @（红头，首行 @ 提醒） |
| `card-leads-<时间>.json` | 本轮有 P0 达标新线索 | 本人私信（P0 红标优先建联） |
| `card-leads-<时间>-group.json` | 有 P0 线索 | 群 @（首行 @ 提醒） |
| `card-status-<时间>.json` | 无变更但需知会 / 有异常 | 本人私信 |

**群卡 @ 规则**：`*-group.json` 首元素自动带 @ 提醒行，由 notify.json `notify.mention`
驱动（`"all"` = `<at id=all></at>` @所有人，需群主开"任何人可@所有人"权限；
`["ou_xxx"]` = 只 @ 指定人；空 = 不 @）。群消息**一律 `--as user`**（沿用 change-watch
实测纪律：bot 发群卡 @ 不生效且易被折叠）；当前 chat_id=临时测试群，正式启用前切换
notify.json。

**卡片直接发送脚本产出的 JSON，不要手拼**；发送降级链见 `references/notify-card.md`
（飞书连接器 bot 身份 → 本地 lark-cli → 落盘 `notify-unsent-*.md` 并会话内明确提醒）。

### 第 5 步：线索确认与入池闭环（人工）

线索卡确认后把 `log/leads.jsonl` 里对应条目的 `status` 改为 `confirmed` / `rejected`
（可直接编辑，或让 AI 改）。`rejected` 的记录是规则调优的依据。

确认要跟进的线索入池「猎隼线索池」多维表格——**已实装**，走「定时与自动化」章节的
四步流程（`--bitable` 预览 → AI 调研补全 → `--bitable --commit` 写入 → `--lead-card`
重发卡片）。注意 `status`（new/confirmed/rejected，人工确认用）与 `bitable_status`
（pending/pushed/exists/filtered/failed，脚本回写写入结果用）是两个字段：
写入由 `--bitable` 驱动、选 `bitable_status=pending` 的线索，**不要求先改 `status`**。

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
5. 按收方路由发送（卡片已产好，直接发）：情报简报/日报 → 群；线索卡 → 本人私信；
   *-group.json → 群 @。发送命令见 references/notify-card.md 降级链。
工作区默认 ~/.market-intel/。
```

**每日摘要（固定时刻，如 08:30）**：跑 `judge.py --digest` 产卡发群；**发送成功后**再跑
`judge.py --digest --commit` 推进锚点（宁重勿漏：失败不 commit，下次自动重覆盖）。

**线索入池「猎隼线索池」多维表格**（notify.json `bitable` 块驱动，已按真实字段校准）：

1. `judge.py --bitable` —— 把待写线索构建为 `out/bitable-pending.json` 预览（离线）。
   构建时会用**当前规则重算匹配**：旧规则遗留、现已不满足"中国企业+出海"硬筛的线索
   自动标记 filtered 排除；记录只填机器可靠字段（公司名称/业务描述/赛道/标签细分/
   融资轮次/资方/曝光媒体/GCP切入点/跟进状态/抓取时间/官网兜底链接）。
2. **AI 调研补全**（`--bitable` 预览前后皆可）：按 `references/bitable-fields.md` 字段标准，
   用已有数据源（facts 档案/情报流水）+ 联网调研补齐 技术底座/CEO/企业规模/成立年限/
   所在城市/联系方式/打单破冰话术，写 `log/enrichments.jsonl`（契约见该文档）；
   查不到填规定兜底值（未查明/未公开/未查询到有效信息），**不猜**；重新跑
   `judge.py --bitable` 让预览带上补全字段。select 值写入前仍过选项校验。
3. 人工确认预览后 `judge.py --bitable --commit` —— 按「公司名称」精确检索去重
   （已存在跳过），按最新 field-list 校验选项值后写入，结果回写 leads.jsonl 与
   `log/bitable.jsonl`。重复执行幂等（exists 不重写）。
4. `judge.py --lead-card` —— 写入后重产最近一轮线索卡：每条线索来源旁带
   「查看线索」按钮，record 级直达表格对应记录（未入池回退表视图）。发送这版卡片。

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
9. **JS 渲染站点纯 HTTP 抓不到**：AI产品榜（aicpb.com）榜单条目为前端渲染，首页锚点
   只有导航链接（2026-10-23 实测），AppMagic 同理——此类榜单源一期禁用，二期评估
   无头浏览器；静态渲染的列表页（智东西/品牌方舟/投资界）links 模式实测可用。
10. **P0/P1 分级口径**（需求文档）：P0 = 近1月融资 / 近3月业务暴增 / 发布新产品 /
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
14. **情报卡族视觉差异纪律**：market-intel 情报卡统一**紫色系**（violet 头 +
    `report_outlined` 图标 + violet-50/100 描边块），与 change-watch 蓝色铃铛监控卡
    一眼区分（实测 2026-10-23 临时测试群）。语义色共用不改：红=强提醒/P0、
    绿=正常/P1、黄=异常。改样式必须先发临时测试群实测渲染，再发正式群。

## 可移植性

- 全部脚本只用 **Python 3 标准库**（零依赖）；
- 不写死安装位置（`os.path.dirname(os.path.abspath(__file__))` 反推），全目录无 `/Users/xxx` 绝对路径；
- 状态、配置、日志都在工作区，是普通文件；多工具共享就让 `--workspace` 指向同一目录。

## 资源

- `config/sources.json.example` —— 公共源登记册模板（字段说明见 `references/sources-guide.md`）
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
