---
name: ai-daily-digest
description: >
  统一每日 AI 资讯采集技能：合并新闻简报（TLDR AI, The Rundown AI）和产品发布（Product Hunt, Hacker News, GitHub Trending），
  跨源去重、排序，生成含趋势分析和内容创作建议的统一日报。
  触发词：/ai-daily-digest, "AI资讯汇总", "AI daily digest", "今天的AI新闻和产品"。
version: 1.0.0
---

# AI Daily Digest

统一每日 AI 资讯采集 — 新闻简报 + 产品发布，一份报告搞定。

## 数据源

| 类型 | 来源 | URL |
|------|------|-----|
| 新闻简报 | TLDR AI | `https://bullrich.dev/tldr-rss/ai.rss` |
| 新闻简报 | The Rundown AI | `https://rss.beehiiv.com/feeds/2R3C6Bt5wj.xml` |
| 产品发布 | Product Hunt | `https://www.producthunt.com/feed` |
| 产品发布 | Hacker News | `https://hn.algolia.com/api/v1/search?tags=show_hn&numericFilters=created_at_i>TIMESTAMP` |
| 产品发布 | GitHub Trending | `https://mshibanami.github.io/GitHubTrendingRSS/daily/python.xml` |

## 工作流

1. **检查缓存**: 查找 `data/AI-Daily-Digest/YYYY-MM-DD_AI-Digest.md`，若当天已存在则直接返回。

2. **并行采集所有数据源**: 使用 WebFetch 逐个抓取。HN URL 中的 `TIMESTAMP` 替换为 24 小时前的 epoch 时间。提取 title, link, description, metrics。

3. **过滤**: 仅保留 AI 相关内容（关键词：AI, ML, LLM, GPT, Claude, agent, automation, model, transformer, RAG, fine-tuning）。

4. **跨源去重**:
   - 相同标题或 >80% 词语重叠 → 合并，保留最佳描述，标注所有来源
   - 相同 URL → 合并
   - 相同产品/公司 → 交叉引用

5. **排序**:
   - AI 相关度（关键词密度）
   - 参与度（归一化：PH votes/500, HN points/100, GH stars/1000）
   - 时效性（越新越高）
   - 内容潜力（教程友好、测评价值、开源加分）

6. **生成统一报告**: 格式见 [TEMPLATE.md](TEMPLATE.md)。包含：
   - Top 5 头条（跨源最佳）
   - 新闻简报（按类别：发布、趋势、研究、开发工具）
   - 产品发布（按类别：热门、开发工具、创意工具、开源）
   - 内容创作建议（3-5 个话题含角度）
   - 本周趋势分析（若有 3+ 天历史数据）

7. **保存**: `data/AI-Daily-Digest/YYYY-MM-DD_AI-Digest.md`

## 输出模式

**手动调用**: 展示完整报告。

**被其他工作流调用时**: 返回精简摘要：
```
**AI Daily Digest (N items from M sources):**
- Top: [Headline 1], [Headline 2], [Headline 3]
- Products: [Product 1], [Product 2]
- Content ideas: [Idea 1], [Idea 2]
Full digest: [[YYYY-MM-DD_AI-Digest]]
```

## 内容角度逻辑

- 高参与度 + 教程友好 → "Tutorial opportunity"
- 新颖 + 早期阶段 → "First-mover coverage"
- 开源 + 复杂 → "Deep dive analysis"
- 多源同话题 → "Trend piece"
- 论文 + 实际影响 → "Paper 解读"

## 错误处理

- 单源失败：继续其余源，报告中标注
- <2 个源可用：回退到昨日存档并警告
- 结果为空：生成最小报告标注 "No significant AI news today"

## 趋势分析

当 `data/AI-Daily-Digest/` 中存在 3+ 天的日报时：
- 识别连续 2+ 天出现的话题（持续热点）
- 对比上周关键词频率（上升/下降趋势）
- 标记首次出现的话题（新兴趋势）
