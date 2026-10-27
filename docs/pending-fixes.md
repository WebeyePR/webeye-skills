# 技术债与待修复问题清单（Technical Debt & Pending Fixes）

> 本文档记录全局扫描发现的已确认技术债，供后续排期修复。修复完成后请在此勾销并注明提交号。
> 记录原则：**只记录、不遗忘**；按 AGENTS.md 范围隔离原则，跨技能问题不与其他工作混杂处理。
> 取证方式：`python3 scripts/manage_skills.py security|lint|stats` + 独立脚本核查（**非仅依赖 lint 结论**，因 lint 本身存在盲区，见 TD-002）。

**最近全量扫描时间**：2026-10-27 11:20（GMT+8）
**扫描范围**：16 个技能全量、`scripts/`、`tests/`、`docs/`、`skills_manifest.json`、`SKILLS.md`、git 历史

---

## 📊 债务总览

| 编号 | 标题 | 等级 | 影响面 | 状态 |
| :--- | :--- | :--- | :--- | :--- |
| **TD-001** | crm-followup-writer 硬编码飞书表格资源 ID（**非凭证**） | 🟡 MEDIUM | 结构情报暴露 | 待修复 |
| **TD-002** | Lint 工具必填字段校验盲区（元数据问题总根因） | 🔴 HIGH | 治理工具失效 | 待修复 |
| **TD-003** | manifest 注册表版本静默兜底，与磁盘状态漂移 | 🔴 HIGH | 8 个技能 | 待修复 |
| **TD-004** | feishu-crm-copilot 本机绝对路径泄露 ×3 | 🟡 MEDIUM | 信息泄露 | 待修复（原 PF-001） |
| **TD-005** | 缺少 YAML Frontmatter（2 个技能完全缺失） | 🟠 HIGH | 技能可能无法加载 | 待修复 |
| **TD-006** | Frontmatter 必填字段缺失（version/author） | 🟠 MEDIUM | 10 个技能 | 待修复 |
| **TD-007** | status 双事实源，frontmatter 未声明 | 🟠 MEDIUM | 15/16 技能 | 待修复 |
| **TD-008** | `alwaysActive` 非标准字段被宿主静默忽略 | 🟡 MEDIUM | 6 个技能 | 待修复 |
| **TD-009** | 测试覆盖严重不足（有效 3/16，含 1 个空目录） | 🟠 MEDIUM | 13 个技能 | 待修复 |
| **TD-010** | 凭证类技能缺 `.env.example` 模板 | 🟡 MEDIUM | 3 个技能 | 待修复 |
| **TD-011** | `change-watch` 测试夹具放在 `.workbuddy/` 非标准目录（**非误提交**） | 🔵 LOW | 目录规范/文档矛盾 | 待修复 |
| **TD-015** | `change-watch` 2 个夹具硬编码绝对路径，**他人 clone 后必崩** | 🟠 HIGH | 测试不可用 | 待修复 |
| **TD-012** | `lib/` 空目录与 README 描述不符 | 🔵 LOW | 文档准确性 | 待修复 |
| **TD-013** | `market-intel` SKILL.md 超渐进披露建议上限 | 🔵 LOW | 上下文成本 | 观察中 |
| **TD-014** | 工作区存在未提交变更（market-intel v0.5.0） | ⚪ INFO | 待用户决策 | 待确认 |

---

## 🟡 TD-001 crm-followup-writer：硬编码飞书多维表格资源 ID

> **重要修正**：2026-10-27 11:25 经用户质疑后深度复核，本项**原定级 CRITICAL 属误判**，已下调为 MEDIUM。详见下方「原定级为何过高」。

- **发现时间**：2026-10-27 11:18（全量扫描，**非** `manage_skills.py security` 报出）
- **风险等级**：🟡 **MEDIUM**（信息/结构暴露，**非**凭证泄露）
- **位置**：`skills/crm-followup-writer/scripts/write_followup.py:34-38`
- **涉及内容（已脱敏）**：`BASE_TOKEN = "XFl3b5****（27 字符）"`

### 问题明细

该脚本硬编码了完整的飞书多维表格定位信息，共 5 个常量：

| 行号 | 常量 | 性质 |
| :--- | :--- | :--- |
| 34 | `BASE_TOKEN` | Base / app_token（**资源标识，非凭证**） |
| 35 | `TABLE_ID = "tblGIUss****"` | 客户跟进记录表 ID |
| 36 | `CUSTOMER_TABLE_ID = "tblF6xy****"` | 客户管理表 ID |
| 37 | `CUSTOMER_NAME_FIELD = "flduEY****"` | 字段 ID |
| 38 | `VIEW_ID = "vew1ZNE****"` | 视图 ID |

### 原定级为何过高（核心澄清）

初判时把 `app_token` 误当作凭证，实际核实后：

1. **`app_token` 不是密钥，只是资源定位符**。它是 URL 中 `/base/<app_token>` 位置的书签式标识，等同「书号」而非「钥匙」。同类形态在本仓库 `feishu-crm-copilot/SKILL.md` 中作为**公开示例**出现（`GZGyb9O****`）。
2. **访问数据需要另外三层凭证**：`tenant_access_token` 或 `user_access_token`，且须由 `App ID + App Secret` 换取，并须在该表上被添加为协作者或开通 `bitable:app` 权限。
3. **仓库内不存在任何 App Secret**。专项扫描 `app_secret`/`client_secret` 真实赋值，**0 命中**。无 Secret 则无法换取任何访问令牌。
4. **脚本本身用 `--as user`**（`write_followup.py:64/92/194`），依赖 lark-cli 的**本地登录态**。真正的权限来源是操作者的飞书账号，与该字符串无关。

**结论：外部人即使拿到此 token，无飞书账号权限也无法读取任何数据。**

### 真实风险（降级后的准确定位）

风险不在「能否访问」，而在以下两点：

