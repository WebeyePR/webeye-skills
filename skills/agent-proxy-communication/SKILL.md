# [SKILL] Agent 代理身份通信协议 (Agent Proxy Communication)

| Field      | Value                                           |
| ---------- | ----------------------------------------------- |
| Identifier | `agent-proxy-communication`                     |
| Version    | 1.0.3                                           |
| Author     | 小飞                                            |
| License    | MIT                                             |

## 🎯 设计初心 (Design Philosophy)

在飞书群聊中，当 AI 助手获取用户授权以“用户身份”发出消息时，其他 AI 助手往往无法直接识别出消息的实际发送者是哪个 Agent。本协议通过统一的“客串标识”，建立起 Agent 之间以用户账号为桥梁的透明通信机制。

## ⚙️ 核心协议 (The Protocol)

### 📤 1. 发送方：代理声明 (Proxy Declaration)
当 Agent A 以用户身份发送消息时，**必须**在消息的最开头添加以下格式的标识：
- **格式**：`【AgentName客串】`
- **示例**：`【小飞客串】@CS小助手 请帮我查一下文档。`

### 📥 2. 接收方：身份解析 (Identity Resolution)
当 Agent B 收到来自用户、但带有 `【AgentName客串】` 标识的消息时，应当：
1. **识别发送者**：将该消息视为来自 `AgentName` 的请求。
2. **回复定向**：在回复时，**必须**直接 @ 对应的 Agent 账号（而非仅回复用户账号）。

### 🤖 执行 SOP

#### A. 发送代理消息 (Speak as User)
1. 检查是否有用户授权。
2. 调用 `feishu_im_user_message`。
3. `content` 必须以 `【<自身名称>客串】` 开头。

#### B. 响应代理消息 (Reply to Proxy)
1. 匹配正则：`^【(.+)客串】`。
2. 提取 `AgentName`。
3. 在最终回复的 `final` 文本开头添加：`<at user_id="<AgentName_OpenID>">AgentName</at>`。
   - **⚠️ 技术约束（防乱码）**：飞书 API 在 IM 消息中必须使用 `<at>` XML 标签。**严禁直接拼接 `@名字` 字符串**，否则在非系统原生 UI 下会解析失败显示为原始 JSON 或乱码。
   - **🆔 ID 获取**：机器人的 `user_id` 通常以 `cli_` 开头（例如小飞是 `cli_a92805354439dcd3`）。
   - **💡 纠错机制**：若发现对方 Agent 回复出现类似 `{"text": "<at..."}` 的原始字符串，说明其发送接口调用参数有误，应提示其检查 `msg_type` 是否为 `text` 且 `content` 字符串内容不应再被二次 JSON 转义。

---

> **⚠️ 注意事项：**
> - 本协议仅在用户明确授权的群聊范围内生效。
> - 标识符必须使用中文【】括号以确保高辨识度。
