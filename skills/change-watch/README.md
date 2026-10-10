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
   - total==0 但有异常 → `python3 <技能目录>/scripts/log_change.py --status-card` 后按 notify-card.md 发状态卡（私信+失败源群@）。
   - total>0 → 下一步。
3) 按 references/criteria.md 对 changes 每条判断，原样带回 change_id，写 judged.json。
4) `python3 <技能目录>/scripts/log_change.py --input judged.json`（漏判会报错）。
5) 按 references/notify-card.md 发 out/card-*.json（私信+高优群@）。
工作区默认 ~/.change-watch/。不要手拼卡片。
```

> 占位符：`<技能目录>` 换成技能解压路径，`<工作区>` 默认 `~/.change-watch/`。

### 方案 B：纯命令行兜底（只抓取，适合服务器 / 无 AI 环境）

```cron
# 每 30 分钟抓取+差分（轻量，约 18 个 HTTP 请求）
*/30 * * * * cd <技能目录> && python3 scripts/watch.py >> ~/.change-watch/log/runs.log 2>&1
```

抓取结果写在 `out/latest.json`，需要判断 + 通知时再跑方案 A 的第 3–5 步（或手动让 AI 工具读 `latest.json`）。

### 节奏建议

理想 15~30 分钟一次；间隔越长最坏延迟越大，**别用「每天一次」当主巡检**（最多做每日汇总）。固定时刻巡检与间隔巡检可并存（例如每 30 分钟轻巡检 + 每天 05:00 日报）。

## 资源

- [`config/targets.json`](config/targets.json) —— 目标 / 信源配置
- [`references/criteria.md`](references/criteria.md) —— 判断准则（改口味改这里）
- [`references/notify-card.md`](references/notify-card.md) —— 卡片模板与发送降级链
- [`scripts/watch.py`](scripts/watch.py) —— 抓取 + 差分
- [`scripts/log_change.py`](scripts/log_change.py) —— 记录判断、生成卡片
- [`scripts/query.py`](scripts/query.py) —— 检索 / 统计

## 版本更新（Changelog）

技能采用 **date-stamp 版本号**（`vYYYYMMDD` 当日首版，同日修复追加 `.N`）。

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

### v20261010 · 2026-10-10
当日首版，从 AIHOT 框架移植的变更监控能力（对象登记册 + 指纹差分 + 待判队列 + 证据溯源 + 健康三态），详见 [`research/`](../../.workbuddy/research/)（如 `2026-10-10-aihot-capability-audit.md`）。

