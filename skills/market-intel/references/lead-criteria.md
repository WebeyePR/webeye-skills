# 企业事实抽取准则（facts）

`judged.json` 的 `facts` 数组：把变更里出现的企业事实抽成结构化记录，由脚本并入企业档案。
事实是规则匹配的原料——**抽取质量直接决定线索质量**。

## 记录契约

```json
{
  "entity_name": "公司名（取原文写法）",
  "aliases": ["同一公司的其他称呼"],
  "industries": ["AI"],
  "event_type": "funding | hiring | other",
  "event": { "按类型见下" },
  "change_ids": ["支撑本事实的变更 id"],
  "confidence": "high | medium | low"
}
```

## event 按类型

- **funding**：`{date, round, amount_text, investors, source_name, source_url}`
  - `date` 尽量给 ISO 日期；原文只有"本月"就写当月 1 日并在 confidence 里降级；
  - `amount_text` 保留**原文写法**（中文如"近亿元人民币"，外刊英文如"$30B"、"$200M"均支持）
    ——脚本会近似解析用于门槛比较，但档案里保留原文，不拿近似值当事实；
  - 只有传闻金额、或金额出现在标题党语境里 → confidence 降为 medium/low。
- **hiring**：`{keywords, page_url}`——通常由 web_page 源确定性直录，AI 只需在
  报道类内容提到"该公司大规模招聘"时补充（`{detail}` 摘要即可，入 other_events）。
- **other**：`{kind, detail, source_url}`——并购、合作、政策等暂不参与规则匹配的事实。

## 防张冠李戴（铁律，沿用 AIHOT IDENTITY_LEXICON 的原则）

- **`entity_name` 必须在支撑该事实的变更原文里出现过**，不许凭常识推断"这肯定是某某公司的"；
- 集团与子公司要分清：文中说"阿里云"就写"阿里云"，不要归到"阿里巴巴"除非原文明说；
- 同一公司多个称呼时，`aliases` 填全（原文称呼 + 词表别名），帮助脚本归一；
- 词表里没有的公司：照写原文公司名（脚本会建 `unknown:*` 档案），**不要硬凑到词表里
  相近的名字上**——归并靠词表维护，不靠模型猜测。

## 与 intel 记录的分工

- 一条变更既值得看又含企业事实 → `intel` 和 `facts` 各一条，共享 `change_ids`；
- 一条变更只是线索原料（如融资快讯）不值得单独通知 → 只写 `facts`，不写 `intel`；
- 两者都没有 → `intel` 里写 `discard`。**三选一必须发生，不许留空。**
