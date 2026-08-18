---
name: crm-followup-writer
label: 业务一部CRM跟进自动同步
description: '将用户口述或粘贴的客户跟进进展，自动写入 WebEye 业务一部共享 CRM（飞书多维表格「🥳业务一部CRM-2026」的客户跟进记录表）。当用户描述"客户 XXX 同步"、粘贴跟进内容（含下一步/任务执行者/沟通对象等要素）时触发。Skill 内置字段映射、客户简称→record_id 解析、人员 open_id 解析、必填校验与用户身份权限管控（仅用 --as user 写入当前用户的跟进，不越权访问其他业务线数据）。'
---

# 业务一部CRM跟进自动同步

## 概述

把同事口述/粘贴的客户跟进进展，自动解析、校验并写入飞书多维表格 **「🥳业务一部CRM-2026」** 的客户跟进记录表。  
每位同事用自己的飞书身份（`--as user`）写入，只有当前用户作为「内部参与人」或「任务执行者」的字段会引用其 open_id，不越权。

> **目标表**：客户跟进记录表（不是客户管理表）。  
> **权限模型**：共用同一张 base，每位同事的写入权限由 base 自身的字段权限和 base 成员配置决定；本 skill 不做越权绕过。

---

## 何时使用

当用户：
- 描述一次跟进："我的客户是 XXX，今天和 YYY 沟通了……"
- 粘贴跟进内容（包含客户、进展、下一步、@某同事）
- 同步 CRM 进展

当用户：
- 明确说"查询/导出 CRM" → 这是查询场景，请改用 `lark-cli base +record-list` 直接查询，不要走写入 skill
- 要做统计/分析 → 走 `+data-query`
- 要修改/删除已有记录 → 走 `+record-update` / `+record-delete`，本 skill 只做新增

---

## 一次标准写入的工作流

### Step 1：解析用户输入

从用户口述或粘贴的内容中拆出以下字段（缺一不可的标 ⚠️）：

| 字段 | 来源 | 必填 | 说明 |
|------|------|------|------|
| ⚠️ 客户简称 | 用户口述 | 是 | 例如 "CATL"、"微软" |
| ⚠️ 跟进日期 | 用户口述；缺省取今天 | 是 | yyyy/MM/dd |
| ⚠️ 跟进记录 | 用户口述详细进展 | 是 | 自由文本 |
| ⚠️ 跟进总结 | 从跟进记录提炼 | 是 | 一句话总结 |
| ⚠️ 下一步(AI用) | 从跟进记录提炼 | 是 | 结构化下一步，例 "7月底8月初对客户AI做workshop；@侯瑞强 确认GCP香港线路稳定性" |
| 下一步跟进计划 | 用户口述 | 否 | 人类可读版本 |
| 沟通对象 | 用户口述 | 否 | 例 "张继哲（CATL AIDC）" |
| 内部参与人 | 用户口述中 @ 的人 | 否 | 多个 open_id 数组 |
| 任务执行者 | 用户口述中 @ 的人 | 否 | 多个 open_id 数组 |
| 任务完成状态 | 默认 "未完成" | 否 | enum: 未完成 / 已完成 |
| 任务截止时间 | 用户口述 | 否 | yyyy/MM/dd HH:mm |
| 跟进记录链接 | 文档/妙记 URL | 否 | 文本 |
| 同步任务 | 默认勾选 | 否 | checkbox |
| 跟进记录质检 | 留空 | 否 | 由管理者填写 |

**关键规则**：
- **不要写「角色」字段**（`fldMlRsNr6`）。它是只读联动字段，根据"跟进人"自动生成，写入会报 ignored_fields。
- **任务执行者 / 内部参与人** 是 `user` 类型，必须用 open_id（`ou_` 开头），不是人名。
- **客户简称** 是 link 类型，写入时填客户管理表的 record_id（如 `recm2Gv8vj`），不是字符串。
- **open_id 解析** 用 `lark-cli contact +search-user --as user "姓名"`；找不到再向用户确认。

### Step 2：在客户管理表查客户 record_id

```bash
lark-cli base +record-search --as user \
  --base-token "XFl3b5yYvag1dTsksuKcrHbZnYc" \
  --table-id "tblF6xyVs09BdGtA" \
  --field "flduEYDucF" \
  --value "<客户简称>" \
  --format pretty
```

- 若命中唯一 → 取其 `record_id`
- 若命中多个 → 把候选项展示给用户，让用户选
- 若未命中 → 告诉用户"该客户在客户管理表里不存在"，不要写客户管理表（越权）

### Step 3：解析人员 open_id

对「内部参与人」和「任务执行者」中提到的每个人名：

```bash
lark-cli contact +search-user --as user "<姓名>" --format pretty
```

找不到 → 跳过该人，并向用户提示"未找到 XXX 的 open_id，已跳过"，不要伪造。

### Step 4：组装并写入

