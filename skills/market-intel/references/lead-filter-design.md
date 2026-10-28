# 线索前置过滤与调研成本治理 · 设计方案

| 项目 | 内容 |
| :--- | :--- |
| 版本 | v1（设计稿，未实施） |
| 日期 | 2026-10-29 |
| 适用技能 | `skills/market-intel` |
| 触发问题 | 「能不能建一个线索池黑名单，提前过滤无效潜在线索，避免浪费调研补全的时间和资源？」 |
| 结论 | **方向成立，但主体已实现**；真正缺的是「已调研跳过」「池状态回流」「拦截留痕」三块，其中前两块直接决定调研成本 |

---

## 0. 摘要（TL;DR）

1. **线索黑名单不是待建，而是已在运行**：`match.check_denylist()` 在工作区已启用，覆盖 117 个巨头/独角兽、49 个成熟出海服务商、12 类晚期轮次，并含已上市检测；生效位置在**规则求值之前**，因此天然早于 AI 调研。用户设想的「提前过滤无效潜在线索」在主体上已经拿到。
2. **真正在烧调研资源的不是黑名单漏网，而是「已完成的活重做一次」**：生产库里存在精确样本——`光启之境` 同时满足 `status=new`、`bitable_status=exists`、`enrichments 14/14 已齐`，按现有 SKILL 口径（「本轮有新线索 → 调研补全」）仍可能被再次联网调研。这是当前唯一**确定发生**的浪费形态。
3. **建议顺序**：P0 做「调研待办闸」（纯本地计算，30~40 行）；P1 做「拦截留痕」+「名单漂移修复」；P1 再做「池状态回流」（依赖本次实测的 7 个状态选项映射）；**明确不做**「整池当黑名单」（会误杀可认领线索）。

---

## 1. 背景与问题定义

用户观察：调研补全（14 必研字段）依赖联网检索，单家公司成本为分钟级 + 万级 token；若能在线索产出阶段就过滤掉「注定不跟」的公司，可省下这部分开销。

需要回答三个问题：

| # | 问题 | 本文结论 |
| :-- | :--- | :--- |
| Q1 | 现有黑名单覆盖到哪一层？是否已在调研前生效？ | 已覆盖，且在调研前生效（见 §2.2、§2.3） |
| Q2 | 还有哪些「无效线索」没被挡住？ | 三类：已调研、已入池、池内人工判定（见 §2.4） |
| Q3 | 建「线索池黑名单」可行吗？ | 可行但需改造：池状态只能作**派生缓存**，不能当第二事实源（见 §5.2） |

---

## 2. 现状全链路取证

### 2.1 线索从产生到入库的 7 个环节

| # | 环节 | 代码位置 | 已有过滤/护栏 | 是否留痕 |
| :-- | :--- | :--- | :--- | :--- |
| 1 | 事实抽取（建企业档案） | `facts.apply_fact()` | **无黑名单**（黑名单公司也会建档案） | 档案本身即记录 |
| 2 | 逐实体黑名单判定 | `judge.cmd_input` → `match.check_denylist()` | ✅ 实体名/别名 + 轮次 + 已上市 | ❌ **仅 stdout 打印，无持久日志** |
| 3 | 规则求值（八行业+出海+轮次） | `match.evaluate_entity()` | ✅ scope 硬筛 | 部分进 discarded |
| 4 | 产出线索行 | `cmd_input` 写 `log/leads.jsonl` | ✅ 7 天 dedupe（`sha1(entity_id+命中组)`） | leads 本体 |
| 5 | 池状态对齐 | `judge._reconcile_pool_status()` | ✅ 命中回填 `exists` + 压 `notify` | leads 字段 |
| 6 | **AI 调研补全 14 字段** | SKILL 第 10 步（会话内联网检索） | ❌ **无任何机器闸门** | `log/enrichments.jsonl` |
| 7 | 写入猎隼池 | `bitable.build_pending()` → `retryable()` → `commit()` | ✅ 重算黑名单 + `blocked`（14 字段不齐拒写）+ 去重 | `bitable-pending.json`、结果回写 |

**关键结论**：第 6 步（调研）是唯一既昂贵又**没有前置判据**的环节；而第 2 步的黑名单天然在第 6 步之前——所以「黑名单提前过滤」这件事已经成立。