1. **仓库为 PUBLIC**（已核实：`WebeyePR/webeye-skills`，`visibility: PUBLIC`，113 个文件全网可见）。因此暴露的是**结构情报**——Webeye 存在这样一张 CRM 多维表格、含客户管理/客户跟进/商机三张子表及其字段命名，可被用于推断业务数据模型。
2. **取决于该表的链接分享设置**。飞书分享有「互联网上获得链接的人可阅读」一档，官方明确标注为「公开可见，可能存在数据泄露的合规风险」。**若**该表被设为这一档，则 app_token 等于半个钥匙，可匿名打开只读页。此设置无法从代码判断，**需到飞书后台核实**（内部 CRM 表设为公开的可能性不高，但属必须验证项）。

### 历史暴露范围

- 引入提交：`c2f8efd feat(skills/crm-followup-writer): 收录业务一部CRM跟进自动同步技能`（**2026-08-18**）
- 公开暴露时长：约 **70 天**
- `git log -S` 确认仅 1 个提交引入，至今仍在 HEAD
- 因仓库确认公开，**该 token 已确定存在于公开网络**

### 为何现有安全扫描未报出

`scripts/manage_skills.py` 的 `SEC-*` 规则覆盖 `sk-`/`AIza`/`ghp_`/PEM 私钥等通用模式，但**不识别飞书 app_token 形态**，也不识别 `tbl*`/`fld*`/`vew*` 资源 ID。这是扫描规则的覆盖缺口。

**但需注意**：考虑到 app_token 本身非凭证，把它加入「高危泄密」规则会产生大量误报（该形态在飞书文档中极为常见）。建议加入**中低危提示级**规则，定位为「内部资源 ID 硬编码提醒」而非「凭证泄露」。

### 建议修复方式

动因是**规范一致性**（AGENTS.md 红线 4「避免硬编码」），而非紧急安全事件：

1. 改为环境变量或配置文件读取：
   ```python
   import os
   BASE_TOKEN = os.getenv("FEISHU_CRM_BASE_TOKEN", "")
   TABLE_ID = os.getenv("FEISHU_CRM_RECORD_TABLE_ID", "tblGIUss****")  # 非敏感，可保留默认
   ```
2. 补充 `skills/crm-followup-writer/.env.example` 模板（见 TD-010）。
3. **飞书后台核实该项**：确认该多维表格的链接分享范围不是「互联网上获得链接的人」。**这是唯一需要实际确认的安全动作**。
4. 凭证轮换与 `git filter-repo` 清洗历史：**均不必要**。仅当第 3 项核实发现该表对外公开时，才需按真实安全事件处理。

### 处置状态

- [ ] 待修复（建议优先级低于 TD-002/003；需先完成第 3 项飞书后台核实）

---

## 🔴 TD-002 Lint 工具必填字段校验盲区（元数据问题总根因）

- **风险等级**：🔴 **HIGH**（治理工具失效，导致所有元数据问题长期隐形）
- **位置**：`scripts/manage_skills.py:587-596`（`run_lint` 函数）

### 问题明细

```python
if "name" in meta and meta["name"] != skill_name:   # 字段缺失 → 不进入判断
if "version" in meta:                               # 字段缺失 → 不进入判断
if "status" in meta and ...:                        # 字段缺失 → 不进入判断
```

采用「**存在才校验**」的条件式判断，而 `AGENTS.md §2.1` 明确将 `name`/`description`/`version`/`author` 标注为**必填**。结果：

- 字段缺失时 lint **完全不检查**，不产生任何 Error 或 Warning
- 实测 `lint` 结论为「0 Errors，6 Warnings」，但真实情况是 **10-11 个技能元数据不合规**
- 这直接解释了为何 TD-005/006/007 长期无人发现

### 业界规范对照

Agent Skills 开放规范（被 Claude Code、OpenAI Codex、Cursor、GitHub Copilot、OpenCode 等 30+ 工具采纳）要求：`name` 与 `description` 为**必填**，`name` 须匹配 `^[a-z0-9]+(-[a-z0-9]+)*$` 且与目录名一致（1-64 字符），`description` 1-1024 字符。官方校验器为 `uvx --from skills-ref agentskills validate <path>`，退出码 0 方为合规。本项目 lint 的严格度显著低于该基线。

### 建议修复方式

1. 将必填字段校验改为**缺失即报 Error**（而非条件跳过）。
2. 增加 `name` 正则校验（现有仅比对目录名，未校验字符集与长度）。
3. 增加 `description` 长度 1-1024 校验（当前完全未校验）。
4. 增加 `scripts/` 存在但无 `tests/` 的提示级告警（对齐 TD-009）。
5. 补充飞书 Token 与资源 ID 的检测规则（对齐 TD-001）。
6. 补充对应单元测试到 `tests/test_manage_skills.py`。

### 预期影响

修复后重新运行 `lint` 将**从 0 Errors 变为报出约 20 项 Error**，属预期行为——这是把隐形债务显性化的过程。建议同批完成 TD-005/006/007 的补齐，避免 CI 长期红。

### 处置状态

- [ ] 待修复（**建议优先于所有元数据逐项修复**，否则修完仍不可验证）

---

## 🔴 TD-003 manifest 注册表版本静默兜底，与磁盘状态漂移

- **风险等级**：🔴 **HIGH**（机器可读注册表丧失可信度）
- **位置**：`scripts/manage_skills.py:685`（`sync_manifest_and_skills`）

### 问题明细

```python
ver = str(s.get("version", "1.0.0"))   # 缺失时静默填 1.0.0
```

实测**8 个技能**的注册表版本与磁盘真实状态不一致：

| 技能 | SKILL.md 真实状态 | manifest 记录 |
| :--- | :--- | :--- |
| agent-proxy-communication | 无 frontmatter | 1.0.0 |
| billing-config-error | 无 version 字段 | 1.0.0 |
| content-broadcast | 无 frontmatter | 1.0.0 |
| crm-followup-writer | 无 version 字段 | 1.0.0 |
| feishu-contract-audit | 无 version 字段 | 1.0.0 |
| gcp-support-ticket | 无 version 字段 | 1.0.0 |
| webeye-kb-organizer | 无 version 字段 | 1.0.0 |
| wechat-daily-monitor | 无 version 字段 | 1.0.0 |

