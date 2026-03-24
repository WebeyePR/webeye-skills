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

- **[查看所有技能索引 (SKILLS.md)](./SKILLS.md)**：包含本库目前支持的所有 AI 技能详情、描述及状态。
- **[贡献指南](#贡献指南)**：了解如何向本项目提交新技能。

## 目录结构

```text
.
├── skills/           # AI 技能存放区 (每一个子文件夹包含各自的 SKILL.md)
├── lib/             # 共享公共逻辑 (feishu-sdk等)
├── docs/             # 项目全局文档
├── SKILLS.md         # 技能清单 (索引)
└── README.md         # 项目入口
```

## 贡献指南

1. 在 `skills/` 目录下创建新的技能文件夹。
2. 确保包含完整的技能定义 (`SKILL.md`)，并包含正确的 YAML frontmatter。
3. 遵循 Python 最佳实践，使用类型提示和完善的注释。
4. 提交前请运行相关测试。

## 维护者

- Webeye CSBU Team
