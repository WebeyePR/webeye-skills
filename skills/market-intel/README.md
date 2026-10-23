# market-intel · 市场情报雷达

持续监控行业动态 + 发现潜在客户企业，沉淀为可查询的情报流水、企业档案与线索库，飞书通知。

## 能力

- **行业情报监控**：公共源登记册（RSS / 公众号 / 官网页面监测）→ 差分出新内容 → AI 判断 →
  飞书情报简报 + 每日日报；
- **潜在客户发现**：融资事件、招聘信号等事实聚合到企业档案 → 按「域限定 + 信号组」规则
  （当前：AI/电商/游戏行业内，近期融资且在招、大额融资）确定性匹配打分 →
  带证据链的线索卡推送本人，高分额外群 @；
- **确认闭环**：线索 `new → confirmed/rejected`，驳回记录反哺规则调优；确认线索可落
  飞书多维表格（字段映射已预设，二期激活）。

## 快速开始

```bash
python3 scripts/pipeline.py --init        # 初始化工作区（默认 ~/.market-intel/）
python3 scripts/pipeline.py --baseline    # 建基线（不推送）
python3 scripts/pipeline.py --run         # 采集 + 差分 -> out/latest.json
# AI 按 references/*-criteria.md 判断后：
python3 scripts/judge.py --input judged.json   # 落盘 + 匹配 + 产卡
python3 scripts/query.py --leads         # 检索线索
```

设计、数据契约与陷阱见 [`SKILL.md`](SKILL.md)。

## 与 change-watch 的关系

借鉴其工程模式（差分/待判队列/健康三态/通知纪律），语义保持一致但**不共享代码**；
企业归一与信源分级沿用 AIHOT 的词表与 tier 概念。详细理由见 SKILL.md「心智模型」。