### 业界规范对照

业界对「生成式清单」的一致实践是：**清单必须忠实反映真实状态，任何不一致都应导致构建失败，而非静默兜底**。代表性做法包括——生成器遇到缺失值时报错或显式标记为 `unknown`；CI 中运行 `--check` 模式重新生成并与已提交文件比对，不一致即失败（drift check）。静默填默认值是反模式：它让「清单显示 X」不再等价于「实际是 X」。

### 建议修复方式

将兜底改为显式标记，例如 `s.get("version") or "UNKNOWN"`，并在 manifest 中增加 `"warnings"` 字段列出缺失项；或在 sync 时直接报错并提示补齐。与 TD-002 同批处理。

### 处置状态

- [ ] 待修复

---

## 🟡 TD-004 feishu-crm-copilot：本机绝对路径泄露 ×3

> 原编号 PF-001，2026-10-27 09:45 首次记录，**本次复核仍然存在**

- **风险等级**：🟡 MEDIUM（SEC-010-LOCAL-PATH）

### 问题明细

| # | 位置 | 泄露内容 |
| :--- | :--- | :--- |
| 1 | `skills/feishu-crm-copilot/SKILL.md:52` | `[workflow-crm-record.md](file:///Users/apple/Project/Git/webeye-skills/...)` |
| 2 | `skills/feishu-crm-copilot/SKILL.md:54` | `[workflow-crm-customer.md](file:///Users/apple/...)` |
| 3 | `skills/feishu-crm-copilot/SKILL.md:56` | `[workflow-crm-opportunity.md](file:///Users/apple/...)` |

泄露内容包括本机用户名 `apple` 与完整目录结构。

### 复核补充证据

- 三个目标文件**确实存在**于同目录 `references/` 下（`workflow-crm-record.md` 11762B、`workflow-crm-customer.md` 10264B、`workflow-crm-opportunity.md` 8891B），故改相对路径**零风险、不会断链**
- 全仓库其他技能的相对链接断链数为 **0**，证明相对路径方案在本仓库已成熟可行
- `file://` 协议在浏览器/HTTP 环境下受安全策略拦截，本身就是**死链接**
- 业界明确将此列为红线：「任何情况下禁止把含本机用户名的绝对路径写入仓库文件」，理由为泄露开发者身份、目录结构，且链接不可用

### 建议修复方式

```markdown
# 修复前
[workflow-crm-record.md](file:///Users/apple/Project/Git/webeye-skills/skills/feishu-crm-copilot/references/workflow-crm-record.md)
# 修复后
[workflow-crm-record.md](./references/workflow-crm-record.md)
```

修复时同步递增该技能 `version: 2.0.9 → 2.1.0`，并跑 `python3 scripts/manage_skills.py lint --skill feishu-crm-copilot` 验证。

### 处置状态

- [ ] 待修复（2026-10-27 记录，按范围隔离原则暂缓；修复成本极低，建议优先排期）

---

## 🟠 TD-005 缺少 YAML Frontmatter（2 个技能完全缺失）

- **风险等级**：🟠 **HIGH**（功能性缺陷，非仅规范问题）

### 问题明细

| 技能 | 现状 | 后果 |
| :--- | :--- | :--- |
| `agent-proxy-communication` | 用 Markdown 表格替代元数据（`\| Identifier \| agent-proxy-communication \|`、`\| Version \| 3.0.0 \|`） | 宿主无法解析 name/description，**技能不会被发现和加载** |
| `content-broadcast` | 完全无任何元数据，仅 `## 描述` 段落 | 同上，且连技能标识都无机器可读来源 |

### 业界规范对照

所有 Agent Skills 兼容宿主（Claude Code / Copilot / Cursor / Codex / OpenCode 等）均要求 SKILL.md **必须以 YAML frontmatter 开头**，且 `name` + `description` 必填。缺失时的行为分两类：一类直接跳过该技能，另一类记为非致命告警后跳过。**两种情况下技能都不可用**。`name` 还须与所在目录名完全一致。

### 建议修复方式

为两个技能补标准 frontmatter（保留原文正文不变，仅在文件头插入）：

```yaml
---
name: agent-proxy-communication   # 原表格中的 Identifier 值
description: >                    # 需含触发场景与关键词
  ...
version: 3.0.0                    # 沿用原表格 Version
author: xuhao
status: 调试中
---
```

`content-broadcast` 的 description 需新撰写（含「链接摘要」「飞书群推送」等触发关键词）。

### 处置状态

- [ ] 待修复

---

## 🟠 TD-006 Frontmatter 必填字段缺失（version/author）

- **风险等级**：🟠 MEDIUM
- **来源**：TD-002 的盲区导致长期未发现

### 问题明细（实测 16 个技能完整分布）

| 分类 | 技能 | 缺失字段 |
| :--- | :--- | :--- |
| 完全无 frontmatter | `agent-proxy-communication`、`content-broadcast` | 全部（见 TD-005） |
| 缺 version + author | `billing-config-error`、`crm-followup-writer`、`feishu-contract-audit`、`gcp-support-ticket`、`webeye-kb-organizer`、`wechat-daily-monitor` | version, author |
| 仅缺 author | `ai-daily-digest`、`change-watch` | author |
| **字段齐全** | `example-skill`、`feishu-crm-automation`、`feishu-crm-copilot`、`feishu-dynamic-task-feedback`、`feishu-knowledge-base`、`market-intel` | — |

**结论：16 个技能中仅 6 个（37.5%）字段齐全，10 个存在缺项。**

### 版本号格式问题

`change-watch` 使用 `version: v20261016.1`。

