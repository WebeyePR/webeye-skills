# change-watch · 变更雷达

持续监控一个目标（公司 / 产品线 / 模型）的变更——**发布、下架、改价、计划下线**——沉淀为可查询的变更流水，只在值得时通知。适合「Google 最近发了什么」「Gemini 模型上下架了吗」「这类变更定时推给我」这类需求。

## 设计要点

- **脚本管确定性**：抓取、指纹、与上一轮差分、落盘、查询——可复现、不花钱、可无人值守。
- **模型管判断**：读变更集，判断是否真变更、多重要、要不要通知。只让「变化」进判断，不全量。
- **两层状态，下架也能抓**：
  - **观测流**：看「谁发了新东西」（`rss` / `json_ld_posts` / `x_timeline`）。
  - **对象登记册**：看「某东西还在不在、状态变没变」（网页清单表）。条目消失 = 下架，`Retirement date` 出现 = 计划下线。

## 支持的目标

当前内置 **1 个目标 · 18 个信源**：

**Google 产品与模型**（`google`）

| 类型 | 数量 | 内容（点击跳转源地址） |
|---|---|---|
| 观测流 · RSS | 12 | [Google 全站](https://blog.google/rss/) · [AI](https://blog.google/technology/ai/rss/) · [Gemini](https://blog.google/products/gemini/rss/) · [开发者博客](https://blog.google/technology/developers/rss/) · [Google Cloud](https://blog.google/products/google-cloud/rss/) · [DeepMind](https://deepmind.google/blog/rss.xml) · [Research](https://research.google/blog/rss/) · [开源博客](https://opensource.googleblog.com/feeds/posts/default) · [Android](https://android-developers.googleblog.com/feeds/posts/default) · [Workspace](https://workspaceupdates.googleblog.com/feeds/posts/default) · [Chrome](https://developer.chrome.com/blog/feed.xml) · [Press Corner](https://www.googlecloudpresscorner.com/press-releases?pagetemplate=rss) |
| 观测流 · LinkedIn | 1 | [Google Cloud 官方页](https://www.linkedin.com/showcase/google-cloud/)（免登录 JSON-LD，含全文 + 时间） |
| 观测流 · X | 1 | [@Google](https://x.com/Google)（免登录 guest 通道，含全文 + 链接） |
| 对象登记册 · 网页表格 | 4 | [Gemini API 模型清单](https://ai.google.dev/gemini-api/docs/models) · [Gemini API 弃用清单](https://ai.google.dev/gemini-api/docs/deprecations) · [Vertex AI 模型版本](https://cloud.google.com/vertex-ai/generative-ai/docs/learn/model-versions) · [Vertex AI 弃用清单](https://cloud.google.com/vertex-ai/generative-ai/docs/deprecations) |

> 加目标或信源：编辑 `config/targets.json`（首次运行复制到工作区），**改配置不碰代码**。

## 渠道（通知怎么发）

卡片由脚本生成（`out/card-*.json`，标题 / 中文标签 / 来源名都已写好），**直接发这份 JSON，不要手拼**。按降级链依次尝试：

1. **[宿主工具连接器（飞书等）](https://open.feishu.cn/)** —— 优先用 **bot 身份**（用户身份常缺发消息权限）。
2. **本地 [lark-cli](references/notify-card.md)（bot 身份）** —— 连接器不可用时的兜底，发送命令见 [notify-card.md](references/notify-card.md)。
3. **都失败** —— 简报全文写入 [out/notify-unsent-<时间>.md](out/)，并在会话内**明确提醒**，绝不静默吞掉。

飞书卡片支持跳转溯源：变更卡片每条带来源链接；状态简报里停更 / 失败源是可点击主页链接（附「约 N 天」）。

## 快速开始

```bash
# 0) 首次使用检查（环境 / 网络 / 发送通道 / 配置引导；CI 加 --non-interactive）
python3 scripts/watch.py --init
#    推荐：由 AI 驱动 —— AI 跑 --init --non-interactive 读「待办」，依环境选飞书通道
#    （连接器/lark-cli）与用户确认目标后查 ID，再用 `watch.py --set-user-id=… --set-chat-id=…` 写回。
#    纯终端无 AI 时：交互 --init 会辅助用本机 lark-cli 查并确认写回；无 lark-cli 则给指引+待办文件。

# 1) 首次：建基线（不推送，避免把历史当新消息）
python3 scripts/watch.py --baseline

# 常规一轮：产出 out/latest.json，先看 summary
python3 scripts/watch.py

# 把判断结论落盘并生成通知卡片
python3 scripts/log_change.py --input judged.json

# 无变更但需知会：生成「扫描状态简报」卡片
python3 scripts/log_change.py --status-card

# 检索历史变更
python3 scripts/query.py --timeline
python3 scripts/query.py --object gemini --stats
```

## 定时任务（装完即用）

技能本身不自带调度。跑通变更通知需要「抓取 → 判断 → 发送」三段连跑，给两个最常用方案。

### 方案 A：AI 工具的定时 / 自动化（推荐，自动判断 + 发送）

把下面这段作为定时任务指令（替换占位符），再选频率即可：

- **固定时刻巡检**：每天 05:00（或 09:00）各一次 —— 日报型。
- **间隔巡检**：每 30 分钟 / 每 1 小时 / 每 10 小时 —— 变了尽快知道。

```text
你正在按计划执行 change-watch 巡检。依次：
1) 运行 `python3 <技能目录>/scripts/watch.py`（退出码 1=配置错，2=有源失败，都继续）。
2) 读 <工作区>/out/latest.json 的 summary：
   - total==0 且无失败/停更 → 结束（不打扰）。
   - total==0 但有异常 → `python3 <技能目录>/scripts/log_change.py --status-card` 后**发本人私信**（有失败源会额外产出群卡，一并发群 @）。
   - total>0 → 下一步。
3) 按 references/criteria.md 对 changes 每条判断，原样带回 change_id，写 judged.json。
4) `python3 <技能目录>/scripts/log_change.py --input judged.json`（漏判会报错）。
5) 按接收方路由发卡片（脚本已产好，直接发，不要手拼）：
   - 实时变更卡 `out/card-<目标>-<stamp>.json` → **本人私信**；
   - 高优群卡 `out/card-<目标>-<stamp>-group.json`（仅 high 时出现）→ **群 @**。
工作区默认 ~/.change-watch/。不要手拼卡片。
```

> 占位符：`<技能目录>` 换成技能解压路径，`<工作区>` 默认 `~/.change-watch/`。

> **按接收方路由（推荐）**：脚本只产卡、不发送，发给谁完全由你的提示词决定。常见分化——实时变更卡发**本人私信**、高优群卡发**群 @**；另建一个固定时刻任务跑 `log_change.py --digest`，把产出的 `out/card-digest-<stamp>.json` 发**群**、健康卡发**本人私信**。这样本人只收「变了」的实时提醒，群收每日定时汇总。

### 方案 B：纯命令行兜底（只抓取，适合服务器 / 无 AI 环境）

```cron
# 每 30 分钟抓取+差分（轻量，约 18 个 HTTP 请求）
*/30 * * * * cd <技能目录> && python3 scripts/watch.py >> ~/.change-watch/log/runs.log 2>&1
```

抓取结果写在 `out/latest.json`，需要判断 + 通知时再跑方案 A 的第 3–5 步（或手动让 AI 工具读 `latest.json`）。

> ⚠️ 两点：① `--health` 必须跟在**每次**采集后跑——源健康跃迁只在状态变化那一轮的 `latest.json` 出现，下一轮即覆盖，攒到固定时刻再跑会**永久丢失**告警。② 部分受管环境装不了 `cron` / `launchd`，此时用方案 A 的「采集与判断同轮」即可，效果等价。

### 节奏建议（按实测修正）

- 宿主定时任务的最细粒度通常是**每小时**（`MINUTELY` 多不支持，`BYMINUTE` 在 `HOURLY` 下会被忽略），所以主巡检按**每小时**建一条即可。
- 采集与判断在**同一轮**内完成时，端到端延迟 = 轮次间隔 + 采集耗时 ≈ **典型 30 分钟、最差 1 小时**。
- **通知延迟由 AI 轮次频率决定，与采集频率无关**：单独把采集高频化并不能让通知更快（反而因两次间隔叠加更慢）。
- **别用「每天一次」当主巡检**（最多做每日汇总）。

## 资源

- [`config/targets.json`](config/targets.json) —— 目标 / 信源配置
- [`references/criteria.md`](references/criteria.md) —— 判断准则（改口味改这里）
- [`references/notify-card.md`](references/notify-card.md) —— 卡片模板与发送降级链
- [`scripts/watch.py`](scripts/watch.py) —— 抓取 + 差分
- [`scripts/log_change.py`](scripts/log_change.py) —— 记录判断、生成卡片
- [`scripts/query.py`](scripts/query.py) —— 检索 / 统计

## 版本更新（Changelog）

技能采用 **date-stamp 版本号**（`vYYYYMMDD` 当日首版，同日修复追加 `.N`）。

### v20261011.2 · 2026-10-11
当日第三版，按「频繁检测要心跳但必须节流、日级摘要不管有无结果都发」的原则补齐发声策略：

- **【新功能】每日摘要改为「有无变更都发」**：`log_change.py --digest` 不再因今日 0 变更而静默。
  无变更时产出「本时段变更 0 条」卡（含扫描统计），有变更时照常聚合。日级间隔够长，这本就是
  足够长的发声，不需要心跳概念、也不算噪音。同时**修正发送目标**：摘要卡现在打印群 `chat_id`
  发送命令（此前误打成私信），与「每日摘要发群」的设计一致。
- **【新功能】巡检心跳（节流存活确认）**：新增 `log_change.py --heartbeat`。频繁检测不能静默到
  让人怀疑它挂了，但也不能每小时刷屏抵消去噪声——故按 `state/last_heartbeat.json` 节流，默认
  **距上次 >= 12h 才发一次「巡检心跳」卡**（证明监控仍在运行、最近一次扫描正常），未到期自己静默。
  心跳发给**本人私信**（不停刷群）。阈值可在 `targets.json` 的 `heartbeat.min_hours` 覆盖（下限 1h，
  建议 >=12h、可上调到一天）。每小时巡检任务的「本轮无变更」分支现在会跑 `--heartbeat`。
- **【配置】** `targets.json` 新增 `heartbeat: {min_hours: 12}`（模板与工作区均已加，附 `_comment`）。
- **【文档】** SKILL.md / 本 changelog 同步版本号；说明日级摘要与节流心跳的分工。

### v20261011.1 · 2026-10-11
当日第二版，修掉 X @Google 渠道异常与一批审计发现，均为向后兼容：

- **【修复·X 渠道】guest 通道抖动不再把整源判死**
  - 实测：guest token 激活的冷启动握手要 15~16 秒，而原超时写死 20 秒（余量仅 4 秒），已真实出现过一次 **SSL 握手超时**把 X 源判成失败、并触发一次源健康告警（历史 36 轮中 1 次 `TimeoutError`）。
  - 现在：token 激活超时放宽到 30s；对 **429 / 5xx / 连接与读超时退避重试 2 次**；401/403 **作废 token 重取**再试；**404（queryId 轮换失效）与 GraphQL 业务错误不重试**，立即报错让人看到。
- **【新功能】有限窗口流：滚动 ≠ 消失**
  - X 时间线 / LinkedIn 展示页只暴露「最近 N 条」窗口，新内容进来时最旧几条被挤出——朴素差分会把它报成 `removed`，制造"下架"假告警（实测 10-10 一轮里 1 条新增伴随 3 条"消失"，其中 3 条全是滚动）。
  - 源配置新增 `windowed: true`（默认关闭，不猜测）。三条件**同时成立**才判滚动：源声明 windowed + 本轮有新条目 + 消失条目早于当前窗口最旧条目。任一不成立即按真消失处理——宁可多报，不把真下架当滚动吃掉。
  - 滚动条目不进变更集、不占 AI 判断配额，只追加 `log/rolled_out.jsonl`；`query.py --rolled-out` 可查（沉默必须有据）。
  - 实测 7 个判定用例全部符合预期，端到端复现真实事件：同一场景变更数从 4 条降到 1 条。
- **【改进】状态卡显示「数据采集于 北京时间 X」**：状态卡是安静时唯一的发声渠道，原先只有卡片生成时间；采集若真停了、`latest.json` 冻在几天前，卡片看起来却一切正常。
- **【修复】单次网络抖动自愈不再刷「已恢复」告警**：原逻辑只要跃迁终点是 `ok` 就发恢复卡，于是 `flapping → ok`（一次失败后自愈）也会通知。实测各源偶发失败率约 2.5%/轮，18 个源下这会频繁打扰。现在**只有 `down → ok`（从持续失联恢复）才通知**；`watch.py` 的 stdout 同步改用同一口径。
  - 实测四种跃迁：flapping→ok 不发卡、down→ok 发卡、ok→down 发卡、ok→flapping 不发卡。
- **【文档】** 陷阱清单补 guest 重试策略与 LinkedIn 集合抖动说明；沉淀文件表补 `rolled_out.jsonl`；检索命令补 `--rolled-out`。

### v20261011 · 2026-10-11

为「采集可以更频繁、判断可以滞后」做准备的两处**可靠性**改动，均为向后兼容：

- **【修复 · 硬前置】未判变更的过期改为按「年龄」，不再按采集轮次**
  - 原 `MAX_REPLAY = 3` 按 `watch.py` 运行次数计数：一旦采集高频化（如每 15 分钟），未判变更约 1 小时内就被判死并丢进 `log/unjudged.jsonl`——**变更被自己人吃掉**。
  - 改为 `MAX_PENDING_AGE_H = 24`，按 `first_seen` 距今时长过期，与采集频率彻底解耦。
  - 实测：一条「刚发现但已重放 3 次」的记录，旧逻辑本轮就会丢弃，新逻辑保留；连跑多轮后未判项仍全部存活。
- **【修复】中断不再静默丢失变更**
  - 原 `watch.py` 在循环内立即写各源状态，而 `latest.json` 最后才写。运行中段被杀（休眠 / 重启 / 超时）会让已检出的变更**进了基线却没进变更集**，下轮不再出现 = 永久丢失（实测一次中断吞掉 4 条）。
  - 改为「先落盘变更，最后统一提交各源状态」。失败模式从「静默丢失」变为「下轮重检」，重检由 `dedupe_key` 去重，无害。
  - 实测：跑到 75 秒强杀后 **0 个状态文件被推进**，`latest.json` 未变。
- **【文档】** `--health` 必须每轮紧跟采集（跃迁会被下一轮覆盖）；补充宿主调度粒度实测结论（最细每小时）。「3 轮」表述全部同步为「未判年龄 24 小时」。

### v20261010.1 · 2026-10-10
当日第二版，含三波实质改动（均不影响监控抓取与数据落盘）：

- **【新功能 · v3.1】通知双通道 + 三刀纪律**
  - 变更通知默认**全级别实时发**（high / normal / low 全部立即发，仅卡片样式区分；`unknown` 仍强制不通知）。
  - 新增 `--health` **一次性健康跃迁告警**（ok→down / →ok 各告警一次，持续失联静音，去重落盘 `state/health_alerts.json`）。
  - 新增 `--digest` **每日变更摘要**（北京 00:00 起聚合 + 失败源脚注，空则静默），配置块 `digest` 已被 `watch.py` 白名单接受。
  - 文档补充「双通道 + 三刀（不重复发 / 空跑静默 / 非噪声才发）」纪律与推荐自动化组合（10h 实时 + 08:30 摘要/健康）。
- **【修复】变更卡时间丢失**：`_published_for` 兜底链原本只取 `published → latest.json → first_seen`，而生产记录三者皆为 `None`，导致卡片从不显示时间。现回退到永远有值的 `logged_at`（检测时间），每条变更卡恢复「北京时间 MM月DD日 HH:MM」。
- **【优化】摘要卡信息密度 + 序号**：原摘要直接复用变更卡大块 + 长摘要，密度低、记录间间距过大。改为**统计块 + 紧凑编号列表（1./2./3.…） + 失败源脚注**，记录间间距由 ~28px 收紧到 5px 内边距 + 3px 间隔，去掉「来源 N 个 ·」啰嗦前缀。

> 稳定性加固（同版内）：`source_pairs()` 在记录同时缺 `sources` 与 `url`（旧版/脏落盘）时不再 `KeyError` 崩溃，退化为空列表。
- **【文档】应用示例（自动化 prompt 模板）补全「按接收方路由」**：实时变更卡 `card-<目标>-<stamp>.json` → 本人私信；高优群卡 `card-<目标>-<stamp>-group.json` → 群 @；每日摘要卡 `card-digest-<stamp>.json` → 群；健康卡 `card-health-<stamp>.json` → 本人私信。明确「脚本只产卡、发给谁由提示词决定」，便于他人装完即按此分化。

### v20261010 · 2026-10-10
当日首版，从 AIHOT 框架移植的变更监控能力（对象登记册 + 指纹差分 + 待判队列 + 证据溯源 + 健康三态），详见 [`research/`](../../.workbuddy/research/)（如 `2026-10-10-aihot-capability-audit.md`）。

