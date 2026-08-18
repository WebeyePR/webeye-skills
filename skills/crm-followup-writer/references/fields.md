# 字段映射参考

> 本文件是 `SKILL.md` 中字段映射的完整版，包含全部 24 个字段的 ID、类型与备注，方便 lookup。

## Base 与表

| 名称 | 值 |
|------|---|
| Base Token | `XFl3b5yYvag1dTsksuKcrHbZnYc` |
| Base 链接 | https://webeye.feishu.cn/base/XFl3b5yYvag1dTsksuKcrHbZnYc |
| 客户跟进记录表 ID | `tblGIUssGOxAZ1zM` |
| 客户跟进记录表 view | `vew1ZNEles` |
| 客户管理表 ID | `tblF6xyVs09BdGtA` |
| 客户管理表"客户简称"字段 | `flduEYDucF`（text，本表内） |

## 客户跟进记录表（`tblGIUssGOxAZ1zM`）字段全表

| 中文名 | field_id | 类型 | 备注 | 必填 |
|--------|----------|------|------|------|
| 客户简称 | `fldBzUwffW` | link → `tblF6xyVs09BdGtA` | 写入 `["<record_id>"]` | ✅ |
| 跟进日期 | `fldaoWFCum` | datetime yyyy/MM/dd | 毫秒时间戳 | ✅ |
| 跟进记录 | `fldkIvPj0V` | text | 自由文本 | ✅ |
| 跟进总结 | `fldnV4pSWq` | text | 一句话总结 | ✅ |
| 下一步(AI用) | `fldH7PrH98` | text | 结构化下一步 | ✅ |
| 下一步跟进计划 | `fld69A4GVM` | text | 人类可读 | ❌ |
| 沟通对象 | `fldIBYecej` | text | 例 "张继哲（CATL AIDC）" | ❌ |
| 内部参与人 | `fldWWqQlzw` | user (multi) | open_id 数组 | ❌ |
| 任务执行者 | `fldPkXHJWz` | user (multi) | open_id 数组 | ❌ |
| 任务完成状态 | `fld0268Jko` | select | "未完成" / "已完成" | ❌ |
| 任务截止时间 | `fld0X712fw` | datetime yyyy/MM/dd HH:mm | 毫秒时间戳 | ❌ |
| 跟进记录链接(云文档/妙计) | `fldIlQuByo` | text | 文档/妙记 URL | ❌ |
| 同步任务 | `fldPC10T8z` | checkbox | 默认 true | ❌ |
| 跟进记录质检 | `fldmusNOGx` | text | 留空待管理者填写 | ❌ |
| 角色 | `fldMlRsNr6` | select (multi) | **只读联动，不要写** | — |
| 跟进人 | `fld8WvCqNr` | created_by | 自动生成 | — |
| 优化建议 | `fldiiSlbhY` | text | 留空 | ❌ |
| 市场BD | `fldGicw6RP` | lookup | 联动自市场活动 | — |
| 销售 | `fldpWxFeFb` | lookup | 联动自客户管理 | — |
| 售前 | `fldmBkPuH2` | lookup | 联动自客户管理 | — |
| CN Startup Team | `fldaUVHvyx` | lookup | 联动自客户管理 | — |

## 角色字段枚举（`fldMlRsNr6`）

业务一部 / 技术服务 / 业务三部 / BMO / CS BU / CE二组 / 华东华北组 / 华南组 / CE一组 / GTM

> 这些值由系统根据"跟进人"自动联动到对应人员所属组别，**不要手动写**。

## 任务完成状态枚举（`fld0268Jko`）

未完成 / 已完成

## 客户管理表（`tblF6xyVs09BdGtA`）关键字段

| 中文名 | field_id | 类型 | 备注 |
|--------|----------|------|------|
| 客户简称 | `flduEYDucF` | text | 模糊搜索用此字段 |
| 销售 | `fld7xeMp4s` | user | 销售人员 |
| 售前 | `fldbfZeR4R` | user | 售前人员 |
| 售后 | `fldmHafnDl` | user (multi) | 售后人员 |
| CN Startup Team | `fld7CTBDu1` | select | 是/否 |
| 客户来源 | `fld7M6uans` | select | 多个枚举 |
| 客户简介 | `fld79zijmI` | text | 客户业务背景 |
| 城市 | `fldHQXDxBi` | text | 例 "上海" |
| 市场活动细分2026 | `fld502iViI` | link | → `tbl92TSWxYeMyDGg` |

## 关联表

| 表名 | table_id | 用途 |
|------|----------|------|
| 📥 \| 客户管理 | `tblF6xyVs09BdGtA` | 客户主数据 |
| 👤 \| Marketing MQL管理 | `tblCrsFu9zMAXReE`（旧）/ `tbl92TSWxYeMyDGg`（2026） | 市场活动 |
| 🥳 业务一部CRM-2026（跟进记录表） | `tblGIUssGOxAZ1zM` | 本 skill 写入目标 |

## 链接模板

跟进记录跳转链接：
```
https://webeye.feishu.cn/base/XFl3b5yYvag1dTsksuKcrHbZnYc?table=tblGIUssGOxAZ1zM&view=vew1ZNEles&record=<record_id>
```

## 工具调用模板

### 查客户 record_id

```bash
lark-cli base +record-search --as user \
  --base-token "XFl3b5yYvag1dTsksuKcrHbZnYc" \
  --table-id "tblF6xyVs09BdGtA" \
  --field "flduEYDucF" \
  --value "<客户简称>"
```

### 查人员 open_id

```bash
lark-cli contact +search-user --as user "<姓名>"
```

### 写入跟进记录

```bash
lark-cli base +record-batch-create --as user \
  --base-token "XFl3b5yYvag1dTsksuKcrHbZnYc" \
  --table-id "tblGIUssGOxAZ1zM" \
  --json '{
    "records": [
      {
        "fields": {
          "客户简称": ["<客户record_id>"],
          "跟进日期": <ms_timestamp>,
          "跟进记录": "<原文>",
          "跟进总结": "<一句话>",
          "下一步(AI用)": "<结构化>",
          "下一步跟进计划": "<人类可读>",
          "沟通对象": "<对象>",
          "内部参与人": [{"id": "ou_xxx"}],
          "任务执行者": [{"id": "ou_xxx"}],
          "任务完成状态": "未完成",
          "任务截止时间": <ms_timestamp>,
          "跟进记录链接(云文档/妙计)": "<url>",
          "同步任务": true
        }
      }
    ]
  }'
```