### 2.2 已具备的黑名单能力（实测）

| 维度 | 实测值 | 实现要点 |
| :--- | :--- | :--- |
| 主体名单 | 模板 118 条 / **工作区生效 117 条** | 超级巨头/独角兽（字节、SHEIN、拼多多、阿里、腾讯、米哈游…） |
| 服务商名单 | 49 条 | 成熟出海服务商/友商（声网、钛动、白鲸出海、赤子城…） |
| 轮次否决 | 12 条 + 16 条白名单 | 只挖种子~B；轮次缺失不否决（防误杀） |
| 已上市检测 | 开 | 中英文关键词（含 NASDAQ/NYSE/stock code） |
| CJK 匹配 | 子串 | 中文无词边界（「希音」命中「希音集团」） |
| 拉丁匹配 | 词边界 `(?<![a-z0-9])词(?![a-z0-9])` | v0.6.5 修：旧子串实现实测 14/14 全误伤（`nio` 命中 Onion Games、`meta` 命中 Metaverse Works） |
| 加载优先级 | 工作区 `denylist.json` > 内置模板 | 用户覆盖即生效，无需重启 |

### 2.3 调研成本模型

| 项 | 量级 | 依据 |
| :--- | :--- | :--- |
| 联网检索 | 每公司 2~5 次查询 | 实测两家公司均需多源交叉验证（品牌方舟/界面/企查查/官网） |
| 单公司耗时 | 2~5 分钟 | 同上 |
| 字段门槛 | 14 个必研字段，缺一即 `blocked` 拒写 | `REQUIRED_ENRICH_FIELDS` |
| Token | 万级/家（含搜索结果阅读） | 会话内判定的实测感受 |

### 2.4 三个真实缺口（含生产库实证）

```
leads.jsonl 唯一记录：
  光启之境 | status=new | bitable_status=exists | notify=True | logged_at=2026-10-27T18:19
enrichments.jsonl：
  光启之境 | 字段 14/14 | researched_at=2026-10-27T20:07
```

| 缺口 | 现象 | 后果 | 根因（代码事实） |
| :--- | :--- | :--- | :--- |
| **A. 已调研仍可能重研** | 14/14 已齐 + 已入库，仍显示 `status=new` | 重复联网检索 + 重复 token | 无 `researched_at`/`enrichment_gaps` 守卫；SKILL 只写「本轮有新线索」 |
| **B. 已入池仍标 new** | `bitable_status=exists` 但 `status=new` | 语义上像新线索 | `status` 无条件写 `"new"`；且**全仓库无任何代码消费该字段**（死字段） |
| **C. 池内人工判断无法回流** | 销售在池里标「无商机丢弃/已合作」，本地无感 | 下次照样进候选、照样调研 | `denylist.json` 是静态文件；池状态只在 `_reconcile_digest_leads` 用于**卡片剔除**，未用于**调研前过滤** |

### 2.5 附带发现的三个缺陷

| # | 缺陷 | 证据 | 影响 |
| :-- | :--- | :--- | :--- |
| D1 | **拦截无留痕** | `denied` 变量只在 `cmd_input` 末尾打印到 stdout（judge.py:1850-1853），不落盘；`discarded.jsonl` 533 条中「黑名单」0 条 | 漏杀不可追溯，无法复盘「为什么这家没进来」 |
| D2 | **名单漂移** | 模板 118 条 vs 工作区 117 条，**缺「阿里」** | 由于按 CJK 子串匹配，「阿里云」「阿里妈妈」等不会命中「阿里巴巴」，实际处于**未保护**状态 |
| D3 | **开放状态口径偏窄** | `FOLLOWUP_OPEN_STATUS = ("", "未跟进")` | 池内「待跟进」「已有联系方式」被当作"已有人认领"，在卡片层被整条剔除；对「待跟进」而言语义偏严 |

---

## 3. 池字段实测（方案 B 的输入）

`lark-cli base +field-list` 实测「猎隼线索池」表共 20 字段，其中「跟进状态」为 select，**7 个选项**：

