# Skills 清单

本文档旨在全面整理 Webeye CSBU 内外部的 AI 技能，并对其进行初步评估，为未来的技能规划、开发和复用提供参考。

## 内部技能 (Webeye 自研)

| 技能名称 | 状态 | 工程化复杂度 | 可复用性 | 核心作用 |
| :--- | :--- | :--- | :--- | :--- |
| [feishu-crm-copilot](https://github.com/WebeyePR/webeye-skills/tree/main/skills/feishu-crm-copilot) | ✅ 生产可用 | 中 | 中 | 将销售沟通记录转化为结构化 CRM 数据 |
| [feishu-knowledge-base](https://github.com/WebeyePR/webeye-skills/tree/main/skills/feishu-knowledge-base) | ✅ 生产可用 | 中 | 中 | 连接并检索飞书知识库，提供智能问答 |
| [gcp-support-ticket](https://github.com/WebeyePR/webeye-skills/tree/main/skills/gcp-support-ticket) | ✅ 生产可用 | 低 | 高 | 协助用户起草符合 GCP 最佳实践的技术支持工单 |
| [feishu-contract-audit](https://github.com/WebeyePR/webeye-skills/tree/main/skills/feishu-contract-audit) | ✅ 生产可用 | 中 | 低 | 自动提取 GCP 合同折扣、金额等条款并录入飞书。 |
| [content-broadcast](https://github.com/WebeyePR/webeye-skills/tree/main/skills/content-broadcast) | ✅ 生产可用 | 中 | 中 | 多渠道内容解析与自动化广播定向分发工作流。 |
| [billing-config-error](https://github.com/WebeyePR/webeye-skills/tree/main/skills/billing-config-error) | ✅ 生产可用 | 中 | 低 | 自动处理账单重复配置报错，追踪合同负责人及系统状态。 |
| [feishu-crm-automation](https://github.com/WebeyePR/webeye-skills/tree/main/skills/feishu-crm-automation) | 🚧 开发中 | 高 | 低 | 自动化创建飞书 aPaaS 工单 |
| [feishu-dynamic-task-feedback](https://github.com/WebeyePR/webeye-skills/tree/main/skills/feishu-dynamic-task-feedback) | 🔬 调试中 | 低 | 高 | 在长时间任务中提供实时的视觉进度反馈 |
| [Agent Proxy Communication](https://github.com/WebeyePR/webeye-skills/tree/main/skills/agent-proxy-communication) | 🔬 调试中 | 中 | 高 | **基础协议**：解决跨 Agent 身份识别与文本强提醒问题。 |


## 外部技能 (第三方)
以下为常用的第三方技能，提高Agent的效率。

| 技能名称 | 状态 | 工程化复杂度 | 可复用性 | 核心作用 |
| :--- | :--- | :--- | :--- | :--- |
| [**security-audit** (安全审计)](https://clawhub.ai/skills?q=security-audit) | ✅ 生产可用 | 中 | 高 | **必装**：每当装完一批 Skill 后进行安全检查，防止代码红线或权限风险。 |
| [**skill-creator** (技能创建者)](https://clawhub.ai/skills?q=skill-creator) | ✅ 生产可用 | 中 | 高 | **必装**：一键生成符合规范的技能模板，标准化技能开发流程。 |
| [**proactive-agent** (主动代理)](https://clawhub.ai/skills?q=proactive-agent) | ✅ 生产可用 | 高 | 中 | 让 Agent 具有自主规划能力，能提前预判需求并拆解多步任务。 |
| [**self-improving-agent** (自我进化)](https://clawhub.ai/skills?q=self-improving-agent) | ✅ 生产可用 | 极高 | 低 | 自动记录并学习用户纠正，使 Agent 的响应策略随使用次数自我优化。 |
| [**baoyu-slide-deck** (幻灯片生成)](https://clawhub.ai/skills?q=baoyu-slide-deck) | ✅ 生产可用 | 中 | 中 | 根据内容自动创建大纲并生成系列 PPT 图片展示。 |
| [**ga4-analytics** (流量分析)](https://clawhub.ai/skills?q=ga4-analytics) | ✅ 生产可用 | 中 | 中 | 集成 GA4 与 Search Console，分析网站流量与 SEO 表现。 |
| [**competitive-analysis** (竞品分析)](https://clawhub.ai/skills?q=competitive-analysis) | ✅ 生产可用 | 中 | 中 | 深度发现市场缺口、分析竞争对手策略并进行业务对标。 |
| [**baoyu-image-gen** (多引擎绘图)](https://clawhub.ai/skills?q=baoyu-image-gen) | ✅ 生产可用 | 中 | 中 | 支持多种主流图像生成模型（OpenAI/Google/Replicate）。 |
| [**google-trends** (趋势监控)](https://clawhub.ai/skills?q=google-trends) | ✅ 生产可用 | 中 | 中 | 获取每日热门搜索并对比关键词热度，辅助内容策划。 |
| [**social-content** (社媒创作)](https://clawhub.ai/skills?q=social-content) | ✅ 生产可用 | 中 | 中 | 针对多社交平台（LinkedIn/TikTok 等）提供调度与发布策略。 |
| [**text-to-pdf** (格式转换)](https://clawhub.ai/skills?q=text-to-pdf) | ✅ 生产可用 | 低 | 高 | 文本与图像快速转为标准的 PDF 文档。 |
| [**reddit-insights** (情感分析)](https://clawhub.ai/skills?q=reddit-insights) | ✅ 生产可用 | 中 | 中 | 基于语义搜索分析 Reddit 用户痛点、趋势及真实反馈。 |


## 评估维度

- **状态**: 技能的成熟度，分为：
    - ✅ **生产可用**: 已经过充分测试，可稳定用于生产环境。
    - 🚧 **开发中**: 正在积极开发和迭代，功能尚不稳定。
    - 🔬 **验证/研究中**: 处于概念验证或研究阶段，可行性待定。
- **工程化复杂度**: 集成和维护该技能所需的技术投入，分为：
    - **高**: 需要大量开发工作、复杂的依赖管理或专门的基础设施。
    - **中**: 需要一定的配置和集成工作，但有清晰的文档和接口。
    - **低**: 开箱即用或只需少量配置即可集成。
- **可复用性**: 技能在不同项目或场景中复用的潜力，分为：
    - **高**: 技能逻辑通用，与特定业务场景解耦，易于迁移。
    - **中**: 技能与特定平台（如飞书）或领域绑定，但其核心模式可借鉴。
    - **低**: 技能为特定业务场景高度定制，难以复用。