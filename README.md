# Webeye CSBU AI Skills

这是一个通用的技能库，专为 Webeye CSBU 部门设计，用于存放和管理各类 AI 技能、自动化脚本及工具。

## 项目宗旨

- **通用性**：提供跨项目可复用的 AI 技能。
- **标准化**：统一技能的开发、部署和调用标准。
- **最佳实践**：遵循代码规范，包含详尽的文档和注释。

## 安装与使用

本库支持通过多种方式安装单个技能（以 `example-skill` 为例）：

### 1. 使用 npm 安装
如果你希望将技能作为依赖管理：
```bash
npm install git+https://github.com/webeye/webeye-skills.git#skills/example-skill
```

### 2. 使用 `degit` 下载
如果你只需要代码副本：
```bash
npx degit webeye/webeye-skills/skills/example-skill my-skill
```

## 核心内容

- **[🤖 Agent 协同与开发规范 (AGENTS.md)](./AGENTS.md)**：专供 AI 智能体与开发者遵循的增删改流程（SOP）、红线守则与审计检查单。
- **[📋 查看所有技能索引 (SKILLS.md)](./SKILLS.md)**：包含本库目前支持的所有 AI 技能详情、状态及复杂度。
- **[📦 技能机器注册表 (skills_manifest.json)](./skills_manifest.json)**：自动同步生成的技能结构化元数据。

## 🌟 部门核心业务技能一览

本仓库收录了服务于 WebEye CSBU 业务部门的各类专属 AI 技能，涵盖知识沉淀、CRM 提效、工单审核与自动化：

| 技能名称 | 核心业务作用与归属上下文 | 文档入口 |
| :--- | :--- | :--- |
| **`webeye-kb-organizer`** | **WebEye 知识库文档整理器**：将大家发在群聊、本地或个人的零散方案与纪要，自动按部门标准排版，归类入库到 **WebEye CSBU 部门的飞书「[WebEye 云服务知识库](https://webeye.feishu.cn/wiki/HdNcw8qJkixrPKkRrIGc0OQEnDh)」** 对应目录（入门指南/解决方案/最佳实践/MSP清单等）。 | [说明文档](./skills/webeye-kb-organizer/SKILL.md) |
| **`crm-followup-writer`** | **业务一部 CRM 跟进自动同步**：将口述或粘贴的客户跟进进展，自动解析、校验并安全写入 WebEye 业务一部共享 CRM（飞书多维表格）客户跟进记录表。 | [说明文档](./skills/crm-followup-writer/SKILL.md) |
| **`feishu-crm-copilot`** | **飞书 CRM 提效助手**：将销售沟通原始素材（文字/截图/会议记录）转化为 CRM 结构化数据并自动提交。 | [说明文档](./skills/feishu-crm-copilot/SKILL.md) |
| **`feishu-knowledge-base`** | **飞书指定知识库 RAG 问答**：基于 WebEye 部门飞书知识库的检索增强生成与精准智能问答。 | [说明文档](./skills/feishu-knowledge-base/SKILL.md) |
| **`gcp-support-ticket`** | **GCP 工单助手**：协助团队起草并优化符合 Google Cloud 最佳实践的技术支持工单。 | [说明文档](./skills/gcp-support-ticket/SKILL.md) |
| **`feishu-contract-audit`** | **飞书合同审核**：自动解析 GCP 商务合同条款并录入飞书 Checklist，提升法务与商务审核效率。 | [说明文档](./skills/feishu-contract-audit/SKILL.md) |
| **`ai-daily-digest`** | **AI 每日资讯**：每日统一采集 AI 新闻简报与产品发布，跨源去重排序并生成含趋势分析的日报。 | [说明文档](./skills/ai-daily-digest/SKILL.md) |

> 💡 完整技能列表与第三方推荐技能请参考 **[SKILLS.md (全量技能清单)](./SKILLS.md)**。

## 目录结构

```text
.
├── AGENTS.md             # 🤖 AI Agent 协同守则与增删改标准化 SOP (核心规范)
├── SKILLS.md             # 📋 技能索引清单 (人工阅读总览)
├── skills_manifest.json  # 📦 自动化生成的结构化元数据注册表
├── skills/               # 🎯 AI 技能存放区 (每一个子文件夹包含独立的 SKILL.md)
├── scripts/              # 🛠️ 技能管理与自动化工具 (manage_skills.py)
├── lib/                  # 📚 共享公共逻辑 (feishu-sdk 等)
├── tests/                # 🧪 管理脚本单元测试
├── docs/                 # 📖 项目全局文档
└── README.md             # 🏠 项目总览入口
```

## 🛠️ 技能运维与统计工具 (`scripts/manage_skills.py`)

仓库提供了纯 Python 标准库实现的运维脚本（零第三方依赖），可供 Agent 和开发者直接运行：

```bash
# 1. 深度安全与防泄密审计 (扫描密钥/私钥/凭据/绝对路径，并输出修复建议)
python3 scripts/manage_skills.py security

# 2. 技能合规性与全量扫描 (Lint)
python3 scripts/manage_skills.py lint

# 3. 全景统计报表 (基础指标、状态分布、复杂度与复用度分布)
python3 scripts/manage_skills.py stats
python3 scripts/manage_skills.py stats --json

# 4. 同步元数据至 skills_manifest.json 并核对 SKILLS.md
python3 scripts/manage_skills.py sync

# 5. 一键创建标准化技能脚手架
python3 scripts/manage_skills.py scaffold <skill-name> --desc "技能简述"
```

## 📝 Git 提交规范 (Git Commit Convention)

- 🚨 **严禁 Agent 自动提交**：所有的 `git commit` / `git push` 操作**必须由用户主动确认、明确授权或下达提交指令后方可执行**。
- 所有提交**必须统一使用中文注释**，并严格遵循**原子化分类提交**原则（严禁跨技能混杂提交，严禁基建与业务技能混杂提交）：

```text
xxx(xxx): 中文xxx
```

**分类 Scope 示例**：
- **单技能变动**：`feat(skills/feishu-voice): 新增飞书语音速记技能`、`fix(skills/feishu-crm): 修复联系人同步逻辑`
- **安全与脱敏**：`sec(skills/feishu-crm): 修复绝对路径引用并完成脱敏`
- **元数据同步**：`chore(manifest): 同步全量技能元数据注册表`
- **运维管理工具**：`refactor(scripts): 优化管理脚本扫描逻辑`
- **规范与文档**：`docs(agents): 完善 AGENTS.md 规范说明`
- **单元测试套件**：`test(manage_skills): 补充安全扫描测试用例`

## 贡献与治理流程

任何对技能库的增、删、改操作，**必须严格遵循 [AGENTS.md](./AGENTS.md) 规定的治理规范与安全红线**：
1. **新增技能**：运行 `scaffold` → 填充逻辑与测试 → `security` & `lint` 扫描（**若有安全告警立即停下等待用户确认**）→ `sync` 注册 → 汇报变更并等待用户确认提交。
2. **修改技能**：评估范围 → 更新 `SKILL.md` (SemVer 升级) → 业务修改与回归测试 → `sync` 同步 → 汇报变更并等待用户确认提交。
3. **废弃技能**：更新状态为 `已废弃` → 在 `SKILLS.md` 标注替代建议 → `sync` 刷新索引 → 汇报变更并等待用户确认提交。

## 维护者

- Webeye CSBU Team