- 对照结论：calver.org 官方**支持** `YYYY.M.D.MICRO` 微段（用于同日多次发布），故该写法在业界语境下**合法**
- 真正问题是与本项目自定约定（AGENTS.md 与 lint 规则均要求 `vYYYYMMDD` 无微段）**不一致**
- 处置建议二选一：① 改为 `v20261016`；② 在 AGENTS.md 中正式承认微段写法并同步修改 lint 正则。**建议选 ②**，因微段能表达同日多次发布，信息量更高。

### 建议修复方式

批量补齐，每技能独立提交（遵循 AGENTS.md 红线 8 禁止跨技能混杂提交）。

### 处置状态

- [ ] 待修复

---

## 🟠 TD-007 status 双事实源，frontmatter 未声明

- **风险等级**：🟠 MEDIUM（架构性一致性问题）

### 问题明细

- 实测 **15/16 技能未在 frontmatter 声明 `status`**（唯一例外：`market-intel` 声明了 `status: 开发中`）
- 真实状态的权威来源实际是 `SKILLS.md` 表格，由 `parse_skills_md_defaults()`（`manage_skills.py:258`）解析后**反向回填**到 manifest
- 违反 `AGENTS.md` 的核心定位：「`SKILL.md` 是 Agent 读取与执行的标准入口」

### 实际危害

Agent 读取 SKILL.md 时无法判断该技能是「生产可用」还是「开发中/调试中」——对 `feishu-crm-automation`（开发中）、`agent-proxy-communication`（调试中）这类技能，Agent 会误当作可用的生产能力调用。人类读 SKILLS.md 才能获知真相，形成机器/人类信息不对称。

### 业界规范对照

单一事实源原则：状态类元数据应在其权威载体内声明，派生载体（此处 manifest、SKILLS.md）由生成器产出，不应反向成为输入。当前实现是「SKILLS.md 作为输入 → 回填 manifest」，方向与规范相反。

### 建议修复方式

1. 在 15 个技能的 frontmatter 中补 `status` 字段（取值：`生产可用`/`调试中`/`开发中`/`已废弃`）。
2. 调整 `parse_skills_md_defaults()` 逻辑：改为以 frontmatter 为准生成 SKILLS.md，而非反向回填。

### 处置状态

- [ ] 待修复

---

## 🟡 TD-008 `alwaysActive` 非标准字段被宿主静默忽略

- **风险等级**：🟡 MEDIUM（预期行为与实际行为不符）

### 问题明细

6 个技能声明了 `alwaysActive` 字段：

| 技能 | 值 |
| :--- | :--- |
| `example-skill` | true |
| `feishu-crm-automation` | true |
| `feishu-crm-copilot` | true |
| `feishu-dynamic-task-feedback` | true |
| `feishu-knowledge-base` | true |
| `market-intel` | false |

该字段**不在** AGENTS.md §2.1 定义的标准字段集内，也**不在** Agent Skills 开放规范的字段白名单内（白名单仅为 `name`/`description`/`license`/`compatibility`/`metadata`/`allowed-tools`）。

### 业界规范对照（关键）

各主流实现的明确行为是：**未知 frontmatter 字段被静默忽略**（silently ignored），以保持向前兼容。因此：

- `alwaysActive: true` **不会**让技能「常驻激活」，该语义在当前生态中没有对应实现
- 作者以为的「让此技能始终加载」的控制力**实际不存在**
- 6 个技能均受影响，其中 `feishu-crm-copilot`（CRM 核心技能）若依赖该字段保证加载，属实质性风险

### 建议修复方式

1. 先与技能作者确认 `alwaysActive` 的真实意图：是「常驻加载」还是「默认启用」？
2. 若意图为常驻加载：当前生态无标准字段支持，需改由宿主侧配置或 AGENTS.md 显式引用实现，并**删除该无效字段**避免误导。
3. 其余非标准字段（`license` 7 个、`description_en`/`agent_created`/`label`/`category` 各 1 个）：`license` 属规范允许字段可保留；`label`/`category` 等私有字段建议迁入规范的 `metadata:` 映射下。

### 处置状态

- [ ] 待修复（需先与作者确认语义）

---

## 🟠 TD-009 测试覆盖严重不足

- **风险等级**：🟠 MEDIUM
- **实测**：`stats` 报告「配套测试覆盖 3 / 16」，实际比该数字更差

### 问题明细

| 技能 | 脚本数 | tests/ 目录 | 真实测试 |
| :--- | :--- | :--- | :--- |
| `market-intel` | 9 | ✅ | 126 个 test 函数（最完善） |
| `feishu-crm-automation` | 5 | ✅ | 2 个 test 函数 |
| `change-watch` | 4 | ⚠️ 在 `.workbuddy/` | **41 项断言**（7+18+16+请求头），实际可运行，但因位置非标准被 stats 漏计，见 TD-011 |
| `example-skill` | 2 | ✅ 仅 `.gitkeep` | **0，空目录** |
| `wechat-daily-monitor` | 2 | ❌ | 0 |
| `crm-followup-writer` | 1 | ❌ | 0 |

**核心问题分两层**：

1. **真实缺测试**：`example-skill`（tests/ 为空目录）、`wechat-daily-monitor`（2 个脚本）、`crm-followup-writer`（1 个脚本，写生产 CRM）三个技能零测试
2. **统计失真**：`change-watch` 实际有 41 项可运行断言，但因放在 `.workbuddy/` 而非 `tests/`，`stats` 记为「无测试」。加上 `example-skill` 的空目录使「3/16」虚高，`stats` 报告的测试覆盖数字**两个方向都不准**

修正后的真实情况：**16 个技能中 3 个有实质测试**（market-intel 126 项、change-watch 41 项、feishu-crm-automation 2 项），覆盖率约 19%。

此外 `tests/` 根目录仅覆盖 `manage_skills.py` 自身（6 个用例），不覆盖任何业务技能逻辑。

### 业界规范对照

