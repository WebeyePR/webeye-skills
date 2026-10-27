# 待修复问题记录（Pending Fixes）

> 本文档记录已发现、暂缓处理的问题，供后续排期修复。修复完成后请在此勾销并注明提交。
> 记录原则：只记录、不遗忘；按范围隔离原则，跨技能问题不与其他工作混杂处理。

---

## PF-001 feishu-crm-copilot：开发者本机绝对路径泄露（中危 ×3）

- **发现时间**：2026-10-27 09:45（`python3 scripts/manage_skills.py security` 全量扫描）
- **风险等级**：🟡 MEDIUM（SEC-010-LOCAL-PATH，开发者本机绝对路径泄露）
- **严重/高危**：0 项；本次扫描 market-intel 技能零命中

### 问题明细

| # | 位置 | 脱敏片段 |
|---|---|---|
| 1 | `skills/feishu-crm-copilot/SKILL.md:52` | `[workflow-crm-record.md](file:///Use****d.md)` |
| 2 | `skills/feishu-crm-copilot/SKILL.md:54` | `[workflow-crm-customer.md](file:///Use****r.md)` |
| 3 | `skills/feishu-crm-copilot/SKILL.md:56` | `[workflow-crm-opportunity.md](file:///Use****y.md)` |

### 建议修复方式

三处均为「加载参考件」的 `file:///Users/...` 本地绝对路径链接，改为技能内相对路径即可：

```markdown
# 修复前
[workflow-crm-record.md](file:///Users/<本机用户名>/.../workflow-crm-record.md)

# 修复后
[workflow-crm-record.md](./references/workflow-crm-record.md)
```

### 处置状态

- [ ] 待修复（2026-10-27 记录，先记录暂不处理：属于他技能存量问题，按范围隔离原则不与 market-intel 工作混杂）
- 修复时同步递增该技能 `version` 并跑 `manage_skills.py lint --skill feishu-crm-copilot` 验证