| 池内状态 | 语义 | 建议处理（新线索场景） |
| :--- | :--- | :--- |
| 未跟进 | 在池但无人认领 | **正常调研**（可认领） |
| 待跟进 | 计划跟进、尚未开始 | **正常调研**（建议同时复核 D3 口径） |
| 已有联系方式 | 联系方式已获取，通常已调研过 | **跳过调研**（A 方案会以 enrichments 判据兜住） |
| GTM跟进中 | 销售推进中 | **跳过调研**（避免与销售重复劳动） |
| 已分配销售跟进 | 已指派 | **跳过调研** |
| 无商机丢弃 | 明确不做 | **硬过滤**（等价黑名单） |
| 已合作 | 已是客户 | **硬过滤**（不再作为新线索） |

其他字段选项（写入时的合法值，供实现参照）：

- 所属赛道（7）：AI应用 / 内容创作 / 具身智能 / AI基础设施 / AI硬件 / 智能制造 / 电商
- 融资动态（19）：含 Pre-A+轮 / 天使+轮 / 战略融资 / 种子轮 …（无 E 轮及以后，与「只挖种子~B」一致）
- 企业规模（6）、成立年限（17）、曝光媒体/合作渠道（50）

---

## 4. 结论

1. **可行性：高**，但不是「新建黑名单」，而是**补齐调研前的判据**与**把池的既有判断接进来**。
2. **优先级应重排**：黑名单（Q1）已达标；真正省钱的是 A/B/C（§2.4）。
3. **风险边界**：任何「提前过滤」都必须留痕（D1），否则会把「目标客户永久消失」变成不可发现的错误（参见 `_word_hit` 的 v0.6.5 误伤教训）。

---

## 5. 方案设计

### 5.1 设计原则（五条，不可妥协）

| # | 原则 | 说明 |
| :-- | :--- | :--- |
| P1 | **留痕优先** | 任何丢弃都要写入 `log/denied.jsonl`（含 entity、原因、命中词、来源环节） |
| P2 | **宁重勿漏** | 只过滤「确定不跟」（竞对/服务商/晚期/已上市/无商机/已合作）；「看起来不相关」交给判定层 `notify=false` 归档 |
| P3 | **单一事实源** | 池是人工判断的唯一来源；本地文件只能是**可重建的派生缓存**（`state/pool_filter.json`），禁止双写 |
| P4 | **不改数据契约** | 不新增必填字段、不改 leads/intel 既有键语义；新增一律可选字段 |
| P5 | **失败不阻断** | 任何 API/IO 失败降级为「按现状继续」，绝不阻断产卡/入池 |

### 5.2 方案 A（P0）：调研待办闸 `--research-todo`

**目标**：把「要不要调研」从 AI 的口头判断变成可执行判据。

- 命令：`python3 <技能目录>/scripts/judge.py --research-todo`
- 判据（全部满足才进待办）：
  1. `retryable(lead)` 为真（即 `bitable_status ∉ {pushed, exists, filtered}`）；
  2. `enrichment_gaps(enrichments[entity_id])` 非空（14 字段有缺口）；
  3. 未命中 `check_denylist`（复用现有函数，防名单在产出后被追加）。
- 输出：stdout 人类可读清单 + `out/research-todo.json`：

```json
{
  "generated_at": "2026-10-29T01:40:00+08:00",
  "items": [
    {
      "entity_id": "unknown:某公司",
      "entity_name": "某公司",
      "priority": "P0",
      "missing_fields": ["funding", "investors"],
      "reason": "enrichment_incomplete"
    }
  ],
  "skipped": [
    {"entity_name": "光启之境", "reason": "already_in_pool", "detail": "bitable_status=exists"},
    {"entity_name": "某某科技", "reason": "enrichment_complete", "detail": "14/14"}
  ],
  "exit_code_rule": "有待办=0，无待办=0（空清单不是错误）"
}
```

- **SKILL 流程改动**（第 10 步）：调研前先跑该命令，**只调研 `items` 内的企业**；`skipped` 需在汇报里点名（避免"悄悄少调研"）。
- 降级：`enrichments.jsonl` 缺失/损坏 → 视为全缺（回到现状行为）。
- 预期收益：直接消灭 §2.4 缺口 A/B；在当前数据下即避免对「光启之境」的重复调研。
- 工作量：`judge.py` +35 行、复用 `bitable.retryable/enrichment_gaps`、无外部依赖。
- 风险：低。唯一注意点是「`exists` 但确有字段缺失」的线索会被跳过——这符合「已在池就不再调研」的口径，且如需补研可加 `--research-todo --include-exists` 逃生门。

