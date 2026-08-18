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

所有 Agent 与开发者提交代码时**必须统一使用中文注释**，并严格遵循**原子化分类提交**原则（严禁跨技能混杂提交，严禁基建与业务技能混杂提交）：

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

任何对技能库的增、删、改操作，**必须严格遵循 [AGENTS.md](./AGENTS.md) 规定的三部曲 SOP**：
1. **新增技能**：运行 `scaffold` → 填充逻辑与测试 → `security` & `lint` 扫描 → `sync` 注册 → 规范 Git 提交。
2. **修改技能**：评估范围 → 更新 `SKILL.md` (SemVer 升级) → 业务修改与回归测试 → `sync` 同步 → 规范 Git 提交。
3. **废弃技能**：更新状态为 `已废弃` → 在 `SKILLS.md` 标注替代建议 → `sync` 刷新索引 → 规范 Git 提交。

## 维护者

- Webeye CSBU Team

