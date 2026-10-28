#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""判断落盘与线索产出：judged.json -> 流水 / 企业档案 / 规则匹配 / 飞书卡片。

分工沿用 change-watch 的核心设计：
- 脚本（确定性）：覆盖度校验、落盘、档案聚合、规则求值、卡片生成——可复现、可回测；
- 模型（AI）：读 out/latest.json 的变更集，按 references/intel-criteria.md 与
  references/lead-criteria.md 判断，产出 judged.json——这是判断，不是查表。

judged.json 契约（两类记录并存，change_id 是对账主键）：
{
  "intel": [ {"change_id", "type", "significance": high|normal|low|unknown,
              "title", "summary", "url", "sources": [{"name","url"}],
              "notify": bool, "notify_reason", "merged_change_ids"?,
              "discard"?: true, "discard_reason"? } ],
  "facts": [ {"entity_name", "aliases"?, "industries"[], "event_type": funding|hiring|other,
              "event": {...}, "change_ids"[], "confidence": high|medium|low } ]
}

铁律（沿用 change-watch）：漏答一条 = 判断不完整，覆盖度校验会点名并退出码 2；
unknown 落盘不通知；凡通知必先判断；卡片由脚本生成，直接发送、不要手拼。
"""

import argparse
import json
import os
import re
import sys
import time

import core
import diffcore
import delivery
import facts as facts_mod
import match as match_mod

INTEL_KEYS = {"change_id", "type", "significance", "object", "title", "summary", "url",
              "sources", "notify", "notify_reason", "merged_change_ids",
              "discard", "discard_reason", "activity", "number_notes",
              "suppressed_by_guard", "merged_titles", "merged_records",
              "sales_playbook", "sales_strategy"}
NOTIFY_KEYS = {"version", "notify", "digest", "bitable"}
NOTIFY_BLOCK_KEYS = {"user_id", "chat_id", "mention", "hero_image"}

SIG_LABEL = {"high": ("重要", "red"), "normal": ("常规", "violet"), "low": ("次要", "neutral")}
SIG_ORDER = {"high": 0, "normal": 1, "low": 2}
PRIORITY_LABEL = {"P0": ("P0 · 优先建联", "red"), "P1": ("P1 · 储备", "blue")}


def load_notify(ws):
    rec = core.jload(os.path.join(ws, "notify.json"), None)
    if rec is None:
        rec = core.jload(os.path.join(core.SCRIPT_DIR, "..", "config", "notify.json.example"), None)
    if not rec:
        return {"notify": {}, "bitable": {}}
    core.check_keys(rec, NOTIFY_KEYS, "notify.json")
    if "notify" in rec:
        core.check_keys(rec["notify"], NOTIFY_BLOCK_KEYS, "notify.notify")
    return rec


def mention_md(cfg):
    """notify.mention -> 飞书卡片 @ 标记。all=<at id=all></at>；列表=逐个 @；空=空串。
    语法沿用 change-watch 群卡实测（card-status-*-group.json）。"""
    m = (cfg or {}).get("mention")
    if m == "all":
        return "<at id=all></at>"
    if isinstance(m, list) and m:
        return " ".join("<at id=%s></at>" % u for u in m)
    return ""


def _mention_lead(text, cfg):
    """群卡首行的 @ 提醒元素（mention 配置为空则不产）。"""
    at = mention_md(cfg)
    if not at:
        return None
    return {"tag": "markdown", "content": "%s %s" % (text, at)}


# ---------- grounding：说明里的数字必须能在变更原文里找到 ----------

# 换算兜底系数：单位换算（亿/万、B/M 与中文亿万的互转）+ 美元近似汇率 7.2。
# 说明文字里的"300亿美元 ← $30B"、"3万人 ← 30,000"属合法换算，不算虚构。
_CONV_FACTORS = (1e-4, 1e-2, 0.1, 1.0, 10.0, 1e2, 1e4, 7.2, 1 / 7.2)


def _conv_variants(values):
    """由原文数字生成常见换算候选集合（容差比较用）。"""
    out = set()
    for v in values:
        for k in _CONV_FACTORS:
            c = v * k
            out.add(round(c, 2))
            if abs(c - round(c)) < 1e-9:
                out.add(int(round(c)))
    return out


_NUM_TOKEN_RE = re.compile(r"\d+(?:\.\d+)?")


def _claim_numbers(text):
    """抽取「数值主张」：剔除紧邻 ASCII 字母的短数字片段（那是标识符，不是数量）。

    2026-10-28 实测（第二次同类假阳性）：裸正则 `\\d+(?:\\.\\d+)?` 把 `2026Q3` 的 3、
    `3C` 的 3、`5G` 的 5、`4K` 的 4 都当成独立数字，于是 grounding 对「季度/品类/
    网络制式/清晰度」缩写反复误报。**假阳性会训练人忽略告警，比漏报更危险**，故收窄口径：
      - 只排除「整数部分 ≤2 位且紧邻 ASCII 字母」的片段（Q3 / 3C / 5G / 4K / B2B / 5.5G）；
      - 中文单位不是 ASCII 字母，`5亿元`、`17%`、`250亿澳元`、`3万` 照常核对；
      - 三位以上数字不排除：`H200` 的 200、`1050` 这类型号/金额写错必须仍被抓到。
    注意：`src_nums` 仍用宽松抽取（原文是证据全集），只有 probe 这一侧收窄。
    """
    out = []
    for m in _NUM_TOKEN_RE.finditer(text):
        s = m.group(0)
        prev = text[m.start() - 1] if m.start() > 0 else ""
        nxt = text[m.end()] if m.end() < len(text) else ""
        glued = ((prev.isascii() and prev.isalpha()) or (nxt.isascii() and nxt.isalpha()))
        if glued and len(s.split(".")[0]) <= 2:
            continue
        out.append(s)
    return out


def grounding_check(rec, change):
    """title/summary 里的数字串应出现在该变更的原文（title+summary）里。

    三层判定（AI 协助申报 + 脚本换算兜底）：
    1. 数字直接出现在原文 → 通过；
    2. AI 在 rec["number_notes"] 里申报了换算依据（如 {"300": "300亿美元=$30B 换算"}）
       → 通过，备注留档（换算是否合理由人工复核，脚本不判断对错）；
    3. 命中常见换算系数（×10、÷10000、×7.2 汇率等）→ 通过，自动备注留档；
    4. 都不满足 → 返回该数字（调用方标 unverified 并告警）。

    返回 (unverified_numbers, grounded_notes)：grounded_notes 供落盘审计。
    """
    src_text = ((change or {}).get("title") or "") + " " + ((change or {}).get("summary") or "")
    # probe 只查说明（summary）：title 是「原样带回」契约（陷阱 23），AI 不改写；
    # 且页面监测源的 title 尾部带动态计数器（阅读/热度数，2026-10-27 实测
    # 449→463 漂移），复用拷回场景必产生误报告警——title 不参与数字核对。
    probe = rec.get("summary") or ""
    if change is None:
        # 变更已滚出本轮窗口（复用拷回常见）：无证据可查，跳过核对（告警无意义）
        return [], {}
    src_nums = set(re.findall(r"\d+(?:\.\d+)?", src_text))
    declared = rec.get("number_notes") or {}
    variants = _conv_variants({float(x) for x in src_nums})
    grounded_notes, unverified = {}, []
    for n in set(_claim_numbers(probe)) - src_nums:
        if n in declared:
            grounded_notes[n] = str(declared[n])
        elif _near(n, variants):
            grounded_notes[n] = "换算兜底（单位/汇率系数命中原文数字）"
        else:
            unverified.append(n)
    return sorted(unverified), grounded_notes


def _near(num_str, variants):
    """数字串是否落在换算候选集合的容差范围内。"""
    try:
        v = float(num_str)
    except ValueError:  # pragma: no cover
        return False
    return any(abs(v - c) <= max(0.01, abs(c) * 1e-6) for c in variants)


# ---------- 重复内容兜底检查（v0.6.7） ----------
#
# 用户实测反馈（2026-10-27）：「卡片里很多信息实际是重复的，虽然标题和文案有些不一样，
# 但是实际表达内容是一致的，AI 协助和兜底就能很好的处理，为什么还会重？」
#
# 事实核对：`references/intel-criteria.md` 早就写了「同一事件多家报道：合并成一条 intel，
# merged_change_ids 列出被合并的 change_id，只通知一次」——**契约是有的，是我判定时没执行**。
# 所以这不是脚本 bug，是执行纪律问题。但用户说得对：「兜底」应该存在。
#
# 兜底设计取舍（重要）：
#   **只告警、不自动删**。原因：
#     ① 判断「两篇是否同一事件」是语义问题——「DeepSeek 降价」与「DeepSeek 登顶开源榜首」
#        都含 DeepSeek 但是两件事，机械合并就是错改；
#     ② 自动删会造成静默漏发，比重复更严重（用户的原始诉求是"尽量不漏"）；
#     ③ 脚本能做的是**把候选挑出来**，让人/模型 3 秒内确认，而不是替它做判断。
#   这与本技能既有纪律一致：脚本负责确定性的"指出来"，判断留给判断层。
_DUP_STOP = frozenset("""的 了 和 与 及 在 是 为 将 已 被 对 从 到 等 新 版 发布 上线 宣布
    元 亿 万 千 百 月 日 年 第 大 小 高 低 多 少 中 内 外 前 后""".split())


def _title_tokens(title):
    """标题 → 用于判重的特征集合（中文二元组 + 拉丁词 + 数字）。

    为什么不用整串相似度：中文标题改写时语序和虚词都会变（「DeepSeek 新模型上线，
    价格更低」vs「DeepSeek V4.1 Flash 新版本」），整串相似度会偏低而漏判；
    抽取「实体词 + 关键数字」二元组更稳。
    """
    t = core.norm(title or "").lower()
    toks = set()
    for w in re.findall(r"[a-z][a-z0-9.+-]{1,}", t):
        toks.add(w)
    for num in re.findall(r"\d+(?:\.\d+)?", t):
        toks.add("#" + num)
    han = re.findall(r"[\u4e00-\u9fff]+", t)
    for run in han:
        if len(run) == 1:
            continue
        for i in range(len(run) - 1):
            bg = run[i:i + 2]
            if bg not in _DUP_STOP:
                toks.add(bg)
    return toks


# 型号/通用词：参与"是否同一事件"判断会大量误报，只用于展示、不作锚点
_MODEL_NOISE = frozenset("""
ai pro max mini plus ultra flash nano turbo lite beta alpha preview released
says said new update version model models feature features launch launches
the and for with from that this will has have been are was its their
""".split())

# 轮次/融资术语：是"事件"的一部分而不是"主体"，混进实体集会误判
# （实测：「Ambi 完成 Pre-A 轮」与「橘猫宇宙 完成 Pre-A 轮」因共有 pre-a 被判重复）
_ENT_STOP = frozenset("""
pre-a pre-b pre-a+ pre-b+ series seed angel ipo pre-ipo
""".split())


def _title_entities(title):
    """标题里的「主体实体」：拉丁专名（剔除产品线后缀、通用词、轮次词）。

    为什么要剔除：`gpt-6`、`k3.1`、`pro`、`ai` 这类是**型号/通用词**，
    「Kimi K3.1 泄露」和「DeepSeek 斩杀 GPT-6」都会带 gpt-6 或 pro，
    拿它们当锚点会把两件不同的事判成重复（实测误报）。
    `pre-a`/`series a` 是轮次词，混进来会让两家不同公司的 Pre-A 融资互相判重（实测误报）。
    主体只保留**品牌/公司/产品名**：deepseek、kimi、meta、qwen、anthropic…
    """
    t = core.norm(title or "").lower()
    ents = set()
    for w in re.findall(r"[a-z][a-z0-9.+-]*[a-z0-9]|[a-z]{2,}", t):
        w = w.strip(".")
        if not w or len(w) < 2:
            continue
        if w in _MODEL_NOISE or w in _ENT_STOP:
            continue
        if re.fullmatch(r"[a-z]+", w) and len(w) <= 2:
            continue
        ents.add(w)
    return ents


def _title_event(text):
    """标题里的「事件类型」标签集合。

    这是判重能否可用的**决定性维度**：同一个主体可以有多件不同的事——
      「Kimi 发布 K3.1 Pro」 vs 「Kimi K3.1 泄露成本数据」→ 主体同、事件不同，**不是重复**；
      「DeepSeek 新模型上线」 vs 「DeepSeek V4.1 Flash 新版本」→ 主体同、事件同，**是重复**。
    只比主体必然误报（实测 Meta 被罚 vs Meta 合作、Kimi 发布 vs Kimi 泄露 全被误判）。

    词表按「营销/情报」语义归类，不追求语言学完备——宁缺勿滥：
    漏掉一个词最多是少提示一次，多归一类会造成误报噪音。
    """
    t = core.norm(text or "").lower()
    tags = set()
    for kw, tag in _EVENT_KWS:
        if kw in t:
            tags.add(tag)
    return tags


_EVENT_KWS = [
    # 发布/上新（含媒体惯用口语：「掀桌」「踢馆」「杀到」都指发新模型抢市场）
    ("发布", "launch"), ("上线", "launch"), ("推出", "launch"), ("开源", "launch"),
    ("新版本", "launch"), ("首发", "launch"), ("正式发布", "launch"),
    ("掀桌", "launch"), ("踢馆", "launch"), ("新模型", "launch"), ("新版本", "launch"),
    ("迭代", "launch"), ("升级", "launch"),
    # 价格
    ("降价", "price"), ("涨价", "price"), ("上调", "price"), ("下调", "price"),
    ("价格", "price"), ("性价比", "price"), ("免费", "price"),
    # 融资/资本
    ("融资", "funding"), ("轮融资", "funding"), ("估值", "funding"),
    ("上市", "ipo"), ("招股", "ipo"), ("ipo", "ipo"), ("并购", "ma"), ("收购", "ma"),
    # 风险/合规
    ("泄露", "leak"), ("罚款", "penalty"), ("处罚", "penalty"), ("禁令", "ban"),
    ("制裁", "ban"), ("下架", "ban"), ("起诉", "legal"), ("诉讼", "legal"),
    ("合规", "compliance"), ("监管", "compliance"),
    # 经营/人事
    ("裁员", "layoff"), ("离职", "hr"), ("招聘", "hiring"), ("任命", "hr"),
    ("财报", "earnings"), ("营收", "earnings"), ("利润", "earnings"), ("亏损", "earnings"),
    # 合作/生态
    ("合作", "partner"), ("战略投资", "partner"), ("入驻", "partner"),
    # 榜单/数据
    ("登顶", "rank"), ("榜首", "rank"), ("榜单", "rank"), ("新晋", "rank"),
    ("下载量", "metric"), ("月活", "metric"), ("用户数", "metric"),
]


def find_duplicate_intel(records, min_entity=0.5):
    """找出「疑似同一事件的多条 intel」，返回 [(主条, 疑似重复条, 说明)]。

    **双维度判据**（缺一不可）：
      ① 主体覆盖：`|实体交集| / |较短一侧实体数| ≥ min_entity`
      ② 事件一致：两边的**事件类型标签有交集**（发布/降价/融资/泄露/罚款…）

    四次口径迭代的完整教训（全部是实测打出来的，不要退回去）：
      ① 整串特征重合度 → 早报把多事件塞进一条标题，特征被稀释，
         「DeepSeek 新模型上线…」vs「iPhone Duo 交付；Kimi K3.1 Pro 发布…」重合度仅 0.36 → 漏判；
      ② 锚点含型号词（gpt-6/k3.1/pro）→「Kimi K3.1 泄露」与「DeepSeek 斩杀 GPT-6」
         因共有 gpt-6 被判重复 → 误报；
      ③ 只比主体实体 →「Kimi 发布」与「Kimi 泄露」主体都是 kimi，覆盖率 1.0 → 误报；
         「Meta 被罚」与「Meta 合作」同理 → 误报；
      ④ 现在：**主体 + 事件类型**双维度。这才是真正区分「同一件事被多家报道」
         与「同一家公司发生了两件不同的事」的判据。

    只用来提示候选，**不修改任何记录、不阻断落盘**。
    """
    live = [r for r in records if not r.get("discard") and r.get("title")]
    feats = []
    for r in live:
        ents = _title_entities(r.get("title"))
        ev = _title_event(r.get("title"))
        if ents:
            feats.append((ents, ev, r))
    out = []
    for i in range(len(feats)):
        ei, vi, ri = feats[i]
        for j in range(i + 1, len(feats)):
            ej, vj, rj = feats[j]
            common = ei & ej
            if not common:
                continue
            cover = len(common) / float(min(len(ei), len(ej)))
            if cover < min_entity:
                continue
            # 事件类型必须有交集；两边都判不出事件时按"不重复"处理（保守不乱报）
            shared_ev = vi & vj
            if not shared_ev:
                continue
            out.append((ri, rj, "%.2f/%s" % (cover, "/".join(sorted(shared_ev)))))
    return out


def duplicate_clusters(intel):
    """把重复候选聚成簇：返回 [[记录...], ...]，每簇 ≥2 条。

    为什么要聚簇而不是只列对：实测一轮里 DeepSeek 有 4 条（专稿+早报+评测+榜单），
    两两配对会给出 6 个组合，人看着是噪音；按连通分量聚成 1 簇才好判断"这 4 条其实是一件事"。
    """
    dups = find_duplicate_intel(intel)
    if not dups:
        return []
    by_id = {}
    for ri, rj, _ in dups:
        by_id[id(ri)] = ri
        by_id[id(rj)] = rj
    parent = {k: k for k in by_id}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for ri, rj, _ in dups:
        a, b = find(id(ri)), find(id(rj))
        if a != b:
            parent[a] = b
    groups = {}
    for k, rec in by_id.items():
        groups.setdefault(find(k), []).append(rec)
    return [g for g in groups.values() if len(g) > 1]


def suggest_merges(intel):
    """生成「合并建议」列表（**只写候选文件，不改 intel**）。

    输出可供人直接改判定的最小信息：主条 change_id + 建议合并进它的 change_id 列表，
    以及每条的一行摘要（含 URL，便于判断哪条信息更全该当主条）。
    """
    clusters = duplicate_clusters(intel)
    out = []
    for group in clusters:
        # 主条选择：significance 高者优先，其次 res 级别（标题短者更像是专稿而非早报聚合）
        def rank(r):
            sig = {"high": 0, "normal": 1, "low": 2}.get(r.get("significance"), 3)
            return (sig, len(r.get("title") or ""))
        ordered = sorted(group, key=rank)
        main, rests = ordered[0], ordered[1:]
        out.append({
            "keep": main.get("change_id"),
            "keep_title": main.get("title"),
            "keep_url": main.get("url"),
            "merge_ids": [r.get("change_id") for r in rests],
            "merge_titles": [r.get("title") for r in rests],
        })
    return out


# 套话模板（v0.6.9）：这些话对任意条目都成立，等于没提供信息。
# 用户实测原话：「榜单监控的每个内容都很短，都是『xxxx。需人工核实公司主体与融资轮次后
# 决定是否建联。』，而且信息 AI 不都会调研补全的吗？」
_BOILERPLATE = [
    "需人工核实公司主体与融资轮次后决定是否建联",
    "需人工核实公司主体",
    "属榜单新面孔",
    "供了解竞对能力与价格结构，本轮不单独通知",
    "供区域判断与话术积累，本轮不单独通知",
    "与八行业出海客户无关或信息量不足，归档留痕",
]


def warn_boilerplate(intel):
    """检测「套话摘要」：同一句模板话套在多个条目上，等于没写。

    为什么要在代码里拦：我在 v0.6.7 之前的判定里给 4 条榜单写了**完全相同的**
    "出现在 AI 产品榜，属榜单新面孔。需人工核实公司主体与融资轮次后决定是否建联。"，
    而详情页明明白白有月活/环比/名次/榜单项可查。这不是数据不可得，是判定偷懒。
    光在准则里写「必须调研」不够——**写得再好也会被跳过，必须让脚本叫出来**。

    判定口径：`notify=true` 的条目里，摘要命中套话模板 或 长度 < min_len（默认 25 字），
    即视为可疑。只告警不阻断（内容质量无法机械判死，最终仍由人/模型判断）。
    """
    suspicious = []
    for r in intel:
        if r.get("discard") or not r.get("notify"):
            continue
        s = (r.get("summary") or "").strip()
        hit = next((b for b in _BOILERPLATE if b in s), None)
        if hit or len(s) < 25:
            suspicious.append((r, hit or "摘要过短(%d字)" % len(s)))
    if not suspicious:
        return 0
    sys.stderr.write(
        "\n[boilerplate] %d 条要通知的内容摘要疑似套话/过短——这类话对任意条目都成立，"
        "等于没提供信息。\n"
        "   榜单类必须写详情页能查到的客观数据（榜单项/指标/环比/名次），"
        "见 references/intel-criteria.md 「榜单新晋必须调研出实质信息」。\n" % len(suspicious))
    for r, why in suspicious[:10]:
        sys.stderr.write("   · [%s] %s\n     摘要: %s\n     问题: %s\n"
                         % (r.get("change_id"), (r.get("title") or "")[:44],
                            (r.get("summary") or "")[:70], why))
    if len(suspicious) > 10:
        sys.stderr.write("   … 还有 %d 条\n" % (len(suspicious) - 10))
    return len(suspicious)


def warn_activity_highlights(intel, min_len=45):
    """检测「活动看点偷懒」：notify=true 的活动 highlight 过短或空泛。

    为什么要在代码里拦（2026-10-27 实测）：同批活动 10-26 判定时的看点 60~76 字
    （含括号细节与行动建议，如「短剧出海工具链（生成、译制、分发）供应商」），
    10-27 洁净重置复判时缩水到 30~38 字套话（如「可作为生态伙伴拓展场景」）——
    详情页数据没变，是判定偷懒。活动看点是市场同事决定「去不去这个活动」的
    直接依据，套话等于没写。与 warn_boilerplate 同纪律：只告警不阻断，
    但把缺口写进 stderr 让判定侧必须回应。

    口径：notify=true 且带 activity 明细的记录，highlight 缺失或 < min_len
    （默认 45 字，与 10-26 良性判定 40~76 字的下沿对齐）即告警。
    notify=false 归档条目不检查。
    """
    suspicious = []
    for r in intel:
        if r.get("discard") or not r.get("notify"):
            continue
        # 只检查活动情报：activity 明细仅活动类记录才有语义，战略风向/技术战报等
        # 类型没有 activity 字段是**正常形态**而非"缺失"（2026-10-28 实测误报：
        # Genie 4 等非活动条目被点名"看点缺失"——检查范围写宽了）。
        if r.get("type") != "活动情报":
            continue
        a = r.get("activity") or {}
        hl = (a.get("highlight") or "").strip()
        if not hl or len(hl) < min_len:
            suspicious.append((r, len(hl)))
    if not suspicious:
        return 0
    sys.stderr.write(
        "\n[activity-highlight] %d 条 notify=true 活动看点过短（<%d字）或缺失——"
        "看点是市场同事决定去不去的直接依据，套话等于没写。\n"
        "   必须写「面向谁、讲什么、为什么值得去、建议动作」，详情页可查的"
        "报名规模/主题细节应带上（参照 activities-criteria.md 的契约）。\n" % (len(suspicious), min_len))
    for r, n in suspicious[:10]:
        a = r.get("activity") or {}
        sys.stderr.write("   · [%s] %s\n     当前看点(%d字): %s\n"
                         % (r.get("change_id"), (a.get("name") or r.get("title") or "")[:44],
                            n, (a.get("highlight") or "(缺失)")[:70]))
    return len(suspicious)


def warn_duplicates(intel, ws=None):
    """落盘后提示疑似重复，写 stderr（不阻断落盘、不删记录）。

    传 ws 时额外落一份 `out/dup-suggestions.json`，给出可直接照抄的合并方案——
    光在 stderr 打印「请人工确认」不够，人还得自己找 change_id、判断谁当主条；
    把这两件事算好写进文件，确认成本从"翻日志"降到"看一眼改一个字"。
    """
    dups = find_duplicate_intel(intel)
    if not dups:
        return 0
    clusters = duplicate_clusters(intel)
    sys.stderr.write(
        "\n[dup] 检测到 %d 簇疑似同一事件（共涉及 %d 条，标题措辞不同但指向同一件事）。\n"
        "      契约要求合并成一条：主条 `merged_change_ids` 列出被合并的 change_id，只通知一次。\n"
        "      **这是提示不是错误**：确认同一事件就按建议合并重跑，是两件事就忽略。\n"
        % (len(clusters), sum(len(c) for c in clusters)))
    for i, group in enumerate(clusters, 1):
        sys.stderr.write("  簇%d（%d 条）:\n" % (i, len(group)))
        for r in group:
            sys.stderr.write("     - [%s] %s\n" % (r.get("change_id"), (r.get("title") or "")[:60]))
    if ws:
        sug = suggest_merges(intel)
        path = os.path.join(ws, "out", "dup-suggestions.json")
        core.jdump(path, {"generated_at": core.now_iso(), "clusters": sug})
        sys.stderr.write("  合并建议已写入: %s（照它改 judged.json 的 merged_change_ids 即可）\n" % path)
    return len(dups)


# ---------- 覆盖度校验 ----------

def coverage_check(latest, intel, fact_records, already_judged=None):
    """每个 change_id 必须被 intel（含 merged/discard）、facts.change_ids 或
    历史登记（log/intel.jsonl + log/discarded.jsonl）覆盖。返回缺失列表。

    历史登记视为已判：覆盖度防的是「窗口内有条目从未被任何一轮判过」，
    而不是强迫每次输入重答全量——facts 补录、增量判定等合法场景的条目
    若此前轮次已落盘，不应再拦（实测 facts-only 输入被误拦退出码 2）。
    """
    changes = (latest or {}).get("changes") or []
    covered = set(already_judged or set())
    for r in intel:
        if r.get("change_id"):
            covered.add(r["change_id"])
        for mid in r.get("merged_change_ids") or []:
            covered.add(mid)
    for f in fact_records:
        for cid in f.get("change_ids") or []:
            covered.add(cid)
    missing = [c["change_id"] for c in changes if c["change_id"] not in covered]
    return missing


# ---------- 卡片生成（沿用 notify-card.md 的骨架与纪律） ----------
#
# 视觉差异纪律：market-intel 通知卡用紫色系（violet 头 + report 图标 + violet 块），
# 与 change-watch 的蓝色铃铛监控卡一眼区分；语义色不动——红=强提醒/P0、绿=P1/正常、
# 黄=异常。改样式前先在临时测试群实测渲染，再发正式群。

INTEL_ICON = {"tag": "standard_icon", "token": "report_outlined"}  # 官方图标枚举，实测渲染 ok

CARD_CONFIG = {
    "schema": "2.0",
    "config": {"update_multi": True, "width_mode": "default",
               "style": {"text_size": {
                   "title": {"default": "heading-2", "pc": "heading-2", "mobile": "heading-3"},
                   "body": {"default": "normal", "pc": "normal", "mobile": "normal"},
                   "caption": {"default": "notation", "pc": "notation", "mobile": "notation"}}}},
}


def _block(md_lines, color="violet", last=False):
    els = [m if isinstance(m, dict) else {"tag": "markdown", "content": m} for m in md_lines]
    return {"tag": "interactive_container", "width": "fill", "has_border": True,
            "border_color": color + "-100", "corner_radius": "8px", "background_style": color + "-50",
            "padding": "12px 12px 12px 12px", "vertical_spacing": "4px",
            "margin": "0px 0px 12px 0px" if not last else "0px",
            "elements": els}


def build_card(title, subtitle, blocks, template="violet", tags=None, icon=None):
    header_tags = [{"tag": "text_tag", "text": {"tag": "plain_text", "content": text}, "color": color}
                   for text, color in (tags or [])]
    header = {"title": {"tag": "plain_text", "content": title},
              "subtitle": {"tag": "plain_text", "content": subtitle},
              "template": template,
              "text_tag_list": header_tags}
    if icon:  # 缺省不带图标，行为与旧版一致
        header["icon"] = icon
    return {
        **CARD_CONFIG,
        "header": header,
        "body": {"direction": "vertical", "padding": "12px 12px 20px 12px", "elements": blocks},
    }


def _sources_line(sources):
    if not sources:
        return ""
    first = sources[0]
    more = "、[{}]({})".format(*[sources[1]["name"], sources[1]["url"]]) if len(sources) > 1 else ""
    extra = " 等%d个来源" % len(sources) if len(sources) > 2 else ""
    return "<font color='grey'>来源 · [{name}]({url}){more}{extra}</font>".format(
        name=first.get("name", "来源"), url=first.get("url", ""), more=more, extra=extra)


def intel_blocks(records, limit=5):
    """情报块：重要 -> 常规 -> 次要。

    **notify=true 全部展示、不截断**（v0.6.5 修，陷阱 58）：本函数是**情报卡**的
    渲染路径，而情报卡里的记录按定义**全是 notify=true**（`cmd_input` 只把
    notify=true 的塞进 `notified_intel`，`cmd_digest` 的 simple 降级分支更是直接
    取窗口内记录）。旧实现 `ordered[:limit]` 硬截断 5 条——9 条通知情报只渲染 5 条，
    且末行只写"另有 4 条次要动态"（把它们错标成"次要"，实际是**已判要通知的重要
    内容**）。锚点一推进，这些内容下期不再出现 → 判定成功但永久漏发。
    陷阱 47 只修了内参卡的活动/新闻/榜单三段，**情报卡这条路径漏了**——
    与陷阱 48「同类问题要一次查遍所有兄弟位置」完全同型复发。
    `limit` 仅约束 notify=false 的归档记录（情报卡正常不会传入，留作通用兜底）。
    """
    ordered = sorted(records, key=lambda r: (0 if r.get("notify") else 1,
                                             SIG_ORDER.get(r.get("significance"), 3)))
    must = [r for r in ordered if r.get("notify")]
    shown = must + [r for r in ordered if not r.get("notify")][:limit]
    blocks = []
    for r in shown:
        sig_label, sig_color = SIG_LABEL.get(r.get("significance"), ("常规", "violet"))
        head = "<text_tag color='%s'>%s</text_tag> <text_tag color='neutral'>%s</text_tag> **%s**" % (
            sig_color, sig_label, r.get("type") or "动态", r.get("title") or "")
        parts = [head, r.get("summary") or ""]
        # 销售弹药：情报卡同样必须带（2026-10-27 用户明令"缺少真正的销售弹药"）。
        # 这条渲染路径独立于 _news_elements——当初只改了内参卡会漏掉情报卡，
        # 用户看到情报卡就看不见销售建议（实测缺陷）。
        if r.get("sales_playbook"):
            parts.append("**【销售对标指引】** %s" % r["sales_playbook"])
        if r.get("sales_strategy"):
            parts.append("**【销售战略建议】** %s" % r["sales_strategy"])
        parts.append(_sources_line(r.get("sources") or []))
        blocks.append(_block(parts))
    if not blocks:
        return blocks, 0
    if len(ordered) > len(shown):
        blocks.append({"tag": "markdown",
                       "content": "<font color='grey'>另有 %d 条动态（notify=false 归档）</font>"
                                  % (len(ordered) - len(shown)),
                       "text_size": "notation"})
    else:
        blocks[-1]["margin"] = "0px"
    return blocks, len(ordered)


def lead_pool_url(lead, view_url):
    """线索定位 URL：已入池（有 record_id）跳到表格里那条记录；否则回退表视图。"""
    rid = (lead or {}).get("bitable_record_id")
    if rid and view_url:
        return "%s&record=%s" % (view_url, rid) if "?" in view_url \
            else "%s?record=%s" % (view_url, rid)
    return view_url


def lead_exists(ld):
    """该公司此前已在猎隼线索池（写入层 exists 分支回写 bitable_status=exists）。

    语义纪律：已存在的记录绝不能再以「新潜在线索」的红色 P0 面孔推送——
    私信卡标注「已存在」，且不进群 @（群卡只催全新线索建联，重复轰炸=骚扰）。
    """
    return (ld or {}).get("bitable_status") == "exists"


def _reconcile_pool_status(ws, entity_names):
    """产卡前按公司名对飞书池查重，回填本地丢失的 bitable_status（陷阱 71）。

    背景（2026-10-27 实测）：bitable_status 只由 --bitable --commit 写入层回写，
    洁净重置/手工清理 leads.jsonl 后本地状态丢失，而飞书池记录还在——
    卡片把昨天刚入池的公司当全新 P0 再推（写入层查重兜底不重复建行，
    但卡面语义失真、且要等 commit 才发现）。

    只查规则命中的候选（通常 ≤3 家，别处不调用，API 预算可控）；
    lark-cli 不可用/网络失败时静默返回空（保持旧行为，不阻断产卡——
    写入层 _search_company 仍是防重复建行的最终兜底）。
    返回 {norm(公司名): {"record_id": …}}。
    """
    try:
        import bitable as bitable_mod
    except Exception:  # noqa: BLE001
        return {}
    cfg = (load_notify(ws).get("bitable") or {})
    if not cfg.get("enabled"):
        return {}
    hits = {}
    for name in entity_names:
        if not name:
            continue
        try:
            rid = bitable_mod._search_company(name, cfg)
        except Exception:  # noqa: BLE001 — 网络/权限/CLI 缺失：静默降级，不阻断产卡
            return hits
        if rid:
            hits[core.norm(name)] = {"record_id": rid}
    if hits:
        sys.stderr.write("[pool-reconcile] %d 家候选已在猎隼池，卡面将标「已存在」: %s\n"
                         % (len(hits), "、".join(sorted(hits.keys()))))
    return hits


def lead_card_split(leads):
    """线索卡分流：(fresh, exists)。群卡 @ 只催 fresh；exists 只在私信卡中性标注带过。"""
    leads = list(leads or [])
    return ([l for l in leads if not lead_exists(l)],
            [l for l in leads if lead_exists(l)])


# 「未开始跟进」的跟进状态集合：只有这些状态允许继续在卡上展示。
# 池字段 options（references/bitable-fields.md）：未跟进/待跟进/GTM跟进中/已合作…
# 语义：已有销售认领的线索对市场团队不再是行动项，重复展示=噪音，整条剔除。
FOLLOWUP_OPEN_STATUS = ("", "未跟进")


def _reconcile_digest_leads(ws, leads):
    """产卡前对窗口内线索做池状态对齐 + 已跟进线索剔除（陷阱 74）。

    为什么必须有这一道（2026-10-28 实测）：
      `--input` 路径有四道拦截（本地 dedupe / 黑名单 / _reconcile_pool_status / notify 压制），
      但 `--digest` 产卡走的是**另一条路径**——只做 in_window 过滤，不查池。
      于是「锚点未推进期间（如洁净重置后一直未 commit）窗口内历史线索」会每轮重复进卡：
      实测光启之境（10-27 落盘的 notify=true + exists 历史组合）连续多轮出现在内参里。

    规则（两级，均为"命中即收敛"，失败一律保持原样不阻断产卡）：
      ① 窗口内 notify=true 且**无** bitable_status 的线索 → 按公司名查池；
         命中则回填 bitable_status=exists + record_id，并压 notify=False
         （与 v0.6.15 的入池语义对齐，卡面走灰色「已登记」而非红色 P0）。
      ② bitable_status=exists 且有 record_id 的线索 → 读池内「跟进状态」；
         状态不属于 FOLLOWUP_OPEN_STATUS（即已有销售认领）→ **整条剔除**，
         不再占用内参版面（用户 2026-10-28 明确要求）。

    成本控制：只查窗口内线索（正常推进时通常 0~2 条），每条约 1.5s；
    lark-cli 不可用/网络失败时静默跳过（保持旧行为）。
    """
    if not leads:
        return leads
    cfg = (load_notify(ws).get("bitable") or {})
    if not cfg.get("enabled"):
        return leads
    try:
        import bitable as bitable_mod
    except Exception:  # noqa: BLE001
        return leads

    kept = []
    reconciled = 0
    dropped_followed = []
    for ld in leads:
        status = (ld.get("bitable_status") or "")
        # ① 无池状态的新线索：查池回填（等价于 --input 路径第③道）
        if ld.get("notify") and not status:
            try:
                rid = bitable_mod._search_company(ld.get("entity_name") or "", cfg)
            except Exception:  # noqa: BLE001 —— 网络/权限/CLI 缺失：保持原样
                kept.append(ld)
                continue
            if rid:
                ld = dict(ld)
                ld["bitable_status"] = "exists"
                ld["bitable_record_id"] = rid
                ld["notify"] = False
                reconciled += 1
        # ② 已在池的线索：读跟进状态，已被认领的整条剔除
        if ld.get("bitable_status") == "exists" and ld.get("bitable_record_id"):
            try:
                follow = bitable_mod.record_field(ld["bitable_record_id"], "跟进状态", cfg)
            except Exception:  # noqa: BLE001
                follow = None
            if follow is not None and follow not in FOLLOWUP_OPEN_STATUS:
                dropped_followed.append("%s（%s）" % (ld.get("entity_name") or "?", follow))
                continue
        kept.append(ld)
    if reconciled:
        sys.stderr.write("[digest-reconcile] %d 条窗口内线索已在线索池，按「已存在」展示\n"
                         % reconciled)
    if dropped_followed:
        sys.stderr.write("[digest-reconcile] %d 条线索已被销售认领（跟进状态非未跟进），"
                         "不进入内参: %s\n" % (len(dropped_followed), "、".join(dropped_followed)))
    return kept


def _active(leads):
    """过滤掉已被当前规则淘汰（bitable_status=filtered）的线索——它们没入池，
    不该出现在卡片里，更不能声称"已登记线索池"。"""
    return [l for l in leads if (l.get("bitable_status") or "pending") != "filtered"]


def lead_blocks(leads, limit=5, view_url=None):
    """线索卡（私信/群）：同 `_lead_elements` 的截断纪律（v0.6.5）。

    notify=true 全展示；notify=false 才按 limit 收敛——线索卡若漏了 P0，
    群卡就是"催建联却不给人名"，现场无法跟进。
    """
    leads = _active(leads)
    must = [l for l in leads if l.get("notify")]
    shown = must + [l for l in leads if not l.get("notify")][:limit]
    blocks = []
    for ld in shown:
        pri_label, pri_color = PRIORITY_LABEL.get(ld.get("priority") or "P1", PRIORITY_LABEL["P1"])
        if lead_exists(ld):
            # 已在线索池：中性标注置前、violet 块——不是新线索，是重复提示
            head = ("<text_tag color='neutral'>已存在·已在线索池</text_tag> "
                    "<text_tag color='%s'>%s</text_tag> <text_tag color='neutral'>%s</text_tag> "
                    "**%s** · 匹配度 %d" % (
                        pri_color, pri_label, "/".join(ld.get("industries") or []) or "未分类",
                        ld.get("entity_name") or "未命名", ld.get("score", 0)))
        else:
            head = "<text_tag color='%s'>%s</text_tag> <text_tag color='neutral'>%s</text_tag> **%s** · 匹配度 %d" % (
                pri_color, pri_label, "/".join(ld.get("industries") or []) or "未分类",
                ld.get("entity_name") or "未命名", ld.get("score", 0))
        rules_line = "命中规则：" + "；".join(ld.get("matched_group_names") or ld.get("matched_groups") or [])
        ev_lines = [e.get("detail") for e in (ld.get("evidence") or []) if e.get("detail")][:3]
        body = rules_line + ("：" + "；".join(ev_lines) if ev_lines else "")
        els = [head, body]
        if ld.get("profile"):
            els.append("业务描述：%s" % ld["profile"])
        if ld.get("opportunity"):
            els.append("**最大机会：%s**" % ld["opportunity"])
        els.append(_sources_line(ld.get("sources") or []))
        pool_url = lead_pool_url(ld, view_url)
        if pool_url:
            els.append(_button("已在线索池·查看既有记录" if lead_exists(ld) else "🔍 查看线索",
                               pool_url, size="small"))
        blocks.append(_block(els, color="violet" if lead_exists(ld)
                             else ("red" if ld.get("priority") == "P0" else "green")))
    if len(leads) > len(shown):
        blocks.append({"tag": "markdown",
                       "content": "<font color='grey'>另有 %d 条归档线索（notify=false）</font>"
                                  % (len(leads) - len(shown)),
                       "text_size": "notation"})
    if blocks:
        blocks[-1]["margin"] = "0px"
    return blocks


def save_leads_latest(ws, new_leads, now, touched):
    """落盘待处理线索视图（out/leads-latest.json）。

    防覆盖丢失：上一轮视图里尚未入池（status=new 且无 record_id）的线索并入本轮，
    否则连续两轮判断时前一轮待办会被静默覆盖（流水虽可查，但待办视图丢失会漏闭环）。
    结转前必须回查 leads.jsonl 的最新写入状态：视图是落盘时刻的快照，其间的
    --bitable --commit 可能已把线索推送入池——按快照结转会把已推送的线索再次
    当待办展示，造成重复通知（2026-10-26 文远知行实例）。
    touched：本轮触及实体集合（或其数量）。
    """
    prev_latest = core.jload(os.path.join(ws, "out", "leads-latest.json"), None) or {}
    cur_by_id = {l.get("lead_id"): l for l in
                 core.read_jsonl(os.path.join(ws, "log", "leads.jsonl"))}
    fresh_keys = {l.get("dedupe_key") for l in new_leads}
    carried = []
    for l in (prev_latest.get("leads") or []):
        if l.get("status") != "new" or l.get("record_id"):
            continue
        if l.get("dedupe_key") in fresh_keys:
            continue
        cur = cur_by_id.get(l.get("lead_id")) or {}
        if (cur.get("bitable_status") or "pending") != "pending":
            continue  # 期间已推送/已存在/已淘汰：不再是待办，不重复通知
        carried.append(l)
    n_matched = touched if isinstance(touched, int) else len(touched or [])
    core.jdump(os.path.join(ws, "out", "leads-latest.json"),
               {"run_at": now, "leads": list(new_leads) + carried,
                "summary": {"new_leads": len(new_leads), "carried_unpooled": len(carried),
                            "matched_entities": n_matched}})
    return carried


# ---------- 卡片体积护栏（飞书硬上限，v0.6.6） ----------
# 官方限制（2026-10-27 核实，两处来源一致）：
#   ① 卡片/富文本消息请求体 **最大 30KB**（错误码 230025 / 200860 "card size exceeds limit"）
#   ② JSON 2.0 结构单卡最多 **200 个元素或组件**（错误码 300305）
# 来源：open.feishu.cn/document/server-docs/im-v1/faq（"建议 JSON 长度不超过 30KB"）
#      open.feishu.cn/document/cardkit-v1/card-element/create（300305 明确 200 个）
#
# 为什么必须有这道护栏（2026-10-27 实测，本技能自己的回归）：
#   v0.6.5 把 notify=true 从「截断」改成「全部展示」——方向是对的（防永久漏发），
#   但**没有同时处理体积**，导致单张内参卡膨胀到 39.6KB（compact 序列化）、
#   177 个元素，**双双超过飞书硬上限**，发送必然报 230025 失败。
#   而这个失败发生在**锚点推进之后**（`--digest --commit` 先产卡后推锚），
#   内容被标记已通报 → 永久丢失。这比原来的截断更糟：截断至少发出去了。
#
# 设计取舍：**分片而不是截断**。
#   用户明令 notify=true 不许漏发；销售指引是必填内容不能砍。
#   两者与 30KB 上限的冲突只能靠「拆成多张卡」解决。
CARD_MAX_BYTES = 30000      # 30KB 上限，留 720 字节余量给外层信封
CARD_MAX_ELEMENTS = 190     # 200 元素上限，留 10 个余量给 header/分片标记


def card_bytes(card):
    """按飞书实际发送形态（compact 序列化）计算字节数。"""
    return len(json.dumps(card, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


def card_element_count(card):
    """统计元素/组件总数（含 interactive_container 内嵌的子元素）。"""
    n = 0

    def walk(o):
        nonlocal n
        if isinstance(o, dict):
            if "tag" in o:
                n += 1
            for k, v in o.items():
                if k in ("elements", "columns", "fields"):
                    walk(v)
                elif isinstance(v, (dict, list)) and k not in ("text", "fields"):
                    walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)

    walk((card.get("body") or {}).get("elements") or [])
    return n


def split_card(card, max_bytes=CARD_MAX_BYTES, max_elements=CARD_MAX_ELEMENTS):
    """把超限的卡片按 body.elements 顺序切成多张，保证每张都在上限内。

    切分纪律：
      - 只在**顶层 elements 边界**切（不在容器内部切，避免半个块）；
      - 每片继承原 header，标题追加「（1/3）」分片标记，便于读者判断完整性；
      - 单片就超限（单个元素比上限还大）→ 该元素单独成卡并在 stderr 告警，
        不静默丢弃（宁可它自己报错，也不让内容消失）；
      - 返回 [card] 表示无需切分（调用方按原样发送）。
    """
    if not card:
        return [card]
    els = (card.get("body") or {}).get("elements") or []
    if not els:
        return [card]
    if card_bytes(card) <= max_bytes and card_element_count(card) <= max_elements:
        return [card]

    # 贪心装箱：逐元素累加，超限前收一片
    chunks, cur = [], []
    for el in els:
        trial = cur + [el]
        probe = {**card, "body": {**(card.get("body") or {}), "elements": trial}}
        if cur and (card_bytes(probe) > max_bytes or card_element_count(probe) > max_elements):
            chunks.append(cur)
            cur = [el]
        else:
            cur = trial
    if cur:
        chunks.append(cur)

    out = []
    total = len(chunks)
    for i, chunk in enumerate(chunks, 1):
        sub = {**card, "body": {**(card.get("body") or {}), "elements": chunk}}
        if total > 1:
            hdr = dict((card.get("header") or {}))
            t = dict(hdr.get("title") or {})
            t["content"] = "%s（%d/%d）" % (t.get("content") or "", i, total)
            hdr["title"] = t
            sub["header"] = hdr
        size = card_bytes(sub)
        if size > max_bytes or card_element_count(sub) > max_elements:
            sys.stderr.write(
                "[card-size][warn] 第 %d/%d 片仍超限（%d 字节 / %d 元素，上限 %d/%d）——"
                "单个内容块过大，飞书可能拒收，请缩短该条正文\n"
                % (i, total, size, card_element_count(sub), max_bytes, max_elements))
        out.append(sub)
    return out


def _flatten(items):
    """展平 write_card 的返回值（path 字符串 / 分片 path 列表），并过滤 None。

    过滤 None 是必要的：`cmd_digest` 在 briefing 渲染返回 None（全空不产卡）时
    会得到 `_flatten([None])`——若不过滤就成了 `[None]`，布尔为真，
    会**跳过 simple 降级分支**导致一张卡都不产（2026-10-27 重构时实测踩到）。
    """
    out = []
    for it in items or []:
        if isinstance(it, (list, tuple)):
            out.extend(x for x in it if x)
        elif it:
            out.append(it)
    return out


def write_card(ws, name, card):
    """写卡唯一出口：先过体积护栏，超限自动分片（v0.6.6）。

    返回**path 或 path 列表**（兼容历史单张语义：未超限时仍是字符串）。
    分片文件名插入 `-p2`/`-p3` 后缀，保持字典序与片序一致。
    """
    parts = split_card(card) if card else [card]
    if len(parts) == 1:
        path = os.path.join(ws, "out", name)
        core.jdump(path, parts[0])
        return path
    base, ext = os.path.splitext(name)
    paths = []
    for i, part in enumerate(parts, 1):
        p = os.path.join(ws, "out", "%s-p%d%s" % (base, i, ext))
        core.jdump(p, part)
        paths.append(p)
    sys.stderr.write(
        "[card-size] %s 超飞书上限（30KB / 200 元素），已自动切成 %d 张，"
        "**全部都要发送**，否则分片内容会丢失（防漏发优先于少发消息）\n"
        % (name, len(parts)))
    return paths


# ---------- 内参卡（需求文档版式：活动日历 / 行业重点新闻 / 榜单监控 / 潜在客户与业务情报） ----------

# 头图与图标 img_key 复用需求文档卡片 JSON 里的既有素材（飞书 image key 租户级通用）
BRIEFING_IMGS = {
    "banner": "img_v3_0212n_1afbec4c-f128-436a-8e46-3f4a29fcd41g",
    "icon": "img_v3_027m_91fb8fc2-e6a3-406c-aa9d-04569e29bccg",
}


def _upload_hero_image(local_path):
    """上传本地图片到飞书换取 image_key（im:resource scope，user 身份与发卡身份一致）。

    失败返回 None（调用方回退默认头图，不阻断产卡）。规范：≤10M、高:宽 ≤16:9。
    """
    import subprocess
    cmd = ["lark-cli", "im", "images", "create", "--as", "user",
           "--data", '{"image_type":"message"}', "--file", local_path]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    except Exception as exc:  # noqa: BLE001
        sys.stderr.write("[hero] 上传命令执行失败: %s\n" % exc)
        return None
    out = proc.stdout or ""
    try:
        payload = json.loads(out[out.find("{"):out.rfind("}") + 1])
        if payload.get("ok") is False:
            raise ValueError(payload.get("error") or "ok=false")
        key = (payload.get("data") or {}).get("image_key")
        if not key:
            # 兼容顶层直接返回 image_key 的响应形态
            key = payload.get("image_key")
        if not key:
            raise ValueError("响应未含 image_key: %s" % out[:200])
        return key
    except Exception as exc:  # noqa: BLE001
        sys.stderr.write("[hero] 上传响应解析失败: %s | stderr: %s\n"
                         % (exc, (proc.stderr or "")[:200]))
        return None


_IMG_KEY_RE = re.compile(r"img_v\d+[0-9a-z]*_[0-9A-Za-z_-]+")


def _extract_image_key(text):
    """从任意文本（如飞书图片分享链接、混排消息）中提取 image_key。

    同一租户内飞书图片链接与裸 key 等价（image_key 租户级通用）。
    """
    if not text:
        return None
    m = _IMG_KEY_RE.search(text)
    return m.group(0) if m else None


def _download_image(url, dst_path):
    """下载图片到本地（≤10M）。失败抛异常，由调用方兜底回退默认头图。

    本机回环地址旁路系统代理（代理通常不转发 127.0.0.1，会 502）。
    """
    import urllib.request
    handlers = []
    host = (urllib.request.urlparse(url).hostname or "").lower()
    if host in ("127.0.0.1", "localhost", "::1"):
        handlers.append(urllib.request.ProxyHandler({}))
    req = urllib.request.Request(url, headers={"User-Agent": "market-intel/1.0"})
    opener = urllib.request.build_opener(*handlers)
    with opener.open(req, timeout=30) as resp:
        data = resp.read(10 * 1024 * 1024 + 1)
    if not data or len(data) > 10 * 1024 * 1024:
        raise ValueError("图片为空或超过 10M")
    if len(data) < 100:
        raise ValueError("响应过小，疑似非图片内容")
    with open(dst_path, "wb") as f:
        f.write(data)


def resolve_banner_key(ws, hero_image=None):
    """解析内参头图 img_key。优先级：CLI --hero-image > notify.hero_image > 代码默认 None。

    智能识别输入形态（2026-10-27 用户要求：充分发挥 AI/脚本智能兜底，不呆板）：
    - 裸 image_key（img_v…）→ 直通；
    - 飞书图片链接（内嵌 image_key，同租户）→ 提取 key 直通；
    - http(s) 图片链接 → 下载临时文件后上传（URL 与内容双级缓存）；
    - 本地路径 → 上传（文件 sha1 缓存）；
    - 其他混合文本 → 尽力提取内嵌 key；仍失败回退默认图，不阻断产卡。
    缓存统一放 state/hero_images.json，同链接/同图绝不重复上传。
    """
    src = hero_image
    if not src:
        src = (load_notify(ws).get("notify") or {}).get("hero_image")
    if not src:
        return None
    src = str(src).strip()
    if src.startswith("img_"):
        return src
    import hashlib
    cache_path = os.path.join(ws, "state", "hero_images.json")
    cache = core.jload(cache_path, {})

    embedded = _extract_image_key(src)
    low = src.lower()

    # ① 飞书图片链接：内嵌 key 同租户直通，无需上传
    if embedded and ("://" in src or ("/" in src and not os.path.isfile(src))):
        return embedded

    # ② http(s) 图片链接：下载后上传，URL 级 + 内容级双缓存
    if low.startswith("http://") or low.startswith("https://"):
        url_cache_key = "url:" + hashlib.sha1(src.encode("utf-8")).hexdigest()
        if url_cache_key in cache:
            return cache[url_cache_key]
        import tempfile
        # 临时文件必须放 /tmp：macOS /var/folders 默认临时目录会被 lark-cli
        # 拒绝读取（2026-10-27 实测 cannot open file，与权限位无关）
        fd, tmp = tempfile.mkstemp(suffix=".png", prefix="mi-hero-", dir="/tmp")
        os.close(fd)
        try:
            _download_image(src, tmp)
            digest = hashlib.sha1(open(tmp, "rb").read()).hexdigest()
            if digest in cache:
                cache[url_cache_key] = cache[digest]
                core.jdump(cache_path, cache)
                return cache[digest]
            key = _upload_hero_image(tmp)
            if key:
                cache[digest] = key
                cache[url_cache_key] = key
                core.jdump(cache_path, cache)
            return key
        except Exception as exc:  # noqa: BLE001
            sys.stderr.write("[hero] 图片链接处理失败，回退默认: %s\n" % exc)
            return None
        finally:
            try:
                os.unlink(tmp)
            except OSError:
                pass

    # ③ 本地路径：上传并按内容缓存
    if os.path.isfile(src):
        digest = hashlib.sha1(open(src, "rb").read()).hexdigest()
        if digest in cache:
            return cache[digest]
        # 非 ASCII 路径中转（2026-10-27 实测，陷阱 72）：lark-cli 对含中文等
        # 非 ASCII 字符的 --file 路径直接报 "cannot open file"（与权限位无关，
        # 文件真实存在也拒读）。与 URL 分支同策略：复制到 /tmp ASCII 临时名
        # 再上传；上传结果按**内容 sha1** 缓存，与原路径名无关，同图不重传。
        try:
            src.encode("ascii")
        except UnicodeEncodeError:
            import shutil
            import tempfile as _tf
            suffix = os.path.splitext(src)[1] or ".png"
            fd, ascii_tmp = _tf.mkstemp(suffix=suffix, prefix="mi-hero-", dir="/tmp")
            os.close(fd)
            try:
                shutil.copyfile(src, ascii_tmp)
                key = _upload_hero_image(ascii_tmp)
            finally:
                try:
                    os.unlink(ascii_tmp)
                except OSError:
                    pass
            if key:
                cache[digest] = key
                core.jdump(cache_path, cache)
            return key
        key = _upload_hero_image(src)
        if key:
            cache[digest] = key
            core.jdump(cache_path, cache)
        return key

    # ④ 兜底：混杂文本里可能内嵌 key；再不行明确告警回退默认
    if embedded:
        return embedded
    sys.stderr.write("[hero] 头图输入无法识别（非路径/链接/key），回退默认: %s\n"
                     % src[:120])
    return None

LEAD_POOL_VIEW_URL = "https://webeye.feishu.cn/base/K1GrbusPBadZzws1DhjcPTionsf?table=tblIJG9zaVi1I7iM&view=vewHOTnMQa"


def _button(content, url, btype="primary", size="small"):
    return {"tag": "button", "text": {"tag": "plain_text", "content": content},
            "type": btype, "width": "default", "size": size,
            "behaviors": [{"type": "open_url", "default_url": url}]}


def _section_heading(text):
    return {"tag": "markdown", "content": "**%s**" % text, "text_size": "heading",
            "margin": "16px 0px 4px 0px"}


def _activity_elements(records, limit=8, max_notify=15):
    """活动日历段：judged 里 type=活动情报 且带 activity 明细的记录。

    行内粗体闭合符后必须跟空格（`**时间：** 值`）：全角标点+紧贴正文会让
    markdown 粗体失效、星号原样显示（实测陷阱）。

    **notify=true 的活动必须全部展示，不得截断**（与新闻段/榜单段同一纪律，
    陷阱 45/47）：截断的 notify=true 条目在锚点推进后 `logged_at` 早于新锚点，
    下期永不出现 → 永久漏发。此前只把 limit 从 3 提到 8（治标），积压超过 8 条
    仍会漏；本次改为与新闻段同构——notify=true 全展示，notify=false 才按 limit。
    `notify=false` 属归档语义，按 limit 少量补充即可。
    """
    els = []
    # 排序：notify 优先（必须展示），同组内 high 优先——high 活动不能被原序
    # 靠前的 normal 挤出日历（2026-10-26 实测：high 物流展第 2 位侥幸入选）
    acts = [x for x in records if (x.get("activity") and not x.get("discard"))]
    acts.sort(key=lambda x: (0 if x.get("notify") else 1,
                             0 if x.get("significance") == "high" else 1))
    must = [r for r in acts if r.get("notify")]
    if len(must) > max_notify:
        sys.stderr.write("[warn] 本轮 notify=true 活动 %d 条超过 %d 阈值——疑似判定过松"
                         "（见陷阱 44：low 不该通知）。为保证不漏发仍全部展示，请复核口径。\n"
                         % (len(must), max_notify))
    shown = must + [r for r in acts if not r.get("notify")][:limit]
    for r in shown:
        a = r["activity"]
        lines = ["**%s**" % (a.get("name") or r.get("title") or "未命名活动")]
        if a.get("time"):
            lines.append("**时间：** %s" % a["time"])
        if a.get("location"):
            lines.append("**地点：** %s" % a["location"])
        if a.get("highlight"):
            # 容错：判定数据若自带「看点：」前缀会渲染成「看点：看点：…」
            # （2026-10-27 实测 18 条历史数据受此污染，源因是契约示例误写前缀）。
            # 契约侧已改（activities-criteria.md 明确不带前缀），此处兜底去重，
            # 避免历史/手写数据的既有前缀再次污染卡面。
            hl = re.sub(r"^\s*(?:看\s*点\s*[:：]\s*)+", "", str(a["highlight"]))
            if hl:
                lines.append("**看点：** %s" % hl)
        if r.get("sources"):
            lines.append(_sources_line(r["sources"]))
        els.append({"tag": "markdown", "content": "\n".join(lines), "text_size": "notation"})
        if a.get("signup_url"):
            els.append(_button("👉 点我查看详情并报名", a["signup_url"]))
    return els


def _news_elements(records, limit=3, max_notify=15):
    """行业重点新闻段：编号红字标题 + 摘要 + 原文按钮。

    纵深防御：无 title 的记录跳过且不占编号（上游护栏之外的最后防线，防坏卡）。

    **notify=true 的记录必须全部展示，不得截断**（2026-10-27 实测漏发，陷阱 45）：
    截断的 notify=true 条目在锚点推进后 `logged_at` 早于新锚点，下期永不出现
    → 永久漏发。活动日历段当初正因同一原因把 limit 从 3 提到 8；新闻段漏了
    同样的修复（实测 02:40 轮 86 条曾通知情报只展示了 3 条，83 条从未真正送达）。
    `notify=false` 属归档语义，按 `limit` 少量补充展示即可。

    max_notify 是**告警阈值不是截断阈值**：超过时说明判定过松（陷阱 42 要求
    low 不通知），输出告警让人复核判定；但代码不静默截断——宁可卡长/发送报错
    （可观测），也不漏发（静默）。
    """
    els = []
    pool = [r for r in records if not r.get("discard") and r.get("title")]
    # 排序：notify 优先（必须展示），同组内按 significance
    pool.sort(key=lambda r: (0 if r.get("notify") else 1,
                             SIG_ORDER.get(r.get("significance"), 3)))
    must = [r for r in pool if r.get("notify")]
    rest = [r for r in pool if not r.get("notify")]
    if len(must) > max_notify:
        sys.stderr.write("[warn] 本轮 notify=true 情报 %d 条超过 %d 阈值——疑似判定过松"
                         "（见陷阱 42：low 不该通知）。为保证不漏发仍全部展示，"
                         "请复核判定口径；卡可能过长导致发送失败。\n"
                         % (len(must), max_notify))
    shown = must + rest[:limit]
    for i, r in enumerate(shown, 1):
        els.append({"tag": "markdown",
                    "content": "<font color='red'>**%d. %s**</font>" % (i, r.get("title") or "")})
        if r.get("summary"):
            els.append({"tag": "markdown", "content": r["summary"], "text_size": "notation"})
        # 销售弹药（2026-10-27 用户明令）：技术战报配【销售对标指引】、战略风向配
        # 【销售战略建议】——用户原话"缺少真正的销售弹药"。必须显著展示（灰底块），
        # 不能只在 JSON 里躺着。字段由 judge 侧护栏保证非空。
        if r.get("sales_playbook"):
            els.append({"tag": "markdown",
                        "content": "**【销售对标指引】** %s" % r["sales_playbook"]})
        if r.get("sales_strategy"):
            els.append({"tag": "markdown",
                        "content": "**【销售战略建议】** %s" % r["sales_strategy"]})
        if r.get("sources"):
            els.append({"tag": "markdown", "content": _sources_line(r["sources"]),
                        "text_size": "notation"})
        if r.get("url"):
            els.append(_button("让我看看👀", r["url"]))
    return els


def _rank_elements(records, limit=4):
    """榜单监控段（需求文档「榜单监控」板块）：榜单新晋/黑马信号 + 背后公司画像结论。

    数据来源：judged 里 type 含「榜单」的情报记录（判断准则约定榜单新晋统一标
    type=「榜单新晋」）。「排除长久霸榜、只看新上榜」的语义在源侧差分（links 模式
    基线有本轮新出现 = added）与 AI 判断（rank_new_entry kind）两处已保证，这里只做展示。
    """
    els = []
    pool = [r for r in records if not r.get("discard") and "榜单" in (r.get("type") or "")]
    # 与新闻段同纪律（陷阱 45/46）：notify=true 不得被 limit 截断（锚点推进后
    # logged_at 早于新锚点 → 永久漏发）。榜单记录多为 notify=false 归档语义，
    # 但一旦判定为要通知就必须展示。
    pool.sort(key=lambda r: (0 if r.get("notify") else 1,
                             SIG_ORDER.get(r.get("significance"), 3)))
    must = [r for r in pool if r.get("notify")]
    shown = must + [r for r in pool if not r.get("notify")][:limit]
    for r in shown:
        els.append({"tag": "markdown",
                    "content": "<font color='red'>**🏆 %s**</font>" % (r.get("title") or "")})
        if r.get("summary"):
            els.append({"tag": "markdown", "content": r["summary"], "text_size": "notation"})
        if r.get("sources"):
            els.append({"tag": "markdown", "content": _sources_line(r["sources"]),
                        "text_size": "notation"})
        # v0.6.8（2026-10-27 用户明令）：**不再为榜单条目渲染「查看榜单原文」按钮**。
        # 依据：榜单记录的 url 与 sources[0].url 实测 4/4 完全一致，而 `sources_line`
        # 本身就把媒体名渲染成可点链接，所以删按钮**不丢任何跳转能力**，只去掉一份
        # 视觉冗余（每个按钮约 200 字节，且与正上方的来源链接指向同一地址）。
        # 注意：其余段落的按钮（新闻「让我看看👀」、活动「报名」、线索「查看线索」）
        # 一律保持原样——用户只点了榜单这一处，不做任何连带改动。
    return els


def _lead_elements(leads, limit=3, view_url=None):
    """潜在客户段：公司名按钮 + 文档字段式描述 + 猎隼线索池登记按钮（record 级定位）。

    P0 优先、同优先级按时间倒序——新线索不许被旧线索占掉展示位。

    **notify=true 的线索必须全部展示、不截断**（v0.6.5 修，陷阱 58）：
    线索段此前是四段里唯一没做 notify 豁免的（陷阱 47 声称"四段统一口径"，
    实际只落地了活动/新闻/榜单三段）。`limit=3` 硬截断时，第 4 条起的 P0 线索
    虽然判了"要通知"，但**永远不进内参卡**；锚点推进后 `logged_at` 早于新锚点，
    下期也不再出现 → 永久漏发。P0 线索是市场同事的当期建联清单，漏发 = 直接
    丢商机（比漏一条资讯严重）。`limit` 仅约束 notify=false 的归档线索。
    """
    els = []
    ranked = sorted(_active(leads), key=lambda l: (l.get("priority") != "P0",
                                                   core.parse_iso(l.get("logged_at")) or 0), reverse=True)
    must = [l for l in ranked if l.get("notify")]
    shown = must + [l for l in ranked if not l.get("notify")][:limit]
    for ld in shown:
        pri_label, _ = PRIORITY_LABEL.get(ld.get("priority") or "P1", PRIORITY_LABEL["P1"])
        lines = ["**%s ｜ %s**" % (ld.get("entity_name") or "未命名", pri_label)]
        if lead_exists(ld):
            lines.append("<font color='grey'>状态：此前已登记【猎隼线索池】，本次未重复写入</font>")
        lines.append("所属赛道：%s" % ("/".join(ld.get("industries") or []) or "待补充"))
        groups = ld.get("matched_group_names") or ld.get("matched_groups") or []
        if groups:
            lines.append("命中规则：%s" % "；".join(groups))
        if ld.get("summary"):
            lines.append(ld["summary"])
        if ld.get("profile"):
            lines.append("业务描述：%s" % ld["profile"])
        if ld.get("opportunity"):
            lines.append("**最大机会：%s**" % ld["opportunity"])
        if ld.get("sources"):
            lines.append(_sources_line(ld["sources"]))
        els.append(_button(ld.get("entity_name") or "查看线索", ld.get("url") or LEAD_POOL_VIEW_URL,
                           btype="danger_text", size="medium"))
        els.append({"tag": "markdown", "content": "\n\n".join(lines), "text_size": "notation"})
        pool_url = lead_pool_url(ld, view_url or LEAD_POOL_VIEW_URL)
        if ld.get("bitable_record_id"):
            btn = ("已在线索池·点此查看既有记录" if lead_exists(ld)
                   else "该信息已登记【猎隼线索池】，点此查看完整线索")
            els.append(_button(btn, pool_url, btype="danger_filled"))
        else:
            els.append(_button("查看【猎隼线索池】", pool_url, btype="danger_filled"))
    return els


def briefing_card(records, leads, view_url=None, banner_key=None):
    """需求文档版式内参卡：活动日历 / 行业重点新闻 / 榜单监控（有数据才出现）/ 潜在客户与业务情报。
    全空返回 None（当天无内容不产空卡）。banner_key 传入时覆盖默认头图（自定义活动图）。"""
    body = [{"tag": "img", "img_key": banner_key or BRIEFING_IMGS["banner"], "preview": True,
             "transparent": False, "scale_type": "fit_horizontal",
             "alt": {"tag": "plain_text", "content": "市场团队内参"},
             "corner_radius": "10px"}]
    act_els = _activity_elements(records)
    if act_els:
        body.append(_section_heading("📅 WebEye活动日历"))
        body += act_els
    # v0.6.5 修：type=活动情报 但**缺 activity 明细**时，旧实现会被活动段（要求
    # `x.get("activity")`）抛弃，同时又被新闻段的 type!="活动情报" 过滤掉 →
    # 两头落空，`briefing_card` 甚至可能返回 None（全空不产卡）→ notify=true
    # 的活动情报永久漏发。activity 明细可能因富化失败/判定漏填而缺失，这属于
    # 数据瑕疵，不该让内容消失：把它降级交给新闻段按普通情报渲染（有 title 就能显示）。
    act_ids = {id(r) for r in records if r.get("activity") and not r.get("discard")}
    news_pool = [r for r in records
                 if "榜单" not in (r.get("type") or "")
                 and (r.get("type") != "活动情报" or id(r) not in act_ids)]
    if any(r.get("type") == "活动情报" and id(r) not in act_ids and r.get("notify")
           and not r.get("discard") for r in records):
        sys.stderr.write("[warn] 存在 notify=true 的活动情报但缺 activity 明细"
                         "（name/time/location），已降级按普通情报渲染——请回判据补全 activity\n")
    news_els = _news_elements(news_pool)
    if news_els:
        body.append({"tag": "hr"})
        body.append(_section_heading("🔥 行业重点新闻"))
        body += news_els
    rank_els = _rank_elements(records)
    if rank_els:
        body.append({"tag": "hr"})
        body.append(_section_heading("🏆 榜单监控｜海外软件榜单·新晋黑马"))
        body.append({"tag": "markdown",
                     "content": "监控海外软件榜单（AI产品榜、AppMagic 等）新上榜/登顶的黑马产品"
                                "——排除长久霸榜，只看新晋；并调研背后公司是否符合 WebEye 潜在客户画像。",
                     "text_size": "notation"})
        body += rank_els
    lead_els = _lead_elements(leads, view_url=view_url)
    if lead_els:
        body.append({"tag": "hr"})
        body.append(_section_heading("🤝 潜在客户与业务情报"))
        body += lead_els
    if len(body) <= 1:
        return None
    return {**CARD_CONFIG,
            "header": {"title": {"tag": "plain_text", "content": "⚡️ 市场团队内参：近期热点与活动简报"},
                       "subtitle": {"tag": "plain_text", "content": core.now_iso().replace("T", " ")[:16]},
                       "template": "indigo",
                       "icon": {"tag": "custom_icon", "img_key": BRIEFING_IMGS["icon"]}},
            "body": {"direction": "vertical", "padding": "12px 12px 20px 12px", "elements": body}}


# ---------- 主流程：judged.json 落盘 ----------

def auto_merge_duplicates(intel, enable=True):
    """**自动合并疑似重复**：把从条并进主条，返回 (新 intel, 合并掉的数量)。

    v0.6.7 加这个函数的原因（用户实测反馈）：
      「很多信息实际是重复的…AI 协助和兜底就能很好的处理，为什么还会重？」
    `warn_duplicates()` 只提示不够——提示了没人改，卡片上照样重复。用户明确说
    「兜底就能很好的处理」，所以这里把兜底做成**真的会合并**。

    合并语义（与 `merged_change_ids` 契约完全一致，不新造字段）：
      - 主条：按 significance 高 → 标题短（更像专稿而非早报聚合）挑；
      - 从条：`change_id` 进主条 `merged_change_ids`，`sources` 并进主条（去重），
        内容**不丢**（标题候选进主条 `merged_titles` 供溯源），然后从 intel 移除。
      - 被合并的从条会由 `coverage_check` 通过 `merged_change_ids` 认可，
        不会因为"少答了"而报覆盖度缺口。

    安全阀（**宁可少合并，不可错合并**）：
      - 只合并 `notify=true` 且非 discard 的（归档条目不上卡，合并没有收益）；
      - 自动合并要求标题、摘要、分类、销售指引与活动明细一致；
      - 「主体 + 事件类型」仅生成候选，语义合并仍由 AI 给出 merged_change_ids；
      - `enable=False` 时退化为仅提示，供不确定场景使用。
    """
    if not enable:
        return intel, 0
    live = [r for r in intel if not r.get("discard") and r.get("notify")]
    # 语义候选仍由 find_duplicate_intel 提示；自动合并只接受正文一致的强证据。
    # 不用候选的连通分量，避免 A≈B、B≈C 却把不同事件 A/C 一起吞掉。
    buckets = {}
    for r in live:
        title = core.norm(r.get("title") or "").lower()
        if len(title) < 8:
            continue
        key = (title, core.norm(r.get("summary") or ""), r.get("type"),
               core.norm(r.get("sales_playbook") or ""), core.norm(r.get("sales_strategy") or ""),
               json.dumps(r.get("activity"), ensure_ascii=False, sort_keys=True))
        buckets.setdefault(key, []).append(r)
    clusters = [group for group in buckets.values() if len(group) > 1]
    if not clusters:
        return intel, 0

    drop_ids = set()
    for group in clusters:
        def rank(r):
            sig = {"high": 0, "normal": 1, "low": 2}.get(r.get("significance"), 3)
            return (sig, len(r.get("title") or ""))
        ordered = sorted(group, key=rank)
        main, rests = ordered[0], ordered[1:]
        mids = []
        for r in rests:
            cid = r.get("change_id")
            if not cid or cid in drop_ids:
                continue
            mids.append(cid)
            mids.extend(r.get("merged_change_ids") or [])
            drop_ids.add(cid)
            # 保留从条完整判定和主链接，避免只存标题而失去审计依据。
            main.setdefault("merged_records", []).append(json.loads(json.dumps(r, ensure_ascii=False)))
            # 来源并入主条（去重，保持原有中文名）
            have = {(s.get("name"), s.get("url")) for s in (main.get("sources") or [])}
            for s in (r.get("sources") or []):
                key = (s.get("name"), s.get("url"))
                if key not in have:
                    main.setdefault("sources", []).append(s)
                    have.add(key)
            # 原标题留档（哪些报道被合并，事后可查）
            main.setdefault("merged_titles", []).append(r.get("title"))
        if mids:
            main["merged_change_ids"] = list(dict.fromkeys(
                (main.get("merged_change_ids") or []) + mids))

    if not drop_ids:
        return intel, 0
    kept = [r for r in intel if r.get("change_id") not in drop_ids]
    sys.stderr.write(
        "[dedupe] 自动合并 %d 条重复报道到 %d 个主条（合并关系记在 merged_change_ids，"
        "来源已并入主条，原标题留在 merged_titles 可溯源）\n"
        % (len(drop_ids), len(clusters)))
    return kept, len(drop_ids)


def absorb_discards(judged):
    """把顶层 `discarded` 键自动并入 intel 列表并返回 (intel, 是否并入)。

    构建脚本常把弃权条目单独放 judged["discarded"] 而忘并入 intel（实战踩坑两次），
    在代码层兜住：有这个键就自动吸收，不再依赖构建者记得手工合并。
    """
    discards = judged.get("discarded")
    if not isinstance(discards, list):
        return judged.get("intel") or [], False
    intel = list(judged.get("intel") or [])
    intel.extend(r for r in discards if isinstance(r, dict))
    return intel, True


def strip_writeback(intel):
    """复用登记的结构归一：剥离落盘回写字段 + 弃权记录转 judged 契约。

    洁净重置复用场景（陷阱 19）：从旧 log/intel.jsonl 原样拷回的记录会带
    落盘回写字段（logged_at/grounding/grounding_notes），会被字段白名单
    拦截退出码 1——复用是标准流程，每次必踩。已知回写字段自动剥离并提示；
    其余未知字段仍由 check_keys 点名拦截（防拼写错误静默失效）。

    另：旧 log/discarded.jsonl 的弃权记录是 {change_id, reason, logged_at}
    结构，与 judged 契约 {discard: true, discard_reason} 不一致——检测到
    含 reason 且未带 discard 键的记录自动归一（dry-run 实测拦截后代码化）。
    返回剥离到的回写字段集合。
    """
    writeback = {"logged_at", "grounding", "grounding_notes"}
    # suppressed_by_guard 不剥离：它是"已通报"的正式契约字段（INTEL_KEYS 成员），
    # 从旧登记复用时要原样继承给 digest 过滤。
    stripped = set()
    for r in intel:
        extra = writeback & set(r)
        if extra:
            stripped |= extra
            for k in extra:
                del r[k]
        if "reason" in r and not r.get("discard") and "discard_reason" not in r:
            r["discard"] = True
            r["discard_reason"] = r.pop("reason")
    return stripped


def demote_already_sent(intel, sent_ids):
    """对已发登记中存在的情报强制 notify=False，返回 (处理后的 intel, 降级条数)。

    洁净重置场景（清空 log/intel.jsonl 验证"新环境"）下，历史已发情报会被当新条目
    重新通知轰炸（陷阱 19 靠人工标注 notify=false，实战负担重且易漏）。
    传入旧 intel.jsonl 的 change_id 集合，代码层强制降级，杜绝重复轰炸。
    """
    if not sent_ids:
        return intel, 0
    demoted = 0
    for r in intel:
        if r.get("discard"):
            continue
        if r.get("change_id") in sent_ids:
            # 命中即打标（无论 notify 当前值——上一轮已压成 false 的记录同样
            # 是"已通报"，不打标会让 digest 在洁净重置全量窗旧闻重推）；
            # notify 仍为 true 的同时压成 false，防实时通知重复。
            r["suppressed_by_guard"] = True
            if r.get("notify"):
                r["notify"] = False
                demoted += 1
    return intel, demoted


def _dry_run_input(ws, input_path, already_sent=None, sent_before=None):
    """沙箱预检：把工作区完整复制到临时目录后执行真实 --input 流程，真实工作区零写入。

    洁净验证每轮都是新场景，历史上多次出现「落盘时才暴露」的问题（字段白名单、
    覆盖度缺口、grounding 拦截）。dry-run 把这些问题提前到落盘前暴露：
    预检通过再去掉 --dry-run 正式执行，失败则不改任何真实文件（兜底纪律）。
    """
    import shutil
    import tempfile
    tmp = tempfile.mkdtemp(prefix="market-intel-dryrun-")
    try:
        for name in os.listdir(ws):
            if name.startswith("."):
                continue
            src = os.path.join(ws, name)
            dst = os.path.join(tmp, name)
            if os.path.isdir(src):
                shutil.copytree(src, dst)
            else:
                shutil.copy2(src, dst)
        rc = cmd_input(tmp, input_path, already_sent=already_sent, sent_before=sent_before)
        sys.stderr.write("[dry-run] 沙箱预检通过（真实工作区零写入）——去掉 --dry-run 正式执行\n")
        return rc
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def cmd_input(ws, input_path, already_sent=None, dry_run=False, sent_before=None):
    if dry_run:
        return _dry_run_input(ws, input_path, already_sent=already_sent, sent_before=sent_before)
    now = core.now_iso()
    latest = core.jload(os.path.join(ws, "out", "latest.json"), None)
    judged = core.jload(input_path, None)
    if not judged:
        raise SystemExit("[error] 读不到判断文件: %s" % input_path)
    intel, absorbed = absorb_discards(judged)
    if absorbed:
        sys.stderr.write("[absorb] 检测到顶层 discarded 键（%d 条弃权），已自动并入 intel——"
                         "下次构建可直接只填 intel 列表\n" % len(judged.get("discarded") or []))
    # 同一事件多源报道的自动合并（v0.6.7，用户实测反馈：卡片重复）。
    # 放在 sent-guard 之前：先合并能让"被合并的从条"也进已发登记，防换源重推。
    intel, merged_n = auto_merge_duplicates(intel)
    if already_sent:
        sent_path = os.path.expanduser(already_sent)
        cut = (core.parse_iso(sent_before) or 0) if sent_before else None
        sent_ids = set()
        for r in core.read_jsonl(sent_path) if os.path.exists(sent_path) else []:
            if r.get("discard"):
                continue
            # notify=true 且落盘时间晚于旧锚点（--sent-before）：判定后从未随
            # 内参发出过（上轮「落盘不发、锚点未推进」场景），不算已发——
            # 压制会造成漏发（宁重勿漏）。notify=false 属归档语义，无条件算已发。
            if r.get("notify") and cut and (core.parse_iso(r.get("logged_at")) or 0) >= cut:
                continue
            sent_ids.add(r.get("change_id"))
            # 被合并条目也属已发：合并报道换源重现时（如 Anthropic IPO 的 36氪/
            # 钛媒体版本在主条滚窗后首次差分入集），不加进来会重复通知轰炸
            for mid in r.get("merged_change_ids") or []:
                sent_ids.add(mid)
        intel, demoted = demote_already_sent(intel, sent_ids)
        if demoted:
            sys.stderr.write("[sent-guard] %d 条情报已在旧登记中（洁净重置防重发），"
                             "强制 notify=false\n" % demoted)
    fact_records = judged.get("facts") or []
    stripped = strip_writeback(intel)
    if stripped:
        sys.stderr.write("[reuse] 已从 intel 记录剥离落盘回写字段: %s（复用旧登记时属预期）\n"
                         % ", ".join(sorted(stripped)))
    for r in intel:
        core.check_keys(r, INTEL_KEYS, "intel record")
    # 通知内容护栏：notify=true 的记录会直接进卡片，缺 title/url 产坏卡。
    # 必须放在 sent-guard 之后——复用旧精简登记的墓碑记录（无 title）已被压成
    # notify=false，不受影响；此护栏拦的是 AI 判定真要通知却漏带正文的场景。
    bad_notify = [r.get("change_id") for r in intel
                  if r.get("notify") and (not r.get("title") or not r.get("url"))]
    if bad_notify:
        sys.stderr.write("[coverage] 以下 notify=true 情报缺 title/url（无法产卡，"
                         "标题与链接必须从 latest.json 原文带回）:\n  %s\n" % "\n  ".join(bad_notify))
        raise SystemExit(2)

    # 销售指引护栏（2026-10-27 用户明令）：技术战报/价格战必须给 sales_playbook，
    # 战略风向必须给 sales_strategy。用户原话："缺少真正的销售弹药"——没有销售动作
    # 的资讯对他等于噪音。空值/占位符/过短一律拦下（脚本只说"有没有"，
    # "好不好"由 AI 内容核查把关，见 SKILL.md 工作流第 7 步）。
    _MIN_PLAYBOOK = 40   # 60–120 字要求，40 是硬下限（防"更稳定更安全"这类空话）
    need_sales = []
    for r in intel:
        if r.get("discard") or not r.get("notify"):
            continue
        t = r.get("type") or ""
        if t == "技术战报":
            val = (r.get("sales_playbook") or "").strip()
            if len(val) < _MIN_PLAYBOOK:
                need_sales.append((r.get("change_id"), "sales_playbook", len(val)))
        elif t == "战略风向":
            val = (r.get("sales_strategy") or "").strip()
            if len(val) < _MIN_PLAYBOOK:
                need_sales.append((r.get("change_id"), "sales_strategy", len(val)))
    if need_sales:
        sys.stderr.write(
            "[sales] 以下 notify=true 情报缺销售指引或过于空泛（技术战报必填 sales_playbook、"
            "战略风向必填 sales_strategy，至少 %d 字且必须落到具体能力/行业/方案，"
            "不许写「更稳定更安全」这类空话；范例见 references/intel-criteria.md §五）:\n  %s\n"
            % (_MIN_PLAYBOOK, "\n  ".join(
                "%s 缺 %s（现 %d 字）" % (cid, field, n) for cid, field, n in need_sales)))
        raise SystemExit(2)
    # 语义护栏：**本轮窗口内**的条目不允许裸骨架（无 title 且非弃权/墓碑）——
    # 窗口条目必须得到完整判定，裸骨架 = 组装脚本丢失正文/理由（2026-10-27 实测：
    # 对旧 discarded.jsonl 的 {change_id, reason} 形态误剥所致，弃权理由永久丢失）。
    # 窗口外的历史精简留痕（v0.3.2 前 significance=unknown+notify=false 形态，
    # 本就无 title）语义合法，放行；其空条目风险由 _news_elements 的 title
    # 防御兜底。
    window_ids = {c["change_id"] for c in ((latest or {}).get("changes") or [])}
    skeletons = [r.get("change_id") for r in intel
                 if not r.get("discard") and not r.get("title")
                 and not r.get("suppressed_by_guard") and r.get("change_id") in window_ids]
    if skeletons:
        sys.stderr.write("[coverage] 以下窗口条目为裸骨架（无 title 且非弃权/墓碑，"
                         "组装丢失正文；复用旧 discarded.jsonl 需保留 reason 字段供归一）:\n  %s\n"
                         % "\n  ".join(skeletons))
        raise SystemExit(2)

    # 1) 覆盖度校验：漏答一条 = 判断不完整（latest.json 缺失时显式告警，不做静默全过）。
    #    历史登记（log/intel.jsonl + log/discarded.jsonl）视为已判，支持 facts 补录
    #    与增量判定场景——只要求「从未判过」的条目在本轮得到回答。
    judged_before = set()
    for log_name in ("intel.jsonl", "discarded.jsonl"):
        for rec in core.read_jsonl(os.path.join(ws, "log", log_name)):
            if rec.get("change_id"):
                judged_before.add(rec["change_id"])
    if latest is None:
        sys.stderr.write("[warn] out/latest.json 缺失，本轮不做覆盖度校验（手工补录场景）\n")
        missing = []
    else:
        missing = coverage_check(latest, intel, fact_records, already_judged=judged_before)
    if missing:
        sys.stderr.write("[coverage] 以下 change_id 未判（补全后重跑，pending 已保留）:\n  %s\n"
                         % "\n  ".join(missing))
        raise SystemExit(2)
    changes_by_id = {c["change_id"]: c for c in ((latest or {}).get("changes") or [])}

    entities = facts_mod.load_entities(ws)
    rules = match_mod.load_rules(ws)
    notify_cfg = load_notify(ws)

    # 2) 情报流水（intel + discard 分流落盘；按 change_id 幂等——修复后重跑不产生重复记录）
    intel_path = os.path.join(ws, "log", "intel.jsonl")
    discard_path = os.path.join(ws, "log", "discarded.jsonl")
    existing_intel = core.read_jsonl(intel_path, strict=True)
    active = {r.get("change_id"): r for r in existing_intel}
    discarded = {r.get("change_id"): r for r in core.read_jsonl(discard_path, strict=True)}
    notified_intel = []
    for r in intel:
        cid = r.get("change_id")
        previous = active.get(cid)
        prior_state = previous or discarded.get(cid) or {}
        if r.get("discard"):
            if previous:
                # 撤回只移出有效流水，原判定仍留在修订日志中。
                core.append_jsonl(os.path.join(ws, "log", "intel-revisions.jsonl"),
                                  {"change_id": cid, "previous": previous, "action": "discard", "at": now})
                del active[cid]
            discarded[cid] = {"change_id": cid, "reason": r.get("discard_reason") or "未说明",
                              "logged_at": prior_state.get("logged_at") or now,
                              "suppressed_by_guard": bool(prior_state.get("suppressed_by_guard"))}
            continue
        rec = {k: r.get(k) for k in INTEL_KEYS if k in r and not k.startswith("_")}
        rec["logged_at"] = prior_state.get("logged_at") or now
        nums, grounded_notes = grounding_check(r, changes_by_id.get(cid))
        if grounded_notes:
            rec["grounding_notes"] = grounded_notes
        if nums:
            rec["grounding"] = "unverified"
            sys.stderr.write("[grounding] change %s 的说明含原文没有的数字: %s（请回原文核实）\n"
                             % (cid, ",".join(nums)))
        rec.setdefault("significance", "unknown")
        # 修正文案不等于重新发送；既有发送保护优先于 AI 新申报。
        if prior_state.get("suppressed_by_guard"):
            rec["suppressed_by_guard"] = True
        if rec["significance"] == "unknown" or rec.get("suppressed_by_guard"):
            rec["notify"] = False
        if previous and previous != rec:
            core.append_jsonl(os.path.join(ws, "log", "intel-revisions.jsonl"),
                              {"change_id": cid, "previous": previous, "action": "revise", "at": now})
        active[cid] = rec
        discarded.pop(cid, None)
        if rec.get("notify"):
            notified_intel.append(rec)
    core.replace_jsonl(intel_path, active.values())
    core.replace_jsonl(discard_path, discarded.values())

    # 3) 事实抽取 -> 企业档案（含 web_page 招聘信号的确定性直录）
    touched = set()
    for f in fact_records:
        eid = facts_mod.apply_fact(ws, f, entities, now)
        touched.add(eid)
    for c in (latest or {}).get("changes") or []:
        if (c.get("source_kind") == "web_page" and c.get("change_kind") == "added"
                and c.get("owner_entity") and str(c.get("key", "")).startswith("kw:")):
            kw = str(c["key"])[3:]
            apply_hiring_signal(ws, c["owner_entity"], entities, kw, c.get("url"), now)
            touched.add(c["owner_entity"])

    # 4) 规则匹配 -> 线索（对被触及的企业全量重算；确定性、可回测）
    #    分级口径（需求文档）：任一 tier=P0 组命中即 P0（市场优先建联，实时通知）；
    #    仅 P1 组命中为 P1（入池+日报，不实时打扰，notify_p1 可放开）。
    new_leads = []
    all_leads_rows = core.read_jsonl(os.path.join(ws, "log", "leads.jsonl"))
    existing_keys = {l.get("dedupe_key") for l in all_leads_rows}
    # 池侧状态对齐（2026-10-27 实测缺口，陷阱 71）：本地 leads.jsonl 的
    # bitable_status 是 --bitable --commit 写入层回写的，洁净重置/手工清理后
    # 本地状态丢失，而飞书池里的记录还在——卡片渲染 lead_exists() 查本地
    # 会把"昨天刚入池的公司"当全新 P0 再推一遍（池侧去重兜底不会重复建行，
    # 但卡面语义失真：红色「P0 · 优先建联」实际早已在池）。修法：规则命中的
    # 候选在产卡前按公司名对飞书池查重，命中即回填 bitable_status=exists +
    # record_id，卡面走「已存在」灰色标注。只对规则命中的少数候选查（通常
    # ≤3 家），不打 API 预算；查重失败（网络/权限）不阻断产卡，保持旧行为。
    pool_hits = _reconcile_pool_status(ws, [t for t in sorted(touched)
                                             if (facts_mod.load_fact(ws, t) or {}).get("name")])
    threshold = match_mod.threshold(rules)
    notify_p1 = bool((rules.get("scoring") or {}).get("notify_p1", False))
    # 绝对负向黑名单（2026-10-27 用户明令）：命中即硬丢弃——不落 leads.jsonl、
    # 不进池、不上报。用户口径：推超级巨头/成熟服务商/晚期融资 = 纯噪音，
    # 比漏报更伤信任。这是一道**独立于规则引擎的拦截**（在黑名单面前不看得分）。
    deny = match_mod.load_denylist(ws)
    denied = []
    for eid in sorted(touched):
        fact = facts_mod.load_fact(ws, eid)
        if not fact:
            continue
        hit, why = match_mod.check_denylist(fact, deny)
        if hit:
            denied.append({"entity_id": eid,
                           "entity_name": fact.get("name") or eid,
                           "reason": why})
            continue
        result = match_mod.evaluate_entity(fact, rules)
        if not result["matched_groups"]:
            continue
        dedupe = core.sha1(eid + "|" + ",".join(sorted(result["matched_groups"])))
        priority = result.get("priority") or "P1"
        name = fact.get("name") or eid
        pool_hit = pool_hits.get(core.norm(name)) or {}
        lead = {
            "lead_id": core.sha1(eid + "|" + now)[:12],
            "dedupe_key": dedupe,
            "entity_id": eid,
            "entity_name": name,
            "industries": fact.get("industries") or [],
            "priority": priority,
            "matched_groups": result["matched_groups"],
            "matched_group_names": result.get("matched_group_names") or result["matched_groups"],
            "score": result["score"],
            "evidence": result["evidence"],
            "summary": lead_summary(fact),
            "profile": fact.get("profile"),
            "opportunity": fact.get("opportunity"),
            "url": lead_source_url(fact),
            "sources": lead_sources(fact),
            "notify": result["score"] >= threshold and (priority == "P0" or notify_p1),
            "status": "new",
            "logged_at": now,
        }
        # 池侧回填（陷阱 71）：飞书池已存在的公司，卡面走「已存在」灰标注，
        # 绝不再以红色全新 P0 面孔重复轰炸（已入池公司不是新潜在线索）。
        if pool_hit.get("record_id"):
            lead["bitable_status"] = "exists"
            lead["bitable_record_id"] = pool_hit["record_id"]
            # 已在池的公司不再触发通知（私信/群催建联语义只对全新线索成立）
            lead["notify"] = False
        recent_cut = time.time() - 7 * 86400
        dup = dedupe in existing_keys and any(
            (core.parse_iso(l.get("logged_at")) or 0) > recent_cut
            for l in all_leads_rows
            if l.get("dedupe_key") == dedupe)
        if dup:
            continue  # 7 天内已报过同组合线索：不重复打扰
        core.append_jsonl(os.path.join(ws, "log", "leads.jsonl"), lead)
        existing_keys.add(dedupe)
        if lead["notify"]:
            new_leads.append(lead)

    save_leads_latest(ws, new_leads, now, touched)
    if denied:
        sys.stderr.write("[denylist] %d 家企业命中黑名单，已硬丢弃（不建联、不入池）:\n  %s\n"
                         % (len(denied), "\n  ".join(
                             "%s — %s" % (d["entity_name"], d["reason"]) for d in denied)))

    # 5) 产卡（脚本产卡、直接发送；群卡仅高分/高优时出现）
    view_url = (notify_cfg.get("bitable") or {}).get("view_url")
    cards = []
    if notified_intel:
        blocks, total = intel_blocks(notified_intel)
        tags = [("%d 条动态" % total, "violet")]
        if any(r.get("significance") == "high" for r in notified_intel):
            tags.append(("含重要动态", "red"))
        card = build_card("市场情报简报", core.now_iso().replace("T", " ")[:16] + " · 本轮判定",
                          blocks + [{"tag": "markdown", "text_size": "notation",
                                     "content": "<font color='grey'>明细已归档，可按时间线 / 类型 / 对象检索。</font>"}],
                          template="violet", tags=tags, icon={**INTEL_ICON, "color": "violet"})
        cards.append(write_card(ws, "card-intel-%s.json" % stamp(), card))
        if any(r.get("significance") == "high" for r in notified_intel):
            # 群卡：重要动态 @ 提醒（notify.mention 驱动，默认 @所有人）
            gels = ([e] if (e := _mention_lead("⚠️ 含重要动态，请关注", notify_cfg.get("notify"))) else []) + blocks
            gcard = build_card("市场情报简报 · 重要", core.now_iso().replace("T", " ")[:16],
                               gels, template="red", tags=tags, icon={**INTEL_ICON, "color": "red"})
            cards.append(write_card(ws, "card-intel-%s-group.json" % stamp(), gcard))
    if new_leads:
        p0_leads = [l for l in new_leads if l.get("priority") == "P0"]
        blocks = lead_blocks(new_leads, view_url=view_url)
        card = build_card("潜在客户线索", core.now_iso().replace("T", " ")[:16] + " · 规则匹配命中",
                          blocks + [{"tag": "markdown",
                                     "content": "<font color='grey'>P0=优先建联；P1=储备线索。"
                                                "命中线索将自动登记猎隼线索池（人工事后核查）；"
                                                "发送以 --lead-card 重产版为准。</font>",
                                     "text_size": "notation"}],
                          template="red" if p0_leads else "green",
                          tags=[("P0×%d" % len(p0_leads), "red")] if p0_leads else [("%d 条线索" % len(new_leads), "green")])
        cards.append(write_card(ws, "card-leads-%s.json" % stamp(), card))
        if p0_leads:
            # 群卡：P0 线索 @ 提醒（notify.mention 驱动，默认 @所有人）
            gblocks = lead_blocks(p0_leads, view_url=view_url)
            gels = ([e] if (e := _mention_lead("⚠️ P0 线索请尽快建联", notify_cfg.get("notify"))) else []) + gblocks
            gcard = build_card("潜在客户线索 · P0 优先建联", core.now_iso().replace("T", " ")[:16],
                               gels, template="red",
                               tags=[("%d 条 P0 线索，请尽快跟进" % len(p0_leads), "red")])
            cards.append(write_card(ws, "card-leads-%s-group.json" % stamp(), gcard))

    # 6) 核销待判队列：历史已判（judged_before）同样核销——否则 facts 补录等
    #    增量输入会把已判条目留在 pending，下轮以 reopened 身份误回放
    judged_ids = set(judged_before)
    for r in intel:
        if r.get("change_id"):
            judged_ids.add(r["change_id"])
        for mid in r.get("merged_change_ids") or []:
            judged_ids.add(mid)
    for f in fact_records:
        for cid in f.get("change_ids") or []:
            judged_ids.add(cid)
    kept = diffcore.save_pending(ws, (latest or {}).get("changes") or [], judged_ids, now)

    print("判定落盘完成：情报 %d 条（通知 %d）、事实 %d 条、新线索 %d 条、待判遗留 %d"
          % (len([r for r in intel if not r.get("discard")]), len(notified_intel),
             len(fact_records), len(new_leads), kept))
    # 重复内容兜底（v0.6.7，用户实测反馈）：同一事件多家报道该合并成一条，
    # 契约早就写了但执行靠人记。这里落盘后把候选挑出来提示，**不删不改不阻断**。
    warn_boilerplate([r for r in intel if r.get("notify")])
    warn_activity_highlights(intel)
    warn_duplicates([r for r in intel if r.get("notify")], ws)
    # write_card 超限时会返回列表（自动分片）：展开后逐张打印，序号即发送顺序
    for p in _flatten(cards):
        print("卡片: %s" % p)
    return 0


def apply_hiring_signal(ws, owner_entity, entities, keyword, page_url, now):
    """web_page 招聘关键词的确定性直录：不经模型，直接入档案（owner_entity 指明归属）。"""
    eid, _ = facts_mod.normalize_entity(owner_entity, entities)
    fact = facts_mod.load_fact(ws, eid) or {
        "entity_id": eid, "name": entities.get(owner_entity, {}).get("name", owner_entity),
        "aliases": [], "industries": [], "funding_events": [], "hiring_signals": [], "other_events": []}
    for hs in fact.setdefault("hiring_signals", []):
        if core.norm_url(hs.get("page_url") or "") == core.norm_url(page_url or ""):
            if keyword not in (hs.get("keywords") or []):
                hs["keywords"] = sorted((hs.get("keywords") or []) + [keyword])
            hs["last_seen"] = now
            core.jdump(facts_mod.fact_path(ws, eid), fact)
            return
    fact["hiring_signals"].append({"keywords": [keyword], "page_url": page_url,
                                   "first_seen": now, "last_seen": now})
    fact["updated_at"] = now
    core.jdump(facts_mod.fact_path(ws, eid), fact)


def lead_summary(fact):
    fe = (fact.get("funding_events") or [{}])[-1]
    hs = fact.get("hiring_signals") or []
    parts = []
    if fe:
        parts.append("近期融资：%s %s" % (fe.get("round") or "", fe.get("amount_text") or "金额未披露"))
    if hs:
        kws = sorted({k for h in hs for k in (h.get("keywords") or [])})
        if kws:
            parts.append("在招岗位关键词：%s" % "/".join(kws[:6]))
    return "；".join(parts) or "命中线索规则"


def lead_sources(fact):
    out = []
    for fe in fact.get("funding_events") or []:
        if fe.get("source_url"):
            out.append({"name": fe.get("source_name") or "融资报道", "url": fe["source_url"]})
    for oe in fact.get("other_events") or []:
        if oe.get("source_url"):
            out.append({"name": oe.get("source_name") or "动态报道", "url": oe["source_url"]})
    for hs in fact.get("hiring_signals") or []:
        if hs.get("page_url"):
            out.append({"name": "招聘页", "url": hs["page_url"]})
    return out[:3]


def lead_source_url(fact):
    src = lead_sources(fact)
    return src[0]["url"] if src else None


def stamp():
    return time.strftime("%m%d-%H%M")


def cmd_lead_card(ws):
    """重产最近一轮线索卡（私信卡 + 群卡 JSON 文件）。

    产出**文件**，不负责发送：什么时候跑、跑完发不发由 SKILL.md「发送纪律」决定
    ——线索卡属展示类，**默认不发**，仅用户明确要"看线索"时才跑本命令并发送。

    运行序：judge.py --input → --bitable --commit →（用户要求时）--lead-card。
    必须在 commit 之后跑：此时 bitable_status 已回写，exists（此前已入池）的线索
    在卡上中性标注「已存在」、绝不进群 @——群卡只催全新 P0 建联，重复轰炸=骚扰。
    群卡也由本命令产出（--input 阶段的群卡是写入前快照，以本命令的最新版为准）。
    """
    latest = core.jload(os.path.join(ws, "out", "leads-latest.json"), None)
    run_ids = [l.get("lead_id") for l in (latest or {}).get("leads") or []]
    if not run_ids:
        raise SystemExit("[error] 最近一轮没有新线索（out/leads-latest.json 为空），无需重发")
    by_id = {l.get("lead_id"): l for l in core.read_jsonl(os.path.join(ws, "log", "leads.jsonl"))
             if l.get("lead_id") in set(run_ids)}
    leads = [by_id[i] for i in run_ids if i in by_id]
    if not leads:
        raise SystemExit("[error] leads.jsonl 里找不到最近一轮的线索，疑被手工清理")
    # 字段实时兜底：旧版落盘线索可能缺 sources/profile/opportunity（如 other_event
    # 命中的、或调研补全晚于落盘的），从企业档案与 enrichments 契约补齐，
    # 保证卡片信息面完整、来源可溯源。
    import bitable as bitable_mod
    enrichments = bitable_mod.load_enrichments(ws)
    for ld in leads:
        if not ld.get("sources"):
            fact = facts_mod.load_fact(ws, ld.get("entity_id")) or {}
            fresh = lead_sources(fact)
            if fresh:
                ld["sources"] = fresh
                ld.setdefault("url", fresh[0]["url"])
        enr = enrichments.get(ld.get("entity_id")) or {}
        for k in ("profile", "opportunity"):
            if not ld.get(k) and enr.get(k):
                ld[k] = enr[k]
    notify_cfg = load_notify(ws)
    view_url = (notify_cfg.get("bitable") or {}).get("view_url")
    fresh, exists_leads = lead_card_split(leads)
    p0_leads = [l for l in fresh if l.get("priority") == "P0"]
    blocks = lead_blocks(leads, view_url=view_url)
    note = "已登记猎隼线索池，点「查看线索」直达对应记录。"
    if exists_leads:
        note += ("标注「已存在」的 %d 条为此前已登记（本次未重复写入），"
                 "不再群@建联。" % len(exists_leads))
    blocks.append({"tag": "markdown",
                   "content": "<font color='grey'>%s</font>" % note,
                   "text_size": "notation"})
    card = build_card("潜在客户线索", core.now_iso().replace("T", " ")[:16] + " · 已入池",
                      blocks, template="red" if p0_leads else "green",
                      tags=[("P0×%d" % len(p0_leads), "red")] if p0_leads
                      else [("%d 条线索" % len(leads), "green")])
    path = write_card(ws, "card-leads-%s.json" % stamp(), card)
    gpath = None
    if p0_leads:
        # 群卡：仅全新 P0（exists 是重复提示，绝不 @所有人 催建联）
        gblocks = lead_blocks(p0_leads, view_url=view_url)
        gels = ([e] if (e := _mention_lead("⚠️ P0 线索请尽快建联", notify_cfg.get("notify"))) else []) + gblocks
        gcard = build_card("潜在客户线索 · P0 优先建联", core.now_iso().replace("T", " ")[:16],
                           gels, template="red",
                           tags=[("%d 条 P0 线索，请尽快跟进" % len(p0_leads), "red")])
        gpath = write_card(ws, "card-leads-%s-group.json" % stamp(), gcard)
    located = sum(1 for l in leads if l.get("bitable_record_id"))
    parts = _flatten([path])
    print("线索卡已重产: %s（%d 条中 %d 条带记录级定位；全新 %d 条、已存在标注 %d 条%s）"
          % ("、".join(parts), len(leads), located, len(fresh), len(exists_leads),
             ("；群卡: %s" % "、".join(_flatten([gpath]))) if gpath else "；无全新 P0，本轮不产群卡"))
    if len(parts) > 1:
        print("[注意] 线索卡已分片为 %d 张（超出飞书 30KB 限制），发送时必须全部发出。" % len(parts))
    return 0


# ---------- 每日摘要（delta 锚点式，沿用 change-watch 的宁重勿漏） ----------

def cmd_digest(ws, commit=False, hero_image=None, publish=False, publish_target=None,
               publish_dry=False, publish_runner=None):
    """产内参卡；(commit) 推进锚点；(publish) 发送并按结果自动推锚。

    `--publish` 的存在意义（2026-10-28 用户明令）：业务人员只触发、不懂也不该
    手工跑 `--commit`。漏跑 commit 会让同一批内容在下一轮再推一遍，因此把
    「发送成功 → 推锚」做成一个原子动作：全部目标/全部得分片都送达才确认，
    任一片失败则保留清单、下次 `--publish` 续发（详见 publish.py 头部纪律）。
    """
    now = core.now_iso()
    anchor_rec = core.jload(os.path.join(ws, "state", "digest_anchor.json"), {"anchor": None})
    anchor = anchor_rec.get("anchor")
    records = [r for r in core.read_jsonl(os.path.join(ws, "log", "intel.jsonl"))
               if delivery.in_window(r, "change_id", anchor_rec)]
    # 已通报压制（sent-guard 标记）的内容不进内参：洁净重置全量窗复判时，
    # 旧闻会被重新打包重复推送（2026-10-27 实测拦截后代码化）；AI 判定
    # notify=false 的记录不在此列——内参保留其归档展示语义。
    records = [r for r in records if not r.get("suppressed_by_guard")]
    leads = [l for l in core.read_jsonl(os.path.join(ws, "log", "leads.jsonl"))
             if delivery.in_window(l, "lead_id", anchor_rec)]
    # 产卡前的最后一道池对齐 + 已跟进线索剔除（陷阱 74）：
    # digest 路径此前没有池状态检查，历史线索（锚点未推进期间）会每轮重复进卡。
    leads = _reconcile_digest_leads(ws, leads)
    # 连用陷阱代码化（2026-10-27 实测坑）：旧实现 --commit 分支提前 return，
    # 导致 --digest --commit 连用只推进锚点、摘要卡从未产出，且无任何提示——
    # 修复为「先产卡、后推进锚点」，连用语义变为产卡+推锚一步完成。
    #
    # v0.6.6 补：`--commit` **不该重新产卡**。标准流程是「--digest 产卡 → 发送 → --commit 推锚」，
    # commit 时若再产一套新卡，会在 out/ 留下**一批没人发过的重复卡片**：
    #   ① 冗余占用（实测一次多出 2 张 40KB 卡片）；
    #   ② 误发风险——运维看到目录里有两批卡，分不清哪批是已发的那批。
    # 故 commit 时若存在待发送清单（说明本批卡已产出、可能已发出），直接复用其卡片路径，
    # 只推锚不重产；清单缺失（说明是「一步到位」用法）才按老行为连产带推。
    batch_prev = core.jload(os.path.join(ws, "state", "digest_pending_send.json"), None)
    # publish 也走复用：上次发送中断留下的清单必须原样续发，不能重新产卡
    # （重新产卡会改内容哈希 → 幂等键失效 → 已送达的分片被重复推送）。
    reuse = bool((commit or publish) and batch_prev and (batch_prev.get("cards") or []))
    by_type = {}
    for r in records:
        by_type[r.get("type") or "未分类"] = by_type.get(r.get("type") or "未分类", 0) + 1
    # 正式版式：需求文档三段式内参卡；三段全空或渲染异常时降级 simple 版式（宁重勿漏）
    path = None
    view_url = ((load_notify(ws).get("bitable") or {}).get("view_url")) or LEAD_POOL_VIEW_URL
    if reuse:
        # 缺失文件属于失败，不得降级产新卡后把旧清单一起确认。
        paths = list(batch_prev["cards"])
        if any(not os.path.isfile(p) for p in paths):
            raise SystemExit("[digest] 待发送分片缺失，拒绝提交；清单已保留")
        print("复用待发送清单中的卡片（不重产）: %s" % "、".join(os.path.basename(p) for p in paths))
    else:
        try:
            banner_key = resolve_banner_key(ws, hero_image)
            if banner_key:
                print("内参头图: 自定义 %s" % banner_key)
            card = briefing_card(records, leads, view_url=view_url, banner_key=banner_key)
            if card is not None:
                path = write_card(ws, "card-daily-%s.json" % stamp(), card)
        except Exception as exc:  # noqa: BLE001
            sys.stderr.write("[fallback] 内参卡渲染失败，降级 simple 版式: %s\n" % exc)
        paths = _flatten([path])

    if not paths:
        if not records and not leads:
            # 全空不产空卡（与 SKILL.md 纪律一致）：briefing 版式三段全空返回
            # None 后，simple 降级分支也不得产出"0 条动态"占位卡
            # 没有待确认批次时重复提交是空操作，保留新锚的同秒边界信息。
            print("窗口内无增量（情报 0、线索 0），不产空卡、不推进锚点")
            return 0
        blocks = [_block(["**本时段市场情报 %d 条**，新线索 %d 条" % (len(records), len(leads)),
                          "、".join("%s %d" % (k, v) for k, v in sorted(by_type.items(), key=lambda x: -x[1])) or "无"],
                         color="violet")]
        # v0.6.5 修：此处旧实现 `[:4]` 预截断，再交给 intel_blocks —— 双保险失效。
        # simple 是**降级兜底**路径（briefing 渲染异常时才走），此时更不能丢内容：
        # 锚点随后即推进，被截掉的 notify=true 情报永久消失。交给 intel_blocks
        # 自己做 notify 豁免（该函数已改为 notify=true 全展示）。
        blocks += intel_blocks(records)[0]
        blocks.append({"tag": "markdown",
                       "content": "<font color='grey'>窗口起点：%s</font>" % (anchor or "首次运行"),
                       "text_size": "notation"})
        card = build_card("市场情报日报", core.now_iso().replace("T", " ")[:16], blocks,
                          template="violet",
                          tags=[("%d 条动态" % len(records), "violet"), ("%d 条新线索" % len(leads), "green")],
                          icon={**INTEL_ICON, "color": "violet"})
        paths = _flatten([write_card(ws, "card-digest-%s.json" % stamp(), card)])
    print("摘要卡已产出: %s" % "、".join(os.path.basename(str(p)) for p in paths))
    if len(paths) > 1:
        # 分片必须全发：只发第一片等于丢掉后面所有情报（比超限报错更隐蔽）
        print("[注意] 摘要卡已分片为 %d 张（超出飞书 30KB 限制），**必须按顺序全部发送**；"
              "漏发任何一片，该片内容在锚点推进后即永久丢失。" % len(paths))
    # v0.6.6：**产卡时落待发送清单**。它的作用是给「发送 → 核对 → 推锚」这个三步链条
    # 留一份可核对凭证：发送失败时能凭它知道该重发哪些卡、覆盖哪些记录。
    # 旧实现只在 commit 分支推进锚点、不留凭据，一旦发送失败（如卡片超限被拒收），
    # 内容已被标「已通报」→ 永久丢失且无从追溯。
    # 注意：commit 且复用清单时**不要覆盖清单**（records 此刻已含本批，覆盖会污染覆盖范围）。
    batch = os.path.join(ws, "state", "digest_pending_send.json")
    if not reuse:
        batch_prev = delivery.manifest(list(paths), records, leads, anchor, now)
        core.jdump(batch, batch_prev)
    if publish and not commit:
        # 自动发送 + 按送达结果推锚：业务人员只触发，不需要也不应该手工 commit。
        # 全部目标/全部分片送达 → 等价于「确认已送达」→ 立即 confirm 推锚；
        # 任一片失败 → 不推锚、清单保留，下次 --publish 续发（幂等键保证不重发）。
        import publish as publish_mod
        try:
            targets = publish_mod.resolve_targets(load_notify(ws), publish_target)
        except ValueError as exc:
            sys.stderr.write("[publish] 发送目标配置错误，未发送、未推锚: %s\n" % exc)
            return 5
        ok, batch_prev = publish_mod.send_batch(
            ws, batch_prev, targets=targets, runner=publish_runner, dry_run=publish_dry)
        if publish_dry:
            print("[publish][dry-run] 沙箱预演完成，未真实发送、未推锚")
            return 0
        if not ok:
            sys.stderr.write(
                "[publish] 有分片未送达：锚点**未推进**、待发送清单已保留。\n"
                "          修复后重跑 `judge.py --publish` 即续发（已送达分片会跳过，"
                "幂等键防重复），无需重新产卡。\n")
            return 5
        commit = True  # 送达即确认
    if commit:
        new_anchor = delivery.confirm(ws, batch_prev, anchor_rec, now)
        print("摘要锚点已推进到: %s" % new_anchor)
        if publish:
            print("[publish] 发送已确认，锚点与「已通报」标记同步完成——"
                  "本批内容不会在下一期内参重复出现。")
        else:
            print("[注意] 锚点已推进且本批记录已标记为「已通报」——若这张卡后续发送失败，"
                  "这批内容不会再次出现在下期内参里（防重发优先于防漏发）。"
                  "因此**正确顺序是：先 --digest 产卡 → 发送成功 → 再 --digest --commit**；"
                  "只有确认卡已送达时才可连用。")
        try:
            os.remove(batch)  # 已发送并推锚：凭据完成使命
        except OSError:
            pass
    else:
        print("待发送清单已落盘: %s（共 %d 张，发送成功后跑 --digest --commit）"
              % (batch, len(paths)))
    return 0


def cmd_publish(ws, publish_target=None, publish_dry=False, publish_runner=None):
    """续发既有待发送清单并推锚（`--publish` 不带 `--digest`）。

    用途：上一次 `--digest --publish` 发送中途失败/进程被杀后，业务侧再触发一次
    补偿即可——已送达的分片按幂等键跳过，只补发缺失部分，全部成功才推锚。
    """
    import publish as publish_mod
    batch = publish_mod.load_batch(ws)
    if not batch:
        print("没有待发送批次：请先 `judge.py --digest --publish` 产卡并发送。")
        return 0
    try:
        targets = publish_mod.resolve_targets(load_notify(ws), publish_target)
    except ValueError as exc:
        sys.stderr.write("[publish] 发送目标配置错误，未发送、未推锚: %s\n" % exc)
        return 5
    print("续发待发送清单: %s" % "、".join(os.path.basename(p) for p in batch.get("cards") or []))
    ok, batch = publish_mod.send_batch(ws, batch, targets=targets,
                                       runner=publish_runner, dry_run=publish_dry)
    if publish_dry:
        print("[publish][dry-run] 预演完成，未真实发送、未推锚")
        return 0
    if not ok:
        sys.stderr.write("[publish] 仍有分片未送达：锚点未推进，清单保留，可再次重试。\n")
        return 5
    anchor_rec = core.jload(os.path.join(ws, "state", "digest_anchor.json"), {"anchor": None})
    new_anchor = delivery.confirm(ws, batch, anchor_rec, core.now_iso())
    print("摘要锚点已推进到: %s（补发完成，本批不会重复出现）" % new_anchor)
    try:
        os.remove(os.path.join(ws, "state", "digest_pending_send.json"))
    except OSError:
        pass
    return 0


def _advance_anchor(ws, anchor, now, mark_sent=True):
    """推进摘要锚点；mark_sent=True 时顺带给窗口内 notify 记录补"已通报"标记。

    2026-10-27 实测缺口：锚点推进意味着这批内容已随内参卡发出，但旧实现只改
    anchor、不打 suppressed_by_guard，于是这些记录永远留着 notify=true 且无标记。
    平时无害（锚点之后才会进窗口），但**一旦锚点被重置/回滚，它们会以旧闻身份
    重新推送到内参卡**。已发卡不可能是"未发"，故在推进锚点的同一动作里补标，
    让"锚点覆盖"与"已通报契约"永远一致，不依赖人工记得传 --already-sent。
    """
    if mark_sent:
        path = os.path.join(ws, "log", "intel.jsonl")
        rows = core.read_jsonl(path)
        anchor_ts = core.parse_iso(anchor) or 0
        now_ts = core.parse_iso(now) or 0
        changed = False
        for r in rows:
            if r.get("discard") or r.get("suppressed_by_guard"):
                continue
            ts = core.parse_iso(r.get("logged_at")) or 0
            # 用 >= 而非 >：锚点本身是「已覆盖到的时刻」，落在锚点上的记录
            # 同样属于本期已发（2026-10-27 实测：6 条记录的 logged_at 恰好
            # 等于锚点，用 > 永远补不上，留下 notify=true 的旧闻重推隐患）。
            if ts and ts >= anchor_ts and ts <= now_ts:
                # 窗口内（旧锚点及之后、本次推进之前）的记录：已随本期内参发出
                r["suppressed_by_guard"] = True
                if r.get("notify"):
                    r["notify"] = False
                changed = True
        if changed:
            with open(path, "w", encoding="utf-8") as f:
                for r in rows:
                    f.write(json.dumps(r, ensure_ascii=False) + "\n")
    # 锚点推进逻辑抽出复用（intel + leads 双覆盖）
    stamps = [r.get("logged_at") for r in core.read_jsonl(os.path.join(ws, "log", "intel.jsonl"))] \
        + [l.get("logged_at") for l in core.read_jsonl(os.path.join(ws, "log", "leads.jsonl"))]
    new_anchor = max([a for a in stamps if a] + [anchor or ""])
    core.jdump(os.path.join(ws, "state", "digest_anchor.json"), {"anchor": new_anchor, "committed_at": now})
    return new_anchor


# ---------- 状态简报 ----------

def cmd_status(ws):
    latest = core.jload(os.path.join(ws, "out", "latest.json"), None)
    if not latest:
        raise SystemExit("[error] 尚无扫描结果（先跑 pipeline.py --run）")
    health = latest.get("health", {})
    down = [k for k, v in health.items() if v.get("status") == "down"]
    stale = latest.get("summary", {}).get("stale_sources") or []
    lines = ["**扫描状态**", "来源 %d 个 · 失败 %d · 停更 %d" % (
        latest.get("summary", {}).get("sources_total", 0), len(down), len(stale))]
    if down:
        lines.append("不可用：%s" % "、".join(sorted(down)))
    if stale:
        lines.append("停更：%s" % "、".join(s.get("label") or s.get("id") for s in stale if isinstance(s, dict)))
    card = build_card("市场情报 · 扫描状态", core.now_iso().replace("T", " ")[:16],
                      [_block(lines, color="yellow" if (down or stale) else "green")],
                      template="yellow" if (down or stale) else "green",
                      tags=[("正常", "green") if not (down or stale) else ("有异常", "yellow")])
    path = write_card(ws, "card-status-%s.json" % stamp(), card)
    print("状态卡已产出: %s" % "、".join(str(p) for p in _flatten([path])))
    return 0


def main():
    ap = argparse.ArgumentParser(description="market-intel 判断落盘与线索产出")
    ap.add_argument("--workspace")
    ap.add_argument("--input", help="judged.json 路径")
    ap.add_argument("--already-sent",
                    help="旧 intel.jsonl 路径（洁净重置防重发）：命中的已发情报强制 notify=false")
    ap.add_argument("--sent-before",
                    help="ISO 时刻（传洁净重置前的 digest 锚点）：旧登记中该时刻之后落盘的 "
                         "notify=true 记录从未随内参发出，不算已发（防漏发，宁重勿漏）")
    ap.add_argument("--dry-run", action="store_true",
                    help="沙箱预检：临时副本上完整演练 --input 流程并报告问题，真实工作区零写入")
    ap.add_argument("--digest", action="store_true", help="产出每日摘要卡（delta 窗口）")
    ap.add_argument("--commit", action="store_true", help="与 --digest 连用：推进摘要锚点")
    ap.add_argument("--publish", action="store_true",
                    help="发送内参卡并在全部送达后自动推锚（业务侧只触发、无需手工 commit）。"
                         "与 --digest 连用=产卡+发送+推锚一步完成；单独使用=续发上次未送达的清单")
    ap.add_argument("--publish-target",
                    help="发送目标: user=本人私信(bot) | chat=群(user) | both=两者；"
                         "缺省读 notify.json 的 digest.publish_target，再退 user")
    ap.add_argument("--publish-dry-run", action="store_true",
                    help="与 --publish 连用：只预演发送参数，不真实送达、不推锚")
    ap.add_argument("--hero-image",
                    help="内参自定义头图：本地图片路径（自动上传飞书换 image_key 并按文件"
                         "指纹缓存，≤10M、高:宽 ≤16:9）或现成 img_v3 key；"
                         "未传时用 notify.json 的 notify.hero_image，再退回内置默认图。"
                         "替换后先私信预览确认再发群（发送纪律）")
    ap.add_argument("--status-card", action="store_true", help="产出扫描状态简报卡")
    ap.add_argument("--research-check", action="store_true",
                    help="调研完整度审计：逐企业核对 14 必研字段，未达标退出码 2")
    ap.add_argument("--bitable", action="store_true",
                    help="把 status=new 的线索构建为猎隼线索池待写记录（预览）；加 --commit 才真正写入")
    ap.add_argument("--lead-card", action="store_true",
                    help="猎隼池写入后重产最近一轮线索卡（「查看线索」直达记录）")
    args = ap.parse_args()
    ws = core.workspace(args.workspace)
    if args.lead_card:
        raise SystemExit(cmd_lead_card(ws))
    if args.research_check:
        import bitable
        raise SystemExit(2 if bitable.audit_enrichments(ws) else 0)
    if args.bitable:
        import bitable
        cfg = (core.jload(os.path.join(ws, "notify.json"), {}) or {}).get("bitable") or {}
        if args.commit:
            # 自动入池模式：先重建预览吸收最新调研（离线），再直接写入。
            # 不再要求「人工确认预览」断点——调研 14 必研字段硬门槛 + 去重 +
            # 选项校验 + blocked 留痕就是安全边界，人工事后在表内核查修正。
            items = bitable.build_pending(ws, cfg)
            if not items:
                print("没有待写线索（leads.jsonl 中 status=new 的已全部处理）。")
                raise SystemExit(0)
            pushed, exists, failed, blocked = bitable.commit(ws, cfg)
            print("猎隼线索池写入完成：新增 %d、已存在跳过 %d、失败 %d、调研未齐全拦截 %d"
                  % (pushed, exists, failed, blocked))
            print("表格视图: %s" % (cfg.get("view_url") or "(notify.json bitable.view_url 未配置)"))
            if blocked:
                sys.stderr.write("[bitable] 有线索因调研未齐全被拦截（先补调研再重跑本命令，"
                                 "审计命令：--research-check）\n")
            raise SystemExit(0 if (failed == 0 and blocked == 0) else (4 if blocked else 3))
        items = bitable.build_pending(ws, cfg)
        if not items:
            print("没有待写线索（leads.jsonl 中 status=new 的已全部处理）。")
            raise SystemExit(0)
        for it in items:
            print("- %s | %s" % (it["company"], json.dumps(it["record"], ensure_ascii=False)[:260]))
        print("预览已写入 %s（共 %d 条）。确认无误后加 --commit 真正写入。" % (bitable.pending_path(ws), len(items)))
        raise SystemExit(0)
    if args.digest:
        raise SystemExit(cmd_digest(ws, commit=args.commit, hero_image=args.hero_image,
                                    publish=args.publish,
                                    publish_target=args.publish_target,
                                    publish_dry=args.publish_dry_run))
    if args.publish:
        raise SystemExit(cmd_publish(ws, publish_target=args.publish_target,
                                     publish_dry=args.publish_dry_run))
    if args.status_card:
        raise SystemExit(cmd_status(ws))
    if args.input:
        raise SystemExit(cmd_input(ws, args.input, already_sent=args.already_sent,
                                   dry_run=args.dry_run, sent_before=args.sent_before))
    ap.print_help()


if __name__ == "__main__":
    main()