### 5.3 方案 B（P1）：池状态回流为派生过滤

**目标**：把池里的人工判断（无商机丢弃/已合作/跟进中）变成调研前可执行的过滤。

- 数据流（单向，不双写）：

```
池（事实源） --record-search/record-field--> state/pool_filter.json（派生缓存，可随时重建）
                                              ↓
                         check_denylist 之外的「第二道池过滤」
```

- 缓存结构：

```json
{
  "version": 1,
  "synced_at": "2026-10-29T02:00:00+08:00",
  "hard_block": [
    {"entity_name": "某公司", "record_id": "recXXX", "status": "无商机丢弃", "note": "人工标丢弃"}
  ],
  "skip_research": [
    {"entity_name": "某公司二", "record_id": "recYYY", "status": "GTM跟进中"}
  ]
}
```

- 刷新策略：仅在 `--research-todo` / `--input` 产线索前，对**候选实体（通常 ≤3 家）**按公司名检索池；命中硬过滤状态记入缓存；**不做全表同步**（省 API，避免大表遍历）。
- 硬过滤与软跳过的边界（依据 §3 实测）：

| 池状态 | 处理 | 是否进 denylist |
| :--- | :--- | :--- |
| 无商机丢弃 / 已合作 | 硬过滤（不产出、不调研、不上卡） | 进派生 `hard_block` |
| GTM跟进中 / 已分配销售跟进 | 软跳过（不调研，但保留线索记录） | 只进 `skip_research` |
| 未跟进 / 待跟进 / 已有联系方式 | 正常 | 不进 |

- 失败降级：API 失败 → 用上次缓存；无缓存 → 按现状（不过滤）。
- 工作量：约 50 行（`record-search` 已有封装 `_search_company`）+ 3 条测试。
- 风险：中。状态值拼写必须与池选项**逐字一致**（本次实测已固化 7 个值）；建议做成常量表 + 单测防漂移。

### 5.4 方案 C（P1，独立小改）：拦截留痕

- `denied` 从「打印」改为「写 `log/denied.jsonl` + 打印」：

```json
{"logged_at": "2026-10-29T02:10:00+08:00", "stage": "input",
 "entity_id": "x", "entity_name": "声网", "reason": "命中黑名单（成熟出海服务商/友商）：声网",
 "matched_word": "声网", "change_ids": ["..."]}
```

- 生命周期：属运行时产物，`--clean` 自动清（不在 `CLEAN_KEEP` 内），与 `intel/discarded` 一致。
- 工作量：约 15 行 + 2 条测试。
- 收益：可审计、可统计拦截率（当前该数据完全缺失）。

### 5.5 方案 D（P1，数据修复）：名单漂移

- 工作区 `denylist.json` 缺「阿里」（模板有）。虽然「阿里巴巴」在名单内，但 CJK 子串匹配下「阿里云」不会命中「阿里巴巴」。
- 处理：把工作区名单与模板做一次对齐（以模板为准，保留工作区自定义项），并在 `--research-check` 或 `pipeline --run` 启动时做一次**只读一致性检查**，发现模板新增条目时告警（不自动覆盖，防误伤用户自定义）。

### 5.6 方案 E（P2，评估后暂不做）

| 项 | 为什么暂不做 |
| :--- | :--- |
| **把整个池当黑名单** | 会误杀「在池但未认领」的可认领线索——这正是三层语义中的第二层（v0.6.17 已定：exists+未跟进应保留灰标） |
| **在 facts 阶段就过滤黑名单** | 收益小（建档案是本地计算，几乎零成本），且会让「被黑名单公司」失去档案留痕，反而降低可解释性 |
| **改 `status` 字段语义** | 该字段全仓库无消费者（死字段）；建议后续**废弃**而非改造，避免制造新的语义债 |
| **把 `FOLLOWUP_OPEN_STATUS` 扩到「待跟进」** | 属卡片展示口径调整，与调研成本无关；需单独评估（D3），不混入本次 |

