# Webeye AI 技能清单

本文档是 Webeye CSBU 部门 AI 技能的索引。它不仅涵盖了目前已上线的生产级技能，还包含精选的第三方资源，并对其进行初步评估，为未来的技能规划、开发和复用提供参考。

---

## 内部技能 (Webeye 自研)

| 技能名称 | 状态 | 工程化复杂度 | 可复用性 | 核心作用 |
| :--- | :--- | :--- | :--- | :--- |
| [example-skill](https://github.com/WebeyePR/webeye-skills/tree/main/skills/example-skill) | ✅ 生产可用 | 低 | 高 | 技能示例，演示技能标准结构与基础文本摘要功能。 |
| [feishu-crm-copilot](https://github.com/WebeyePR/webeye-skills/tree/main/skills/feishu-crm-copilot) | ✅ 生产可用 | 中 | 中 | 将销售沟通原始素材（文字/截图/会议记录等）转化为 CRM 结构化数据自动提交。 |
| [feishu-knowledge-base](https://github.com/WebeyePR/webeye-skills/tree/main/skills/feishu-knowledge-base) | ✅ 生产可用 | 中 | 高 | 基于飞书知识库的检索增强生成 (RAG) 问答。 |
| [feishu-dynamic-task-feedback](https://github.com/WebeyePR/webeye-skills/tree/main/skills/feishu-dynamic-task-feedback) | 🚧 调试中 | 低 | 高 | 通过动态表情提供实时视觉进度反馈。 |
| [gcp-support-ticket](https://github.com/WebeyePR/webeye-skills/tree/main/skills/gcp-support-ticket) | ✅ 生产可用 | 低 | 中 | 协助起草并优化符合 GCP 最佳实践的技术支持工单。 |
| [feishu-contract-audit](https://github.com/WebeyePR/webeye-skills/tree/main/skills/feishu-contract-audit) | ✅ 生产可用 | 中 | 低 | 自动解析 GCP 合同条款并录入飞书 Checklist。 |
| [content-broadcast](https://github.com/WebeyePR/webeye-skills/tree/main/skills/content-broadcast) | ✅ 生产可用 | 中 | 中 | 内容多渠道解析与自动化定向广播分发。 |
| [billing-config-error](https://github.com/WebeyePR/webeye-skills/tree/main/skills/billing-config-error) | ✅ 生产可用 | 中 | 低 | 账单系统重复配置纠错与故障链路追踪。 |
| [Agent Proxy Communication](https://github.com/WebeyePR/webeye-skills/tree/main/skills/agent-proxy-communication) | 🚧 调试中 | 中 | 高 | 解决跨 Agent 协作时的身份识别问题。 |
| [feishu-crm-automation](https://github.com/WebeyePR/webeye-skills/tree/main/skills/feishu-crm-automation) | 🚧 开发中 | 高 | 低 | 自动化执行飞书 aPaaS 工单的静默创建与状态轮询。 |
| [wewe-rss-add-feed](https://github.com/WebeyePR/webeye-skills/tree/main/skills/wewe-rss-add-feed) | ✅ 生产可用 | 中 | 低 | 自动化添加与查询微信公众号到 `wewe-rss` 监控库，支持智能分流。 |

## 外部常用技能 (第三方)

| 技能名称 | 状态 | 工程化复杂度 | 可复用性 | 核心作用 |
| :--- | :--- | :--- | :--- | :--- |
| [**security-audit** (安全审计)](https://clawhub.ai/skills?q=security-audit) | ✅ 生产可用 | 中 | 高 | **必装**：新加技能后的安全扫描，防止权限越界。 |
| [**skill-creator** (技能创建者)](https://clawhub.ai/skills?q=skill-creator) | ✅ 生产可用 | 中 | 高 | **必装**：一键生成符合本项目规范的技能脚手架。 |
| [**proactive-agent** (主动代理)](https://clawhub.ai/skills?q=proactive-agent) | ✅ 生产可用 | 高 | 中 | 让 Agent 具备预判需求并主动规划多步任务的能力。 |
| [**baoyu-slide-deck** (幻灯片库)](https://clawhub.ai/skills?q=baoyu-slide-deck) | ✅ 生产可用 | 中 | 中 | 根据大纲自动生成系列 PPT 幻灯片预览图。 |
| [**baoyu-image-gen** (绘图专家)](https://clawhub.ai/skills?q=baoyu-image-gen) | ✅ 生产可用 | 中 | 中 | 集成 DALL-E 3、Imagen 等多引擎的高性能绘图工具。 |
| [**ga4-analytics** (流量分析)](https://clawhub.ai/skills?q=ga4-analytics) | ✅ 生产可用 | 中 | 中 | 深度链接 GA4 与搜索中心，提供自动化流量洞察。 |
| [**google-trends** (趋势监控)](https://clawhub.ai/skills?q=google-trends) | ✅ 生产可用 | 中 | 中 | 自动监控热门关键词及热度对比。 |
| [**text-to-pdf** (格式转换)](https://clawhub.ai/skills?q=text-to-pdf) | ✅ 生产可用 | 低 | 高 | 支持文本与素材快速转成标准的 PDF 文档。 |
| [**reddit-insights** (情感挖掘)](https://clawhub.ai/skills?q=reddit-insights) | ✅ 生产可用 | 中 | 中 | 基于语义搜索对 Reddit 社区进行痛点与趋势分析。 |

---

## 评估标准

我们从三个维度对每项技能进行工程化评估：

- **状态**: 
    - ✅ **生产可用**: 已通过测试，稳定运行。
    - 🚧 **开发中 / 调试中**: 逻辑待完善或正在对接 API。
- **工程化复杂度**: 
    - **高**: 需要复杂的 Infra 依赖（如向量库）或重型业务编排。
    - **中**: 需要标准 API 对接与中等规模的数据处理。
    - **低**: 基于 Prompt 或轻量级脚本，开箱即用。
- **可复用性**: 
    - **高**: 通用模式，与特定业务解耦。
    - **中**: 平台绑定（如飞书）但逻辑可平移。
    - **低**: 为特定业务场景高度定制。

---
*注：本文件由维护者手动更新，用于全景能力展示。更多技术细节请参考 [README.md](https://github.com/WebeyePR/webeye-skills/tree/main/README.md)*