Agent Skills 社区最佳实践要求每个生产技能至少具备：
1. **触发测试**：正例（直接请求 + 改写请求均命中）与反例（无关请求不误触发）
2. **功能测试**：正常操作、边界情况、超范围请求；同一请求运行 3-5 次验证输出一致性

CLI 类脚本还应至少覆盖参数解析与错误分支。当前 3 个无测试技能均含真实业务写入逻辑（CRM 写入、公众号抓取），缺测试的风险面高于纯文档技能。

### 建议修复方式

按业务风险排序：`crm-followup-writer`（写生产 CRM，风险最高）→ `wechat-daily-monitor` → `example-skill`（若保留其示例定位）。同时：

- 把 `change-watch` 夹具迁至 `tests/`（见 TD-011），使 `stats` 统计正确
- 清理 `example-skill/tests/.gitkeep` 空目录或补实际测试，避免统计失真
- 建议在 `manage_skills.py stats` 中把「有 tests/ 目录但零测试文件」单独标记，防止再次出现空目录虚高

### 处置状态

- [ ] 待修复

---

## 🟡 TD-010 凭证类技能缺 `.env.example` 模板

- **风险等级**：🟡 MEDIUM（违反 AGENTS.md 红线 3）
- **实测**：仅 3/16 提供模板

### 问题明细

| 技能 | 引用的凭证变量 | 有模板 | 说明 |
| :--- | :--- | :--- | :--- |
| `feishu-crm-copilot` | `FEISHU_CRM_BITABLE_TOKEN`、`FEISHU_CRM_CUSTOMER_TABLE_ID`、`FEISHU_CRM_RECORD_TABLE_ID`、`FEISHU_CRM_OPPORTUNITY_TABLE_ID` | ❌ | 正文明确要求「检测环境变量或 .env」，却无模板 |
| `crm-followup-writer` | `BASE_TOKEN`（当前硬编码，见 TD-001） | ❌ | 修复 TD-001 时必须同步补模板 |
| `example-skill` | `OPENAI_API_KEY`（`scripts/config.py:11`） | ❌ | 示例技能应作示范 |
| `feishu-crm-automation` | `FEISHU_CLIENT_ID`/`FEISHU_CLIENT_SECRET` | ✅ | — |
| `market-intel` | 可选变量 | ✅ | 内容完整，含说明 |
| `wechat-daily-monitor` | `JIZHILE_API_KEY`/`DAJIALA_API_KEY` | ⚠️ 部分 | 模板仅含 `JIZHILE_API_KEY`，脚本支持 `DAJIALA_API_KEY` 作为备用名（`fetch_daily_posts.py:71`）但未在模板说明 |

### 建议修复方式

补 3 个缺失模板，并完善 `wechat-daily-monitor` 模板的双变量说明（注明两者为别名关系、择一配置）。模板内一律使用占位值，禁止真实凭证。

### 处置状态

- [ ] 待修复

---

## 🔵 TD-011 change-watch 测试夹具放置在 `.workbuddy/` 非标准目录

> **重要修正**：2026-10-27 11:36 深度复核后，本项**原定性「临时脚本被误提交」不成立**，性质已重新界定。详见下方「原判断错在哪」。

- **风险等级**：🔵 LOW（目录规范问题，非误提交、非安全问题）
- **位置**：`skills/change-watch/.workbuddy/`（4 个文件，已被 git 跟踪且在 HEAD 中）

### 事实核实（逐项验证）

| 核实项 | 结果 |
| :--- | :--- |
| 是否在 git 索引与 HEAD | ✅ 是，`git cat-file -p HEAD:skills/change-watch/.workbuddy/` 返回 4 个 blob |
| 是否误提交 | ❌ **否，属有意提交** |
| 夹具是否可运行 | ✅ **4 套全部可运行，合计 41 项全通过** |
| 是否有价值 | ✅ 是，覆盖并发/解析/滚出/LinkedIn 请求头等真实逻辑 |

### 引入提交（含作者完整说明）

提交 `c6f2061 test(change-watch): 补入三个未入库的测试夹具`（guyue，2026-10-14），提交说明全文：

> verify_rolled_out.py / verify_concurrency.py / verify_linkedin_headers.py
> 自各自版本起就在工作区运行，但从未 git add（提交信息引用了、仓库里没有），
> **换机器 clone 会缺失测试套件**。本次补齐，与 verify_parse.py 共同构成四套可
> 随时重跑的夹具（滚出 7 + 并发 18 + LinkedIn 请求头 + 解析 16）。

`verify_parse.py` 由更早的 `1a50b0b fix(change-watch): X 三类特殊推文与 RSS 位置键修复 (v20261014.1)` 引入。

**这是一次有明确理由、有清晰动机的正确提交**——作者是为了解决「夹具只在本地、换机器 clone 就丢失」的真实问题。提交信息规范性（`test(change-watch):` 前缀）也符合 AGENTS.md 红线 8。

### 实测运行结果（本次验证）

```text
verify_rolled_out.py        结果: 7/7 通过
verify_concurrency.py       结果: 18/18 通过
verify_parse.py             结果：16 通过 / 0 失败
verify_linkedin_headers.py  联网对比 A/B 请求头，返回 SAME
```

合计 **41 项断言全部通过**，夹具质量良好、可直接纳入 CI。

### 原判断错在哪

上一轮记录了三处不准确的推断：

1. 把 `.workbuddy/` 的**目录命名**直接推断为「AI 会话临时产物」，进而认定是误提交
2. 未查看提交说明就下结论
3. 建议「迁至 tests/ 或 `git rm --cached`」——后者会**误删 41 项有效测试**，是危险建议

### 是否故意提交：分文件结论（2026-10-27 11:40 补充取证）

**结论：4 个文件全部是故意提交，但两类提交的理由不同。**

触及该路径的全部提交只有 2 个（`git log --all -- skills/change-watch/.workbuddy/`）：