```bash
lark-cli base +record-batch-create --as user \
  --base-token "XFl3b5yYvag1dTsksuKcrHbZnYc" \
  --table-id "tblGIUssGOxAZ1zM" \
  --json '{
    "records": [
      {
        "fields": {
          "客户简称":           ["<客户record_id>"],
          "跟进日期":           <ms_timestamp>,
          "跟进记录":           "<原始跟进内容>",
          "跟进总结":           "<一句话总结>",
          "下一步(AI用)":       "<结构化下一步>",
          "下一步跟进计划":     "<人类可读下一步>",
          "沟通对象":           "<沟通对象>",
          "内部参与人":         [{"id": "ou_xxx1"}, {"id": "ou_xxx2"}],
          "任务执行者":         [{"id": "ou_xxx"}],
          "任务完成状态":       "未完成",
          "任务截止时间":       <ms_timestamp>,
          "跟进记录链接(云文档/妙计)": "<url>",
          "同步任务":           true
        }
      }
    ]
  }'
```

**关键规则**：
- **必传 `--as user`**：用用户身份写，不绕权限
- **datetime 字段**：用毫秒时间戳（13 位），不是字符串
- **user 字段**：必须是 `[{"id": "ou_xxx"}]` 数组结构
- **link 字段**：必须是 `["<record_id>"]` 数组结构
- **不写「角色」**（会被忽略）

### Step 5：回报结果

回复用户时**必须附 record_id**（来自工具返回），让用户能跳转核对。例如：

```
✅ 已写入：record_id = recvXXXXX
[查看该记录](https://webeye.feishu.cn/base/XFl3b5yYvag1dTsksuKcrHbZnYc?table=tblGIUssGOxAZ1zM&view=vew1ZNEles&record=recvXXXXX)
```

**禁止**："已同步"、"已记录"等无 record_id 的口头承诺。  
**禁止**：在工具调用返回前就告诉用户"已写入"。

---

## 字段映射快速参考（完整版见 references/fields.md）

| 中文名 | field_id | 类型 | 备注 |
|--------|----------|------|------|
| 客户简称 | `fldBzUwffW` | link → 客户管理表 | 必填 |
| 跟进日期 | `fldaoWFCum` | datetime yyyy/MM/dd | 必填 |
| 跟进记录 | `fldkIvPj0V` | text | 必填 |
| 跟进总结 | `fldnV4pSWq` | text | 必填 |
| 下一步(AI用) | `fldH7PrH98` | text | 必填 |
| 下一步跟进计划 | `fld69A4GVM` | text | 可选 |
| 沟通对象 | `fldIBYecej` | text | 可选 |
| 内部参与人 | `fldWWqQlzw` | user (multi) | open_id 数组 |
| 任务执行者 | `fldPkXHJWz` | user (multi) | open_id 数组 |
| 任务完成状态 | `fld0268Jko` | select | 未完成/已完成 |
| 任务截止时间 | `fld0X712fw` | datetime yyyy/MM/dd HH:mm | 可选 |
| 跟进记录链接 | `fldIlQuByo` | text | 可选 |
| 同步任务 | `fldPC10T8z` | checkbox | 默认 true |
| 跟进记录质检 | `fldmusNOGx` | text | 留空 |
| 角色 | `fldMlRsNr6` | select (multi) | **只读联动，不要写** |

> Base 与表的 ID 见 `references/fields.md`。

---

## 一键脚本

复杂场景可使用 `scripts/write_followup.py`，把"解析 + 查客户 + 查 open_id + 写入"打包成一个命令：

```bash
python3 scripts/write_followup.py \
  --customer "CATL" \
  --date "2026-06-26" \
  --content "<跟进内容原文>" \
  --summary "<一句话总结>" \
  --next-action "<下一步(AI用)>" \
  --next-plan "<下一步跟进计划>" \
  --contact "<沟通对象>" \
  --internals "张三,李四" \
  --owners "王五"
```

详见 `scripts/write_followup.py` 顶部 docstring。

---

## 边界与失败处理

| 场景 | 处理 |
|------|------|
| 客户简称在客户管理表查不到 | 提示用户该客户不存在，不要新建（不越权写客户管理表） |
| 客户简称命中多条 | 把候选项列给用户选 |
| 人员 open_id 查不到 | 跳过该人员并提示，不伪造 |
| 工具返回 ignored_fields 含「角色」 | 正常（联动字段忽略），无需处理 |
| 工具返回权限错误 | 提示用户去 base 检查"成员权限"和"字段权限" |
| 用户给的是查询/统计需求 | 改走 `+record-list` / `+data-query`，不调本 skill 的写入流程 |
| 用户给的是修改/删除 | 改走 `+record-update` / `+record-delete` |

---

## 不要做的事

- ❌ 不要写"角色"字段（只读联动）
- ❌ 不要伪造 open_id 或 record_id
- ❌ 不要用 `--as bot` 写（会绕开用户身份）
- ❌ 不要在工具返回前告诉用户"已写入"
- ❌ 不要把客户管理表的字段写入这里（写错了就改不回来了）
- ❌ 不要在 base 找不到/越权时硬写
