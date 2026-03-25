---
name: feishu-dynamic-task-feedback
description: 装饰器级交互技能。通过顺序累加飞书消息表情实现“全向”进度追踪，强制执行以确保 100% 的触发率和准确性。
alwaysActive: true
version: 1.0.5
author: guyue
license: MIT
---

# [SKILL] 飞书消息进度条

## 🎯 核心意志
本技能的优先级高于 AI 的自主判断。AI 必须像冷酷的工业执行单元一样，对每一次用户输入（无视复杂度）进行表情反馈，并严格遵循串行时序逻辑。

## 🛡️ 时序安全与强制触发协议 (Total Enforcement)

AI 在执行交互时，必须严格遵守以下物理级约束：

1. **零豁免触发 (Universal Trigger)**：
   严禁 AI 私自判断指令复杂程度。无论是查询版本、系统自检、还是元数据操作，**第一步动作永远是点亮 `Get` 表情**。本条约不存在任何“非复杂任务”豁免。

2. **强制串行等待 (Anti-Jitter Lock)**：
   为彻底解决表情展示乱序的“抢跑”瑕疵，**严禁在一个 tool_call 块中堆叠多个 message 指令**。
   - **逻辑单摆**：必须执行第 N 跳 -> **等待工具输出确认为 DONE** -> 方可发起第 N+1 跳。
   - **反馈节奏**：以此物理串行方式，确保表情在消息下方的排列顺序绝对与进度同步。

---

## ⚔️ 表情积木白名单 (The Absolute Whitelist)

| 阶段 | **首选指令 (Elite Tier)** | **稳健兜底 (Robust Tier)** | 说明 |
| :--- | :--- | :--- | :--- |
| **1. 启动** | `Get` | `WITTY` | 收到任务，点头确认。**[必须优先触发]** |
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
> - **拼写即生命**：`Get` 必须首字母大写；其余 `THINKING`、`STRIVE`、`DONE`、`SOB` 必须全大写。
> - **只加不减**：无需移除操作，表情顺序排布即为进度条。