| 文件 | 引入提交 | 提交方式 | 是否故意 |
| :--- | :--- | :--- | :--- |
| `verify_parse.py` | `1a50b0b` | 与功能修复同批提交 | ✅ **是** |
| `verify_concurrency.py` | `c6f2061` | 单独专项提交 | ✅ **是** |
| `verify_linkedin_headers.py` | `c6f2061` | 单独专项提交 | ✅ **是** |
| `verify_rolled_out.py` | `c6f2061` | 单独专项提交 | ✅ **是** |

**判断依据（逐项证据）**：

**① `c6f2061` 是纯粹的定向提交，不是误带**

`git show --name-status c6f2061` 只列出 3 个新增文件，**无任何其他文件混入**：

```
A  skills/change-watch/.workbuddy/verify_concurrency.py
A  skills/change-watch/.workbuddy/verify_linkedin_headers.py
A  skills/change-watch/.workbuddy/verify_rolled_out.py
```

若为 `git add -A` 误带，通常会混入 README/SKILL.md/scripts 等其他改动。这里**只有这 3 个文件**，说明作者是逐个 `git add` 精确提交的。提交前缀也用了规范的 `test(change-watch):`。

**② 提交说明明确写出了动机（排除「顺手提交」可能）**

`c6f2061` 说明原文：

> 自各自版本起就在工作区运行，但从未 git add（提交信息引用了、仓库里没有），**换机器 clone 会缺失测试套件**。

这里揭示了一个关键因果：此前的 `1a50b0b` 提交说明里**已经引用了**「三套夹具共 41 项全绿」，但当时只 add 了 `verify_parse.py`，另三套漏了。`c6f2061` 是在**修补自己前一个提交的不完整**——发现说明与实物不符后主动补齐。这是负责任的行为，不是疏忽。

**③ 两个提交相隔仅 41 秒，印证「发现漏提交后立即补」**

```
1a50b0b  2026-10-14 11:05:11  fix(change-watch): X 三类特殊推文...修复
c6f2061  2026-10-14 11:05:52  test(change-watch): 补入三个未入库的测试夹具
```

中间无其他提交。典型的「提交后立刻发现漏了，马上补一个」的时序。

**④ `verify_parse.py` 的提交理由不同，但同样是故意**

它随 `1a50b0b` 与 `watch.py`、`README.md`、`SKILL.md` 一并提交（`M` + `A` 混合），因为它是当次功能修复的**配套验证工具**——说明里写明「新夹具 `.workbuddy/verify_parse.py` 16 项」用于验证 note 全文/RSS 位置键等改动。属「改代码 + 附验证手段」的正常工程行为。

### 那为什么用了 `.workbuddy/` 这个目录名？

**事实层面（可确证的）**：

- `change-watch` 由 `guyue` 于 2026-10-10 以 `a91dca7 feat(skills/change-watch): 新增 change-watch 技能` 首次入库，**首次入库时目录中没有 `.workbuddy/`**（`git ls-tree a91dca7` 显示仅 README/SKILL/config/references/scripts）
- `.workbuddy/` 是 4 天后（10-14）才出现的
- 全仓库仅 `change-watch` 一个技能使用该目录名（`git log --all --name-only` 全历史扫描确认）
- 根目录 `.workbuddy/` **从未被 git 跟踪**（`git log --all -- .workbuddy/` 为空）
- 该技能 `SKILL.md` 头部有 `agent_created: true`，说明**由 AI 协作创建**

**推断层面（标注为推断，未获作者确认）**：

最可能的解释是——该技能在与 AI 助手（WorkBuddy）协作开发，`.workbuddy/` 是**工作过程中自然形成的目录**，作者把验证脚本放在了当时顺手的位置，随后因为脚本确实有价值就一并提交了。

**但必须说明**：这一点**无法从仓库中确证**。作者没有在任何提交说明中解释为何选这个目录名。我不会把推断当结论写入——如需确认，应直接问 `guyue`。

### 真正存在的问题（重新界定）

问题不在「提交了」（4 个文件都该提交），而在**目录选择与文档一致性**，共三点：

1. **命名语义冲突**：`.workbuddy/` 在生态中普遍指 AI 工具会话工作区（本项目根目录就有一个存 memory）。正式测试代码放同名目录，易被后续维护者（含 AI Agent）误判为临时文件——本次误判即是明证。
2. **与技能自身文档矛盾**：`skills/change-watch/SKILL.md:308` 明确声明「**不依赖任何工具的私有目录：技能不引用 `.workbuddy`、`.doubao` 之类路径**」，而该技能目录下就有 `.workbuddy/` 且 `README.md:152` 直接引用了它。**文档与实际自相矛盾，这是确定性问题**。
3. **工具统计失效**：因不在标准 `tests/` 下，`manage_skills.py stats` 统计不到，导致 change-watch 被记为「无测试」（实际有 41 项）——**测试覆盖被系统性低估**。

### 附带发现：README 存在真实断链

`README.md:256` 指向 `[research/](../../.workbuddy/research/)`，解析结果为根目录 `.workbuddy/research/`：

- **该路径不存在**（`os.path.exists` = False）
- 根目录 `.workbuddy/` **从未被 git 跟踪**，故 clone 后必然 404
- 这是 `verify_parse.py` 放在 `.workbuddy/` 之外、**另一个独立的目录混淆问题**：作者把「根目录 `.workbuddy/`」当成了可引用的仓库路径，但它既不在仓库里，也不存在于当前工作区（只有 `memory/`）

### 建议修复方式（仅一个方案）

**迁移至 `skills/change-watch/tests/`**，保留 git 历史：

```bash
git mv skills/change-watch/.workbuddy/*.py skills/change-watch/tests/
```

- 采用 `git mv` 而非删除重建，保证历史可追溯
- 同步更新 `README.md:152` 与 `SKILL.md:308` 的路径表述，消除文档矛盾
- 迁移后 `stats` 可正确统计该技能测试覆盖（41 项），TD-009 的统计失真同步修正
- **不应**往 `.gitignore` 加 `.workbuddy/` 后 `git rm --cached`，那会丢失这批资产
- `README.md:256` 的 `../../.workbuddy/research/` 断链需单独处理（删除链接或改为文字说明），**不要**为了修链接而把研究笔记补进仓库

