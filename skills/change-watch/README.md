# change-watch · 变更雷达

持续监控一个目标（公司 / 产品线 / 模型）的变更——**发布、下架、改价、计划下线**——沉淀为可查询的变更流水，只在值得时通知。适合「Google 最近发了什么」「Gemini 模型上下架了吗」「这类变更定时推给我」这类需求。

## 设计要点

- **脚本管确定性**：抓取、指纹、与上一轮差分、落盘、查询——可复现、不花钱、可无人值守。
- **模型管判断**：读变更集，判断是否真变更、多重要、要不要通知。只让「变化」进判断，不全量。
- **两层状态，下架也能抓**：
  - **观测流**：看「谁发了新东西」（`rss` / `json_ld_posts` / `x_timeline`）。
  - **对象登记册**：看「某东西还在不在、状态变没变」（网页清单表）。条目消失 = 下架，`Retirement date` 出现 = 计划下线。

## 支持的目标

当前内置 **1 个目标 · 26 个信源**：

**Google 产品与模型**（`google`）

| 类型 | 数量 | 内容（点击跳转源地址） |
|---|---|---|
| 观测流 · RSS | 13 | [Google 全站](https://blog.google/rss/) · [AI](https://blog.google/technology/ai/rss/) · [Gemini](https://blog.google/products/gemini/rss/) · [开发者博客](https://developers.googleblog.com/feeds/posts/default) · [GCP Release Notes](https://cloud.google.com/feeds/gcp-release-notes.xml) · [Google Cloud](https://blog.google/products/google-cloud/rss/) · [DeepMind](https://deepmind.google/blog/rss.xml) · [Research](https://research.google/blog/rss/) · [开源博客](https://opensource.googleblog.com/feeds/posts/default) · [Android](https://android-developers.googleblog.com/feeds/posts/default) · [Workspace](https://workspaceupdates.googleblog.com/feeds/posts/default) · [Chrome](https://developer.chrome.com/blog/feed.xml) · [Press Corner](https://www.googlecloudpresscorner.com/press-releases?pagetemplate=rss) |
| 观测流 · LinkedIn | 3 | [Google Cloud 官方页](https://www.linkedin.com/showcase/google-cloud/) · [Google DeepMind](https://www.linkedin.com/company/googledeepmind/) · [Google](https://www.linkedin.com/company/google/)（免登录 JSON-LD，含全文 + 时间） |
| 观测流 · X | 6 | [@Google](https://x.com/Google) · [@GoogleAI](https://x.com/GoogleAI) · [@GoogleDeepMind](https://x.com/GoogleDeepMind) · [@GoogleAIStudio](https://x.com/GoogleAIStudio) · [@googledevs](https://x.com/googledevs) · [@GoogleCloudTech](https://x.com/GoogleCloudTech)（免登录 guest 通道，含全文 + 链接） |
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
# 每 30 分钟抓取+差分（轻量，约 26 个 HTTP 请求，并发约 12 秒）
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

### v20261014.2 · 2026-10-14（午间）

判定准则补「跨轮同事件」指引：同一事件被多个源在不同轮次采集到时（实测：新加坡端侧诈骗检测，
博客+X 那轮已合并记录，LinkedIn 公司页同内容 4 小时后才到），判定前先查 `log/changes.jsonl`
近期记录，命中已记录事件则 `discard` 并引用其 `change_id`——流水只留一条、不重复通知。
（纯准则修订，代码与配置零改动。）

### v20261014.1 · 2026-10-14（当晚）

采集质量修复：四个问题全部来自当日生产数据的实测复盘，修法都以「不制造假告警」为前提，受影响基线已无损迁移。

- **【修复·X】纯媒体帖不再进流水**：视频/图片帖常无正文，`full_text` 只剩一条指向本条推文自己媒体附件的 t.co 短链，无可读文本——曾每天制造 3 条"待确认"噪音。现在"正文仅剩一个 t.co 短链 + 有媒体 + 无文章链接"的推文直接忽略（不进基线、不占判断配额）。**带图说的视频帖不受影响。**
- **【修复·X】长文（note tweet）不再被截断**：超过约 280 字的长帖，`legacy.full_text` 只是截断展示版，完整正文在 `note_tweet` 里——实测 Gemini 4 Argon 定价公告的后半段（$2/$10 每百万 token）此前被静默丢掉。现在优先取 note 全文。
- **【修复·X】X 文章（Article）有正文可判了**：长文帖正文只剩 `x.com/i/article/…` 短链，全文取不到（文章页 404、`withArticlePlainText` 开关无效），但推文自带 `article` 对象含 `title` + `preview_text`——实测 @GoogleCloudTech 的长文仅凭标题+预览即可定性。
- **【修复·RSS】共用链接的流不再产生 phantom changed**：`parse_feed` 原来键 = 链接，冲突就按顺序加 `#2 #3…`，键实际是"第几条"。GCP 发布说明流 30 条 entry 全指向同一个 URL，每日轮换时条目位置整体偏移——内容没变却整批报 changed（实测单轮 30 条 phantom changed + 10 条 phantom added）。改为链接共用时取 Atom `id` / RSS `guid` 作键；**链接唯一的流行为不变**（12 个博客流逐个验证零漂移）。
- **【迁移】** 6 个 X 源 + `gcp-release-notes` 的基线用新解析器迁移，规则是「只迁旧基线里已有的条目，新增条目留给下一轮正常报出」——零假告警过渡，真实变更一条不吞（release-notes 30/30 同内容换键）。
- **【验证】** 新增夹具 `.workbuddy/verify_parse.py`（16 项：note 全文/纯媒体忽略/文章标题预览/feed 键稳定性含轮换对比/6 项回归）；三套夹具共 41 项全绿；端到端 4 轮：26 源 0 失败、迁移后 0 changed / 0 removed。

### v20261014 · 2026-10-14

稳定性与效率版：并发采集 + 三道防失效闸门。全部改动以「并发结果与串行逐源等价」为验收标准。

- **【性能】并发采集（38.7s → 12.5s，3.1×）**
  - 26 个源改为并发抓取。三条约束保证"并发不改变结果"：① 结果**按配置顺序**返回（stdout 与 `latest.json` 的顺序不随完成情况抖动）；② **daemon 工作线程**（刻意不用 `ThreadPoolExecutor`——它的线程非 daemon，解释器退出会 join，卡住的源会把定时任务拖死，正是壁钟上限要避免的事）；③ 单源异常/超时只体现为该源 `ok=False`，不外溢。
  - **X 通道仍串行**（`X_CONCURRENCY=1`）：guest 通道共用 token 且限流最严，串行与改造前时序等价；token 激活加锁，并发下只激活一个。
  - **单域名限流** `host_concurrency=2`（`blog.google` 4 个源、`cloud.google.com` 3 个）——且闸只做**有限等待**：一个源卡住不会占着闸位把同域名其它源全拖到超时（实测过这个失败模式）。
  - 新 `settings`：`collect_concurrency`（默认 6）、`host_concurrency`（默认 2）、`collect_wall_clock_seconds`（默认 300，超时未返回的源判失败并继续出结果）。**排查时把 `collect_concurrency` 设 1 即退回串行。**
  - 实测：并发与串行两轮**逐源条目数完全一致**（26/26 源 0 失败）。
- **【加固】单实例锁**：两个 `watch` 并发会各自基于同一份旧基线算 diff 再互相覆盖 `latest.json` / `pending.json`，结果是谁的变更集都不完整且极难排查。现在拿不到锁就**跳过本轮（退出码 3）**，不硬跑。实测：并发第二个进程 exit 3 并明确提示。
- **【修复】表格 colspan 分组行不再被当成数据**：`ai.google.dev` 弃用清单每组模型之间夹一行单单元格的 "Preview models"（14 处）。它不是任何模型的记录，却因第一列同名被加 `#2 #3 …` 后缀——页面只要新增/删除一个分组，后面所有后缀整体错位，凭空制造一批"变化/消失"。判据收紧为**仅单列行**（实测过放宽成"少于典型列数"会误删 `gemini-api-models` 的 20 条 2 列真实记录）。生效后该源 100 → 86 条，73 个模型全部保留。
- **【加固】覆盖度校验不再能静默失效**：`log_change.py --input` 的漏判点名以 `out/latest.json` 为基准；该文件不存在时"空集合减任何集合恒为空"→ 校验会**静默判定为全部覆盖**。现在显式告警并回退出码 2（实测：39 条只判 1 条时点名 38 条并 exit 2；`latest.json` 缺失时提示"本轮没做覆盖度校验"）。

### v20261013.2 · 2026-10-13
当日第二次修订，目标是把「源不稳」与「伪告警」两类噪声压到零（均带实测取证）：

- **【修复】LinkedIn company 页 HTTP 999（反爬限流）**：`fetch()` 的请求头从 5 个裸字段升级为**完整 Chrome 浏览器头**（补 `sec-ch-ua` / `Sec-Fetch-*` / `Referer` / `Upgrade-Insecure-Requests` / `Connection` 等）。实测（同 IP、交替请求、2s 间隔，12 次/组）：旧头下 `/company/googledeepmind`、`/company/google` **12 次请求失败 10 次（83%）**，新头 **12/12 全成功**；对 blog.google / developers.googleblog / gcp-release-notes 三个 feed 与 ai.google.dev、cloud.google 四个登记册逐次比对状态一致、条目数一致、字节差 <0.1%，**零副作用**。
  - 结论：999 是「请求头不像浏览器」触发，不是 IP 级封禁；**未引入 curl_cffi 等第三方依赖**（保住零依赖可移植性）。curl_cffi 的 TLS 指纹伪装实测同样 6/6 成功，作为将来 LinkedIn 升级到 TLS 指纹检测时的备选记录在代码注释里。
- **【修复】窗口收窄被误报成「内容消失」**：有限窗口流每次返回的条数并不固定——实测 X guest 通道抓 @Google，上一轮 15 条、下一轮 12 条，本轮**没有任何新条目**，最旧 3 条仍被报成 `removed`。`split_rolled_out()` 因此新增**情形 B**：windowed 源在无新条目、但本轮条数确实少于上轮（窗口收窄）且消失项早于当前窗口最旧项时，同样判为「滚出」而非「删除」，记入 `log/rolled_out.jsonl`。保守边界不变（窗口没收窄却少了条目、非 windowed 源、首轮无 prev，仍按真消失处理）。
- **【配置】13 个 RSS 源声明 `windowed: true`**：RSS/Atom feed 只保留最近 N 条（20/25/30/100），新文章进入后最旧几条必然掉出——此前未声明 windowed，每轮都把它们报成「文章被删除」。**已取证**：本轮 5 条 RSS `removed` 的原文全部 HTTP 200 仍可访问，确为窗口滚动。注意 **registries（对象登记册）绝不设 windowed**——它们是完整清单，「消失」就是真的下架，是本系统的核心价值。
- **【配置】9 个社交源（6 个 X + 3 个 LinkedIn）停更阈值放宽到 336h**：社交账号节奏不规则（@GoogleAIStudio 上次发推是 10-06 的 Nano Banana 2.1，7 天不发属正常），原用全局 168h 会在阈值两侧抖动（实测 168.1h 刚越界即告警）。
- **【清理】** 已证伪的 8 条历史告警（3 条 X 窗口收窄 + 5 条 RSS 滚动）补记 `log/rolled_out.jsonl` 移出待判队列，留痕可查。
- **【验证】** `split_rolled_out` 7 个场景单测全绿（含「窗口未收窄=真消失」「非 windowed=真消失」「首轮 prev=None=保守」等回归）；端到端复跑 3 轮：**26 源失败 0 / 停更告警 0 / 消失 0**，滚出正确归类，待判队列只剩真实新增。

### v20261013.1 · 2026-10-13
当日修订，由多轮验证矩阵（U1 单元 + T1–T19 场景 × 3 轮）驱动的三项加固：

- **【加固】`--digest` 全程持有 digest 排他锁**：此前只有 `--commit` 持锁，`--digest` 与 `--commit`（或两个 `--digest`）并发时可能出现「旧锚点读取 + pending 覆盖」竞争。现在 `--digest` 也在锁内运行——并发的第二个 run 会在读锚点前看到已提交的新锚点，窗口自动正确切分。
- **【修复】0 条摘要卡文案**：「今日（截至 X）暂无已确认的变更」→「截至 X 暂无已确认的变更」，delta 多日窗口下不再误导读者。
- **【加固】`digest.seed_hours` 容错**：非数字（如手误 `"24h"`）时 stderr 警告并按 24h 处理，不再 traceback；负值钳到 0。
- **【验证】** 单元 + 19 场景连续 3 轮全绿（分区性/盲区回归/重覆盖/同刻重跑/多日间隔/时钟回退/未来戳/并发/损坏检查点/过期 pending/无 pending/seed 边界/include_low/day 回归/卡片干净度/禁用开关/配置容错）；live 复跑：增量 0 新条 → 发临时群（本人身份）→ commit 落锚 → 采集 18 源全 ok → 1 条判无关 → 待判队列核销 → 群通道自检 ok。

### v20261013 · 2026-10-13
修复每日摘要的窗口盲区，改为「检查点增量窗口」，向后兼容、可一键回退：

- **【修复】消除摘要约 15.5h 盲区**：`--digest` 原锚定自然日零点（`[今天 00:00, 发信时]`），相邻两日摘要之间有约 15.5h（昨晚发完→今晨 00:00）的变更永不进任何群摘要。改为 **delta 模式**（默认）：锚定「上次已交付摘要」，窗口 = `(上次 sent_at, 本次运行]`，首尾相接、多日间隔自动吸收、不重不漏。
- **【新功能】`--digest --commit`（Tier 1 防漏）**：`--digest` 现在**只产卡 + 写 `state/digest_pending.json`，不推进锚点**；须由调用方在**发送成功后**跑 `--digest --commit` 才把锚点推进到本次选取上界（`sent_at = window_to`，绝不写"现在"）。**宁重勿漏**：发送失败不 commit → 锚点不推进 → 下次 `--digest` 自动重覆盖本窗口重试，绝不漏；忘 commit 只会重复一次（可恢复）。含 flock 并发锁、单调保护（锚点不倒退）、原子写；锚点抬到「窗口内最大 logged_at」以防时钟前移/未来戳导致的重复覆盖。
- **【配置】** `targets.json` 的 `digest` 块新增 `mode`（`delta` 默认 / `day` 旧行为可回退）与 `seed_hours`（首跑无锚点时的回溯时长，默认 24h）。
- **【验证】** 分区性/盲区回归/发送失败重试/同刻重跑/跳日/时钟回退/空窗推进/day 回归（T1–T8）全绿；live 首跑真实产卡、发临时群（本人身份）、commit 落锚并增量复核通过。
- **【文档】** SKILL.md 版本号与每日摘要说明同步更新。

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

