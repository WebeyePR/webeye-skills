---
name: feishu-dynamic-task-feedback
description: 装饰器级交互技能。通过顺序累加飞书消息表情实现“独占式”进度追踪，结合“精英-稳健”双轨逻辑，确保 100% 的交互成功率。
alwaysActive: true
version: 1.0.4
author: guyue
license: MIT
---

# [SKILL] 飞书消息进度条

## 🎯 核心意志
本技能严禁任何“发散思维”。AI 必须像冷酷的工业执行单元一样，严格按照下表的官方映射值执行指令，并在 API 报错时自动触发“稳健兜底”机制。

## ⚔️ 表情积木白名单 (The Absolute Whitelist)

| 阶段 | **首选指令 (Elite Tier)** | **稳健兜底 (Robust Tier)** | 说明 |
| :--- | :--- | :--- | :--- |
| **1. 启动** | `Get` | `WITTY` | 收到任务，点头确认。 |
| **2. 思考** | `THINKING` | `THUMBSUP` | 逻辑解构/审题中。 |
| **3. 执行** | `STRIVE` | `MUSCLE` | 核心引擎全功率运行。 |
| **4. 成功** | `DONE` | `THANKS` | 任务圆满封包。 |
| **N/A. 失败**| `SOB` | `THUMBSDOWN` | 任务受阻/异常。 |

## 🚫 禁绝幻觉：非法表情名单 (Blacklist)

**严禁** 在任何环节触发以下表情：
- `STARE`, `EYES`, `WATCH`, `EYESCLOSED` (严重触发 400 风险)
- `CheckMark`, `WARNING`, `ERROR` (极其不稳)
- `PENCIL`, `GET`, `Thinking` (非标准拼写或全部大写导致的失效)

## 🛡️ AI 错误处理 SOP (Error Handling)

**AI 必须实时关注 `message` 工具的工具输出：**

1.  **先行试探**：默认首选 `Elite Tier` 进行 `react` 操作。
2.  **错误感知**：若收到类似 `om_xxx ... failed` 或 `Internal Error (400)` 的回复。
3.  **秒级止损**：**严禁重试首选值**。必须在这一步紧接着尝试对应的 `Robust Tier` 指令。
4.  **无缝衔接**：即便表情点亮失败，也不允许报怨，直接进入核心业务生成环节。

---

> **⚠️ API 严格规范：**
> - **拼写即生命**：静止修改表情指令大小写，如 `Get` 必须首字母大写；`STRIVE`、`DONE`、`MUSCLE` 必须全大写。
> - **只加不减**：无需移除操作，表情顺序排布即为进度条。
