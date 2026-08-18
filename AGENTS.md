# 🤖 Webeye CSBU AI Skills Agent Instruction Manual (AGENTS.md)

> **适用对象**：所有参与维护本仓库的 AI Agent（Claude, Gemini, Antigravity, Cursor, Windsurf 等）与工程师。  
> **核心使命**：立足 Webeye 多人协作与多技能共存的实际业务场景，规范技能资产的生命周期、防泄密安全合规与 Agent 自动化闭环。

---

## 🗺️ 1. 仓库结构与协作定位 (Repository Map & Collaboration)

| 路径 | 说明 | 协作与运维规则 |
| :--- | :--- | :--- |
| `skills/` | 技能主目录 (`skills/<skill-name>/`) | 各技能独立自治存放，**严禁跨技能越权或污染修改**。 |
| `scripts/manage_skills.py` | 统一运维管理工具 (零外部依赖) | 提供 `security`、`lint`、`stats`、`sync`、`scaffold` 指令。 |
| `skills_manifest.json` | 机器可读注册表 | 由 `manage_skills.py sync` 自动提取生成，禁止手动编辑。 |
| `SKILLS.md` | 人类可读技能清单 | 由同步脚本统一对齐维护。 |
| `tests/` | 自动化测试套件 | 运行 `python3 -m unittest discover -s tests` 进行验证。 |
| `README.md` | 项目全局总览入口 | 仓库主页与安装指南。 |

---

## 📋 2. 技能组织与标准入口 (Skill Structure & Entrypoints)

每个技能位于 `skills/<skill-name>/` 下，以 **`SKILL.md` 作为 Agent 读取与执行的标准入口**：

```text
skills/<skill-name>/
├── SKILL.md                 # [必须] Agent 核心定义文件（YAML Frontmatter + SOP 正文）
├── .env.example             # [推荐] 环境变量模板（用于提供配置说明，严禁存真实密钥）
├── README.md                # [可选] 人工阅读说明与快速上手文档
├── scripts/                 # [可选] 可执行脚本目录（Python/Node.js/Bash）
├── references/              # [可选] 业务规范、接口文档与参考资料
└── tests/                   # [可选] 单元测试与验证脚本
```

### 2.1 `SKILL.md` 标准元数据 (Frontmatter)
```yaml
---
name: my-skill-name             # 必填，全小写中划线 (kebab-case)，须与目录名保持一致
description: >                  # 必填，简明中文描述，说明技能核心作用与触发时机
  一句话描述技能的作用与触发条件。
version: 1.0.0                  # 必填，语义化版本号 (SemVer)
author: Webeye CSBU Team        # 必填，作者或团队名称
status: 生产可用                 # 选填：生产可用 | 调试中 | 开发中 | 已废弃 (默认：生产可用)
complexity: 中                  # 选填：高 | 中 | 低 (默认：中)
reusability: 高                 # 选填：高 | 中 | 低 (默认：高)
---
```

> [!NOTE]
> **关于辅助文档与资源**：`SKILL.md` 是供 AI Agent 理解技能的统一入口。作者可根据实际需要自由添加面向人类阅读的 `README.md`、开发文档或配套资源文件。

---

## 🚀 3. Agent 标准作业流程 (Step-by-Step SOP)

### Task 1: 新增技能 (Create New Skill)
1. **脚手架生成**：运行 `python3 scripts/manage_skills.py scaffold <skill-name> --desc "<简述>"`。
2. **编写实现**：完善 `skills/<skill-name>/SKILL.md` 正文（触发条件、业务流程、边界限制），并按需补充脚本、文档与测试。
3. **安全与合规扫描**：运行 `python3 scripts/manage_skills.py lint` 确保合规，运行 `python3 scripts/manage_skills.py security` 进行安全审计。**若发现安全风险必须立即停下向用户告警并等待确认**。
4. **同步元数据注册表**：运行 `python3 scripts/manage_skills.py sync` 刷新 `skills_manifest.json` 与 `SKILLS.md`。
5. **汇报变更并等待确认**：向用户展示变更明细与建议的 Git 提交信息（如 `feat(skills/<skill-name>): 新增 <skill-name> 技能`），**严禁自动提交，必须等待用户显式确认或下达提交指令**。

### Task 2: 修改 / 升级技能 (Modify Skill)
1. **限定修改范围**：仅修改目标 `skills/<target-skill>/`，并在 `SKILL.md` 中递增 `version`。
2. **执行扫描与验证**：运行 `python3 scripts/manage_skills.py lint --skill <target-skill>` 并执行业务自测。**若发现安全风险必须立即停下向用户告警并等待确认**。
3. **同步元数据**：运行 `python3 scripts/manage_skills.py sync`。
4. **汇报变更并等待确认**：向用户展示变更明细与建议的 Git 提交信息（如 `fix(skills/<target-skill>): 修复 xxx 逻辑至 vX.Y.Z`），**等待用户显式确认后方可提交**。

