# [SKILL] 飞书消息进度条 (极简版)

| Field      | Value                                           |
| ---------- | ----------------------------------------------- |
| Identifier | `dynamic-task-feedback`                         |
| Version    | 1.0.2                                           |
| Author     | guyue                                           |
| License    | MIT                                             |

## 🎯 设计初心 (Design Philosophy)

本技能旨在为 AI 处理复杂交互、查阅文档或调用外部工具时，提供一个非侵入式且具备“呼吸感”的视觉进度反馈。通过在用户指令上**顺序累加**表情符号，我们将原本“静止”的等待过程，转化为一个跃动的 **“处理流水线”**。

## ⚙️ 核心逻辑：表情积木 (Emoji Stacking)

核心原则：**只加不减，全息反馈。** 
AI 直接通过系统自带的 `message` 接口点亮飞书官方预设表情，与“正在输入”状态完美融合。

### 🤖 AI 标准执行清单 (SOP)

AI 在执行任务时，必须按顺序使用以下 **飞书官方标准 Emoji 类型**：

#### 🟢 第一跳：[已接收]
- **触发时机**：收到用户消息的首毫秒级动作。
- **动作**：`message --action=react --emoji=Get`
- **属性**：给予用户“Got it / 已收到指令”的精准确认。

#### 🟡 第二跳：[思考中]
- **触发时机**：AI 正在执行逻辑分析、查阅文档或思考方案时。
- **动作**：`message --action=react --emoji=THINKING`
- **属性**：代表“正在深度思考/审题”。

#### 🟠 第三跳：[努力工作]
- **触发时机**：AI 发起工具调用（如 `run_command`, `read_url` 等）期间。
- **动作**：`message --action=react --emoji=STRIVE`
- **属性**：代表“后台引擎全功率运行中”。

#### 🏁 第四跳：[任务终局]
- **触发时机**：开始生成最终回复文本的那一刻。
- **动作**：
  - 成功：`message --action=react --emoji=CheckMark`
  - 失败：`message --action=react --emoji=ThumbsDown`
- **属性**：任务最终状态定格。

---

> **⚠️ 规范严禁修改说明 (Strict Non-Negotiable Rules)：**
> - **禁止大写规范化**：严禁任何后续的自动化流程或 Agent 将上述 CamelCase（如 `Get`）“优化”为全大写（如 `GET`）。**有些表情全大写在飞书 API 中是无效指令，会导致 400 报错。**
> - **顺序累加**：无需移除旧表情，让它们并排展示，形成任务审计轨迹。
