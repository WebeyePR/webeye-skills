# 线索规则：三层模型（rules.json）

## 语义

**命中 = scope 全维成立 AND（任一 signal_group 成立）**

```text
第一层 scope          多维域限定（维度间 AND），所有组共享的硬前提：
                      industry（八行业 any_of）/ origin（中国企业）/ global（业务出海）
                      —— 组间条件耦合的唯一书写位置，禁止复制进组里
第二层 signal_groups  信号组：组间 OR（满足任一组即命中），组内 all_of 是 AND。
                      tier=P0 的组任一命中 → 线索分级 P0（优先建联、实时通知）；
                      仅 P1 组命中 → P1（入池 + 日报，不实时打扰）
第三层 scoring        评分：score = 命中组最高 weight + 每多一组 extra_group_bonus
                      threshold 只决定"通知谁"，不做硬淘汰（宽松 + 人工确认）
```

为什么耦合条件必须只写一处：把"八行业/中国企业/出海"复制进每个组，将来加行业要改 N 处，
漏一处就是规则漏洞。scope 继承让规则加组、加行业都只动一个地方。

## 信号类型（evaluator 注册制）

| type | 条件字段 | 读档案哪个字段 |
|---|---|---|
| `industry_tag` | `any_of` | `industries[]` |
| `funding_event` | `within_days` / `min_amount_cny` / `round_any` | `funding_events[]` |
| `hiring` | `within_days` / `keywords_any` | `hiring_signals[]` |
| `other_event` | `kind_any` / `within_days` / `keywords_any` | `other_events[]`（kind: business_surge / product_launch / rank_new_entry…） |
| `chinese_company` | —（scope 用） | `origin=CN`，兜底启发式（人民币融资/公司主体词/城市词） |
| `going_global` | —（scope 用） | `going_global=true`，兜底关键词扫描（出海/跨境/全球化/海外） |

新增信号类型：在 `scripts/match.py` 加一个 `@evaluator("xxx")` 函数 +
`KNOWN_EVALUATORS` 登记 + 配置里即可使用。求值只读档案，不碰网络、不调模型。

## 关键设计约束（为什么这样设计）

1. **规则匹配的是企业档案（facts），不是原始事件**。事件由 AI 判断阶段抽取入档，
   档案把"180 天内融过资""近 30 天在招"这类跨事件、跨渠道的条件变成可能。
2. **确定性可回测**：同一份档案 + 同一份规则必得同一结论。改规则后可用历史档案回放，
   对比新旧规则的命中差异，再上线。
3. **金额是近似门槛**：`min_amount_cny` 用近似解析值比较（模糊表述取下限、美元按近似汇率），
   只用于筛选，不作为事实输出。
4. **评分排序不淘汰**：score 低于 threshold 只是不通知，流水照记——避免早期规则不成熟
   把好线索静默吃掉。

## 调优闭环

- 误报：把该线索 `status` 改 `rejected` 并在 `notes` 里记一句原因 → 收紧对应组条件或降 weight；
- 漏报：检查档案里事实是否缺失（信源没覆盖）→ 加源，而不是先放宽规则。