### 关于根目录 `.workbuddy/`

根目录 `.workbuddy/`（存放 `memory/`）当前为 `?? .workbuddy/`，**未被 git 跟踪，且历史上从未跟踪过**，`.gitignore` 亦未包含。建议加入 `.gitignore` 以防误提交——但那与 TD-011 是两个独立问题，不混为一谈。

### 处置状态

- [ ] 待修复（建议 `git mv` 迁移 + 同步修正两处文档表述 + 处理 1 处断链；需用户确认）
- [ ] 建议向作者 `guyue` 确认 `.workbuddy/` 目录名的由来（当前为推断，非结论）

---

## 🟠 TD-015 change-watch 2 个夹具硬编码绝对路径，他人 clone 后必崩

> 2026-10-27 11:44 为回答「TD-011 如何处理」而做迁移可行性验证时发现，**与原问题独立，但更严重**。

- **风险等级**：🟠 **HIGH**（已入库代码在他人环境确定性崩溃，且违反 AGENTS.md 红线 4）
- **位置**：
  - `skills/change-watch/.workbuddy/verify_parse.py:16`
  - `skills/change-watch/.workbuddy/verify_rolled_out.py:2`
- **内容**：`sys.path.insert(0, "/Users/apple/.workbuddy/skills/change-watch/scripts")`
- **已确认入库**：`git show HEAD:<file> | grep "/Users/apple"` 命中，即**任何 clone 者都会拿到**

### 实测证据（三段验证）

**① 本机能跑，是因为本机恰好存有一份旧副本**

`/Users/apple/.workbuddy/skills/change-watch/scripts/watch.py` 真实存在（89343B，2026-10-16），且与仓库副本 diff **完全相同**——所以本机测试通过，问题被完全掩盖。

**② 移除该本机副本后，测试立刻崩溃**

```text
Traceback (most recent call last):
  File ".workbuddy/verify_parse.py", line 17, in <module>
    import watch
ModuleNotFoundError: No module named 'watch'
```

（已在验证后恢复原状，未造成破坏）

**③ 对照：同目录另 2 个夹具写法正确**

| 文件 | 路径定位方式 | 结果 |
| :--- | :--- | :--- |
| `verify_concurrency.py:16` | `os.path.dirname(os.path.abspath(__file__))` 反推 | ✅ 可移植 |
| `verify_linkedin_headers.py` | 同上 | ✅ 可移植 |
| `verify_parse.py:16` | 硬编码 `/Users/apple/...` | 🔴 clone 必崩 |
| `verify_rolled_out.py:2` | 硬编码 `/Users/apple/...` | 🔴 clone 必崩 |

**同一目录下两种写法并存**，说明作者在不同时间点写法不一致，后两个是早期遗留。

### 为何这是 HIGH 而非 LOW

这一点与 TD-011 的性质截然不同：

- TD-011 是「目录放哪儿更好」的**规范问题**，文件本身能用
- TD-015 是「**文件在别人机器上根本跑不起来**」的**功能缺陷**，且违反 AGENTS.md 红线 4（禁止硬编码本机绝对路径）
- 讽刺之处：`SKILL.md:307` 明文写「全目录**不含 `/Users/xxx` 之类的绝对路径**」，而这 2 个文件就在该技能目录下、已入库、正含绝对路径

### 建议修复方式

改法与另 2 个夹具对齐，两行即可：

```python
# verify_parse.py:16 与 verify_rolled_out.py:2 均改为：
SKILL = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(SKILL, "scripts"))
```

**注意**：若同时执行 TD-011 的迁移（`.workbuddy/` → `tests/`），`os.path.dirname` 层级数**不变**（两者同为技能根下的一级子目录），故同一份代码在迁移前后都正确。**建议两件事合并一次提交完成**，避免改两遍。

### 处置状态

- [ ] 待修复（**建议与 TD-011 合并处理**，一次改动同时解决「位置规范」与「路径可移植」）

---

## 🔵 TD-012 `lib/` 空目录与 README 描述不符

- **风险等级**：🔵 LOW（文档准确性）

### 问题明细

- `README.md:58` 声明：`├── lib/  # 📚 共享公共逻辑 (feishu-sdk 等)`
- 实际内容：`lib/.gitkeep` 一个 0 字节占位文件，**无任何代码**
- `git ls-files lib/` 仅返回 `lib/.gitkeep`

### 建议修复方式

二选一：① 修正 README，移除「feishu-sdk」表述或标注「预留目录」；② 若确有共享逻辑规划，落地实现。**建议选 ①**，成本最低且消除误导。

### 处置状态

- [ ] 待修复

---

## 🔵 TD-013 `market-intel` SKILL.md 超渐进披露建议上限

- **风险等级**：🔵 LOW（上下文成本，观察中）

### 问题明细

- `skills/market-intel/SKILL.md` 共 **664 行**（全库最长，其余均 < 330 行）
- 业界建议 SKILL.md 控制在 **500 行以内**（约 5000 字符/约 100 token 广告位之外的主体成本），超出部分应移入 `references/` 按需加载
- 该技能已有 `references/` 目录，具备拆分基础

### 缓解情况

该技能当前状态为「开发中」，且已有 126 个测试用例保障，实际风险可控。属**优化项而非缺陷**，建议在功能稳定后再做拆分。

### 处置状态

- [ ] 观察中（不急于处理）

---

## ⚪ TD-014 工作区存在未提交变更（market-intel v0.5.0）

- **风险等级**：⚪ INFO（需用户决策）

### 现状

```
 M skills/market-intel/SKILL.md
 M skills/market-intel/scripts/core.py
 M skills/market-intel/scripts/diffcore.py
 M skills/market-intel/scripts/judge.py
 M skills/market-intel/tests/test_market_intel.py
 M skills_manifest.json
?? .workbuddy/
```