---

## 6. 兼容性与回滚

| 项 | 结论 |
| :--- | :--- |
| 与 `--bitable --commit` 的关系 | A/B 只影响**调研入口**，不改写入链；写入链仍五重护栏（黑名单重算/14 字段/去重/选项校验/blocked 留痕） |
| 与 `--digest` 的关系 | 无交集（digest 只读 intel/leads 的展示字段） |
| 与洁净重置的关系 | 新增 `state/pool_filter.json`、`log/denied.jsonl` 属派生/运行时产物，`--clean` 自动清，不污染 `CLEAN_KEEP` |
| 契约扩展 | 仅新增可选 CLI 与可选输出文件，不改 intel/leads/discarded 既有键 |
| 回滚 | 三项均为「加闸」而非「改数据」：回滚 = 停止调用新命令，数据无损 |

---

## 7. 测试计划

| 方案 | 用例 | 断言 |
| :--- | :--- | :--- |
| A | 新线索 + 字段缺 | 出现在 `items` |
| A | `exists` 线索 | 出现在 `skipped`（reason=already_in_pool），不调研 |
| A | 14/14 齐全 | `skipped`（reason=enrichment_complete） |
| A | 命中黑名单 | `skipped`（reason=denylist） |
| A | enrichments 文件缺失 | 全部视为待办，不崩 |
| B | 池状态=无商机丢弃 | 进 `hard_block`，不产出线索 |
| B | 池状态=GTM跟进中 | 进 `skip_research`，线索保留 |
| B | 池状态=未跟进 | 不进任何过滤 |
| B | 池 API 失败 | 用缓存 / 不过滤，不阻断 |
| C | 黑名单命中 | `log/denied.jsonl` 追加一行，含 matched_word |
| D | 工作区名单少于模板 | 启动告警提示缺失条目，不自动改写 |

预估：+11~13 条，测试总数 291 → 302~304。

---

## 8. 实施顺序（原子提交）

| 顺序 | 提交 | 内容 | 版本 |
| :-- | :--- | :--- | :--- |
| 1 | `feat(skills/market-intel): 新增调研待办闸 --research-todo` | 方案 A + 测试 | 0.6.21 |
| 2 | `fix(skills/market-intel): 黑名单拦截留痕到 denied.jsonl` | 方案 C + 测试 | 0.6.22 |
| 3 | `fix(skills/market-intel): 补齐工作区黑名单缺失项并加一致性告警` | 方案 D | 0.6.22 |
| 4 | `feat(skills/market-intel): 池状态回流为派生过滤（硬过滤/软跳过）` | 方案 B + 测试 | 0.6.23 |
| 5 | `docs(skills/market-intel): SKILL 采纳调研待办闸并沉淀陷阱` | 流程与新坑 | 0.6.23 |

---

## 9. 验收标准（可执行断言）

1. `--research-todo` 对当前生产库输出：`items` 为空、`skipped` 含「光启之境（already_in_pool）」；
2. 构造一家「字段缺 2 项 + 未入池」的公司，`items` 恰好包含它；
3. 黑名单命中后 `log/denied.jsonl` 有对应行，`discarded.jsonl` 不受影响；
4. 名单一致性检查能报出「阿里」缺失，且**不自动修改**用户文件；
5. 全量测试保持全绿，lint 0 Errors，manifest 同步。

---

## 10. 附录：取证命令

```bash
# 黑名单规模与结构
python3 -c "import json;d=json.load(open('config/denylist.json.example'));print({k:len(v) for k,v in d.items() if isinstance(v,list)})"

# 运行侧是否启用
python3 -c "import sys;sys.path.insert(0,'scripts');import match;d=match.load_denylist('$HOME/.market-intel');print(len(d['deny_entities']))"

# 黑名单调用点
grep -rn "check_denylist" scripts/*.py

# 拦截是否留痕（结果：仅 stdout）
grep -n "denied" scripts/judge.py

# 线索与调研现状
python3 -c "import json;print([json.loads(l) for l in open('$HOME/.market-intel/log/leads.jsonl')])"

# 池字段选项（真实读取）
lark-cli base +field-list --base-token <app_token> --table-id <table_id> --as user
```