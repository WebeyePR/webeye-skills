# market-intel · 市场情报雷达

> **新环境/新会话的 AI 从这里开始。** 本文是上手入口与文档地图；操作细节在
> [`SKILL.md`](SKILL.md)（Agent 作业标准），本文不重复其内容。

持续监控行业动态 + 按规则筛出潜在客户，沉淀为可查询的情报流水、企业档案与线索库，飞书通知。
服务业务：**WebEye 市场部获客**（需求源：《市场情报内参闭环需求文档》），确认线索入
「猎隼线索池」飞书多维表格，供市场同事建联。

## 现状（2026-10-26，v0.3.0）

| 需求版块 | 实现状态 |
|---|---|
| 潜在客户挖取 | ✅ 三层规则（三维域限定 + P0/P1 信号组）→ 线索卡 → 猎隼池入池闭环 |
| 线索调研补全 | ✅ **14 必研字段硬门槛**：`--research-check` 审计，未齐全 `--commit` 拒绝入池（exit 4）；查不到填兜底值，不猜不缺 |
| 重点资讯情报 | ✅ 14 源在采（36氪/财联社/钛媒体/量子位/白鲸出海 等），差分 → AI 判断 → 情报卡 |
| 活动情报 | ✅ 活动行源 + **详情页富化**（时间/地点/报名规模自动补全，禁写「见报名页」） |
| 榜单监控 | ✅ AI产品榜 aicpb（links 模式）；内参卡含「海外软件榜单·新晋黑马」段（只报新上榜，基线后无新晋则该段空）；AppMagic 纯 SPA 停用留档 |
| 招聘类网站 | ⏸ boss直聘纯 HTTP 不可行（SPA+反爬），登记停用留档；招聘信号由官网招聘页差分 + 媒体报道承担 |
| 每日内参 | ✅ 需求文档版式四段卡（活动日历/行业重点新闻/榜单监控/潜在客户与业务情报，空段不占位） |

## 环境前置依赖（一句话装完技能后，跑通链路还需要这些）

| 依赖 | 用途 | 缺了会怎样 | 备注 |
|---|---|---|---|
| **飞书连接器**（宿主 AI 授权） | 发卡首选通道（私信 bot / 群卡 user） | 卡片只能落盘发不出 | 见 `references/notify-card.md` 降级链 |
| **lark-cli**（已装并 `auth login`） | 发卡兜底、猎隼池写入、查群/表信息 | `--bitable --commit` 不可用、降级链断裂 | 群 ID 用 `lark-cli im +chat-search` 查 |
| **RSSHub 本地服务** | `rsshub://` 前缀的源（36氪快讯等） | 对应源采集失败（按源失败处理，不致命） | 仓库根 [`AGENTS.md` 3.5](../../AGENTS.md)；纯 https 源不受影响 |
| **WebEye 飞书租户素材** | 内参卡头图/图标 img_key、猎隼池表 token | 跨租户头图不渲染、写入错表 | 同租户开箱即用；换租户先换 `notify.json` 的 bitable 块与 `judge.py BRIEFING_IMGS` |

**脚本本身零外部依赖**（Python 3 标准库，无绝对路径硬编码）——上表全是链路级依赖，
跑测试（第 1 步）不需要任何一项。

## 新环境启动清单（换机器/首次部署按序执行）

1. **跑测试**：`python3 skills/market-intel/tests/test_market_intel.py` → 应全绿（零外部依赖，Python 3 标准库）。
2. **初始化工作区**：`python3 skills/market-intel/scripts/pipeline.py --init`（默认 `~/.market-intel/`）。
3. **配源**：编辑工作区 `sources.json` 启用想要的源。`rsshub://` 前缀的源依赖本地 RSSHub——
   先看 [`AGENTS.md`「本地基础设施」](../../AGENTS.md) 确认服务状态，部署/渠道/排错见
   [`references/rsshub-sources.md`](references/rsshub-sources.md)；地址变了只改 `settings.rsshub_base` 一处。
4. **配通知**：编辑工作区 `notify.json`（飞书 user_id / chat_id / mention；`lark-cli im +chat-search` 查群 ID）。
   **正式启用前把 chat_id 从测试群切到正式群。**
5. **建基线**：`pipeline.py --baseline`（只记快照不推送，否则把历史当新消息）。
6. **验证一轮**：`pipeline.py --run` → 看 `out/latest.json` → 按 SKILL.md 第 3 步判断 →
   `judge.py --input` → 先发**临时测试群**实测卡片渲染，链路通了再转正式。
   有待入池线索时 `judge.py --research-check` 审计调研完整度（14 必研字段，未达标
   `--commit` 会拒绝写入）；调研齐全即 `judge.py --bitable --commit` **自动入池**
   （无需人工确认，人工事后在表内核查）。
   ⚠️ 人工触发与巡检的**默认通知都是一张内参大卡**（市场团队内参）；情报卡/线索卡按需
   点播才发，线索入池闭环正常推进（发送纪律见 SKILL.md 第 4 步「行为边界」）。
7. **建自动化**：巡检（每小时）+ 每日内参（早间）两条宿主任务，prompt 模板在 SKILL.md「定时与自动化」。

## 文档地图（要做什么 → 看哪份）

| 任务 / 问题 | 看这里 |
|---|---|
| 日常怎么跑一轮、怎么判断、怎么发卡 | [`SKILL.md`](SKILL.md)（工作流 + 陷阱清单，**操作前必读**） |
| 有哪些卡片、各自发给谁、何时出现 | SKILL.md 第 4 步卡片族表 |
| 加新源 / 改源配置 | [`references/sources-guide.md`](references/sources-guide.md) |
| RSSHub 部署、换地址、路由排错、可用渠道 | [`references/rsshub-sources.md`](references/rsshub-sources.md) |
| 情报判断口径（什么值得通知） | [`references/intel-criteria.md`](references/intel-criteria.md) |
| 线索/事实抽取口径（八行业、kinds、防张冠李戴） | [`references/lead-criteria.md`](references/lead-criteria.md) |
| 活动情报白名单/黑名单 | [`references/activities-criteria.md`](references/activities-criteria.md) |
| 线索规则怎么改（不改代码） | [`references/rules-guide.md`](references/rules-guide.md) |
| 猎隼池 20 字段标准与调研兜底值 | [`references/bitable-fields.md`](references/bitable-fields.md) |
| 卡片样式与发送降级链 | [`references/notify-card.md`](references/notify-card.md) |
| 本机服务（RSSHub 等）状态登记 | [`AGENTS.md` 3.5 本地基础设施](../../AGENTS.md) |

## 与 change-watch 的关系

借鉴其工程模式（差分/待判队列/健康三态/通知纪律），语义一致但**不共享代码**；
企业归一与信源分级沿用 AIHOT 的词表与 tier 概念。理由见 SKILL.md「心智模型」。