- 版本已从 `0.4.4` → **`0.5.0`**
- 变更规模：**610 行**（本次扫描期间仍在增长：09:45 为 341 行、10:31 为 610 行）
- 表明有并行开发正在进行

### 处置

按 AGENTS.md 红线 7，Agent **不得自动提交**。待用户下达明确指令后，按「业务技能变更」与「manifest 同步」分离提交：

- `feat(skills/market-intel): ...至 v0.5.0`
- `chore(manifest): 同步 market-intel 元数据至 v0.5.0`

### 处置状态

- [ ] 待用户决策

---

## ✅ 已核查确认无问题的维度（排除误报）

为避免遗漏，以下维度已完成独立验证，结果干净：

| 检查维度 | 结论 |
| :--- | :--- |
| 通用密钥模式（`sk-`/`AIza`/`ghp_`/PEM 私钥） | 仅命中 `tests/test_manage_skills.py` 中的**测试假数据**，真实代码 0 命中 |
| 真实飞书凭证（`bascn*`/`app_*` 前缀） | 0 命中 |
| SKILL.md 相对链接断链 | **0**（证明相对路径方案成熟，支撑 TD-004 修复） |
| Python 脚本语法 | 26 个脚本全部 `py_compile` 通过 |
| `SKILLS.md` vs 磁盘目录 | **16 : 16 完全对齐**，无幽灵条目、无遗漏 |
| `description` 长度 | 全部 ≤ 1024（最大 `market-intel` 409 字符），合规 |
| 技能目录命名 | 全部符合 kebab-case |
| git 跟踪的 `.pyc` | 无，`.gitignore` 对 `__pycache__/`/`*.py[codz]` 配置正确 |
| `manifest` 条目数 | 16，与磁盘一致（仅版本值存在 TD-003 漂移） |
| `wechat-daily-monitor/.env.example` 内容 | 使用占位值 `"xxxx"`，无真实凭证泄露 |
| `AGENTS.md` 自身的 `/Users/...` 出现 | 仅作为「禁止示例」出现在红线 4 中，属正当用法 |

---

## 🗺️ 建议修复路线（按依赖与风险排序）

### 阶段一：止血与澄清

1. **TD-001** — 先到飞书后台核实该表分享范围（唯一需实际确认的安全动作）；随后将 5 个 ID 下沉为环境变量/配置，属规范整改
2. **TD-004** — feishu-crm-copilot 三处路径改相对路径（10 分钟，零风险）

### 阶段二：修工具（让债务可见）

3. **TD-002 + TD-003** — 修复 lint 必填字段盲区、补齐飞书 Token 检测规则、移除 manifest 静默兜底，并补测试
   - ⚠️ 此步完成后 `lint` 将报出约 20 项 Error，属预期；需紧接着做阶段三

### 阶段三：补元数据（机械性批量修复）

4. **TD-005** — 2 个技能补 frontmatter（含新写 description）
5. **TD-006** — 10 个技能补 version/author；统一 change-watch 版本号策略
6. **TD-007** — 15 个技能补 status；调整 `parse_skills_md_defaults` 方向
7. **TD-008** — 清理/迁移非标准字段（需先与作者确认 `alwaysActive` 语义）

### 阶段四：补质量与卫生

8. **TD-010** — 补 3 个 `.env.example`（建议与 TD-001 同批）
9. **TD-011** — change-watch 夹具 `git mv` 迁至 `tests/`，同步修正 README/SKILL.md 两处矛盾表述（迁移后 stats 才能正确统计其 41 项测试）
10. **TD-009** — 按风险补测试：crm-followup-writer → wechat-daily-monitor → example-skill

### 阶段五：文档与优化

11. **TD-012** — 修正 README 的 `lib/` 描述
12. **TD-013** — market-intel SKILL.md 拆分（功能稳定后）

### 提交策略提醒

按 AGENTS.md 红线 8，上述修复**必须拆分独立提交**：单技能变更一技能一提交，`scripts/`、`tests/`、`manifest`、全局文档各自独立，严禁混杂。

---

## 📌 修复记录区

> 修复完成后来此勾销，格式：`- [x] TD-XXX 简述 — 提交 <hash>（YYYY-MM-DD）`

- [x] 原 PF-001 已并入 **TD-004**（2026-10-27 重新编号；问题本身仍存在，尚未修复）

---

## ⚠️ 定级修正记录（避免后续重复误判）

本清单在初次扫描后有 **2 项定级被下调**，原因均为「未充分核实就下结论」。记录在此供后续维护者参考：

| 项目 | 原定级 | 修正后 | 误判原因 |
| :--- | :--- | :--- | :--- |
| TD-001 | 🔴 CRITICAL | 🟡 MEDIUM | 把飞书 `app_token` 误当作凭证。实际它只是资源标识，访问数据需另有三层凭证（App Secret→token→协作者权限），且仓库内无任何 App Secret |
| TD-011 | 「临时脚本误提交」 | 🔵 目录规范问题 | 仅凭 `.workbuddy/` 目录名推断为临时产物，未查提交说明（作者明确写明是为解决「换机器 clone 丢测试」），且建议的 `git rm --cached` 会误删 41 项有效测试 |

**给后续维护者的方法论提醒**：

1. **区分「标识」与「凭证」**。URL 中的资源 ID、app_token、table_id 不等于密钥；判断泄露严重性前先问「拿到它，没有账号/Secret 能做什么」。
2. **下结论前先读提交说明**。`git show <hash>` 与 `git log -1 --format=%B` 往往已给出作者动机，成本极低。
3. **目录名不代表性质**。同名目录（如 `.workbuddy`）可能是作者自建的正式代码位置，需看内容与实际运行结果。
4. **实测优于推断**。本轮把 4 套夹具实际运行（41 项全通过）后，性质判断才准确。