### Task 3: 深度安全审计 (Security Audit)
1. 运行 `python3 scripts/manage_skills.py security` 扫描 API Key、私钥、Token 与本地绝对路径。
2. **安全风险拦截与确认**：若发现任何安全或信息泄露告警，**必须立即中断当前流程，向用户汇报泄露位置、风险等级与脱敏片段，停下等待用户确认并给出处理决策**。

### Task 4: 废弃技能 (Deprecate Skill)
1. 将 `SKILL.md` 中 `status` 改为 `已废弃`，并在描述中补充推荐替代方案。
2. 运行 `python3 scripts/manage_skills.py sync`。
3. **汇报变更并等待确认**：向用户展示变更建议（如 `chore(skills): 废弃 <target-skill> 技能`），**等待用户显式确认后方可提交**。

---

## 🛑 4. 核心安全红线与治理原则 (Guardrails & Governance)

1. **🔒 范围隔离原则 (Scope Isolation)**
   - 聚焦于目标技能的开发与维护，**严禁跨技能随意篡改其他成员的技能文件**。
2. **🚫 严禁擅自篡改技能内容 (No Unsolicited Modification)**
   - 导入、收录或同步技能时，必须完整保留原作者提交的代码、文档与配置，**严禁未经显式允许擅自修改技能正文、改写代码逻辑或强加多余文件**。只有在用户提出明确修改指令时方可调整。
3. **🛡️ 零泄密与安全拦截原则 (Zero-Leakage & Safety Interruption)**
   - 严禁在代码、文档或配置中提交 API Key（`sk-`、`AIza` 等）、Token、Secret 或私钥。
   - 敏感配置统一通过环境变量读取，并提供 `.env.example` 模板。
   - 🚨 **安全发现必须强制停下确认**：凡在安全扫描中发现敏感信息泄露、密钥暴露或高危路径，**Agent 必须立即中断后续操作，向用户发出警报并展示修复建议，绝对禁止擅自跳过或盲目提交，必须停下等待用户确认决策**。
4. **🚫 环境脱敏 (De-hardcoding)**
   - 避免硬编码开发者本机绝对路径（如 `/Users/...`）。推荐使用相对路径或动态环境变量。
5. **🚦 物理断点与操作安全 (Safety Gate)**
   - 对外写操作（写入 CRM、群发消息等）建议设置“提取 ➔ 预览拦截 ➔ 显式确认 ➔ 实际执行”的安全断点。
6. **🧪 物理执行验证 (Mandatory Verification)**
   - 交付前需对新增脚本或逻辑执行真实运行测试或 Dry-run，确保交付质量。
7. **🛑 严禁自动 Git 提交原则 (User Confirmation Required for Git Commits)**
   - 🚨 **严禁 Agent 自动/自主执行 Git 提交与推送**：所有的 `git commit` 与 `git push` 操作**必须由用户主动确认、明确授权或下达提交指令后方可执行**。
   - Agent 仅负责在完成验证后生成规范的 Commit 建议（遵循 `xxx(xxx): 中文xxx` 格式与全中文注释），严禁在未获用户许可前自作主张发起提交。
8. **📝 原子化与分类提交规范 (Atomic & Classified Commits)**
   - 🚫 **严禁跨技能混杂提交**：不同技能的变更必须**拆分为独立的 Commit** 分别提交，严禁在一个提交中同时包含多个技能目录的修改。
   - 🚫 **严禁业务技能与全局基建混杂提交**：运维脚本 (`scripts/`)、测试套件 (`tests/`)、全局文档 (`AGENTS.md` / `README.md`) 及元数据注册表 (`skills_manifest.json`) 的更新必须与具体技能代码分开独立提交。
   - **Commit 格式规范**：提交时统一格式为 `xxx(xxx): 中文xxx`：
     - **单技能开发与维护**：`feat(skills/<name>): 新增 xxx 技能`、`fix(skills/<name>): 修复 xxx 技能中的 xxx 问题`、`sec(skills/<name>): 修复敏感路径与凭据脱敏`
     - **全局元数据注册表**：`chore(manifest): 同步全量技能元数据注册表`
     - **运维管理脚本**：`refactor(scripts): 优化管理脚本逻辑`、`feat(scripts): 新增 xxx 运维指令`
     - **规范与全局文档**：`docs(agents): 完善 AGENTS.md 规范说明`、`docs(readme): 更新项目说明文档`
     - **测试套件与用例**：`test(manage_skills): 补充安全扫描测试用例`
9. **💬 中文注释要求 (Chinese Comments)**
   - 所有新增或修改的代码、脚本和配置文件，应包含清晰、规范的中文注释。
10. **💡 文档与描述业务清晰性原则 (Business-Context-Rich Documentation)**
    - **拒绝抽象套话**：在项目主页 `README.md`、技能清单 `SKILLS.md` 及技能元数据描述中，严禁使用含糊、抽象的技术套话。
    - **明确业务归属与上下文**：必须清晰说明技能所服务的具体业务场景、对接系统（如特定 CRM、部门知识库空间、工单系统等）及其核心价值，确保团队成员查阅项目总览时能够一目了然。
