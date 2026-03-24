---
name: feishu-contract-audit
description: 专门用于“Google Cloud 价格补充协议 (Pricing Addendum)”的自动化审核与飞书表格录入。当用户提供 Google Cloud 合同 PDF 并要求核查、录入飞书表格（如 Commit deal 核查 Checklist）时使用。支持自动识别折扣、承诺金额、里程碑返利、子账号等核心条款，并能处理飞书表格的权限授权、错行修正及✅结论标注。
---

# Google Cloud 合同审核与录入规范

本 Skill 用于标准化 Google Cloud 价格补充协议的审核流程，确保数据准确录入飞书电子表格。

## 核心核查项定义

在审核 PDF 时，必须准确提取以下关键信息：

1.  **Program Discount**: 通常在第 1.6 章节，确认为 12% 或其他比例。
2.  **Minimum Commitment**: 通常在第 2.0 章节的表格中，确认金额（如 $1,000,000）和周期（通常 12 个月）。
3.  **Milestone Credits**: 通常在第 3.1 章节，确认触发金额（Spend Milestone）和返利金额（Credit Amount）。
4.  **Shortfall (True-Up)**: 确认第 2.1 章节是否包含未达标需补足差额的条款。
5.  **计入范围**: 确认 Marketplace (2.3) 和 TSS 技术支持 (1.4) 是否计入承诺。
6.  **子账号 (Subaccounts)**: 提取第 4.0 章节列出的所有 Billing Account ID。

## 飞书表格录入操作指南

### 1. 权限与授权流 (SOP)
如果遇到权限报错（如 `awaiting_app_authorization`），必须按照以下顺序操作：
- **引导授权**：直接告知用户点击授权卡片。
- **一次性原则**：如果用户询问，告知将一次性申请所有权限。
- **强制刷新**：若多次授权无效，使用 `feishu_oauth(action='revoke')` 强制撤销后让用户重新授权以刷新 Token。

### 2. 录入规范
- **BMO核查列**：录入数值（如折扣率 0.12，金额 1000000）。
- **备注列 (F列)**：
    - **必须**在结论前添加 `✅` 标志。
    - **必须**引用合同具体章节（如：✅ 12.00% Program Discount，见 1.6 节）。
    - 针对缺失项，标注“✅ 合同未体现...”。

### 3. 防错行逻辑
- 在执行 `write` 操作前，必须先调用 `read` 读取 A 栏和 B 栏，通过匹配“核查项”名称（如 "Program Discount"）来确定目标行号，严禁硬编码行号。

## 异常处理
- **LLM Error (Region Block)**：如果遇到区域拦截错误，说明当前模型受限，应自动切换至备用模型（如 Claude/OpenAI）重试，并向用户说明情况。
- **数据溯源**：若 PDF 模糊或关键页缺失，必须标注【假设】并询问用户，不得幻觉数据。
