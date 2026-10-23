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
import facts as facts_mod
import match as match_mod

INTEL_KEYS = {"change_id", "type", "significance", "object", "title", "summary", "url",
              "sources", "notify", "notify_reason", "merged_change_ids",
              "discard", "discard_reason"}
NOTIFY_KEYS = {"version", "notify", "digest", "bitable"}
NOTIFY_BLOCK_KEYS = {"user_id", "chat_id", "mention"}

SIG_LABEL = {"high": ("重要", "red"), "normal": ("常规", "blue"), "low": ("次要", "neutral")}
SIG_ORDER = {"high": 0, "normal": 1, "low": 2}


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
    probe = (rec.get("title") or "") + " " + (rec.get("summary") or "")
    src_nums = set(re.findall(r"\d+(?:\.\d+)?", src_text))
    declared = rec.get("number_notes") or {}
    variants = _conv_variants({float(x) for x in src_nums})
    grounded_notes, unverified = {}, []
    for n in set(re.findall(r"\d+(?:\.\d+)?", probe)) - src_nums:
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


# ---------- 覆盖度校验 ----------

def coverage_check(latest, intel, fact_records):
    """每个 change_id 必须被 intel（含 merged/discard）或 facts.change_ids 覆盖。返回缺失列表。"""
    changes = (latest or {}).get("changes") or []
    covered = set()
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

CARD_CONFIG = {
    "schema": "2.0",
    "config": {"update_multi": True, "width_mode": "default",
               "style": {"text_size": {
                   "title": {"default": "heading-2", "pc": "heading-2", "mobile": "heading-3"},
                   "body": {"default": "normal", "pc": "normal", "mobile": "normal"},
                   "caption": {"default": "notation", "pc": "notation", "mobile": "notation"}}}},
}


def _block(md_lines, color="blue", last=False):
    return {"tag": "interactive_container", "width": "fill", "has_border": True,
            "border_color": color + "-100", "corner_radius": "8px", "background_style": color + "-50",
            "padding": "12px 12px 12px 12px", "vertical_spacing": "4px",
            "margin": "0px 0px 12px 0px" if not last else "0px",
            "elements": [{"tag": "markdown", "content": m} for m in md_lines]}


def build_card(title, subtitle, blocks, template="blue", tags=None):
    header_tags = [{"tag": "text_tag", "text": {"tag": "plain_text", "content": text}, "color": color}
                   for text, color in (tags or [])]
    return {
        **CARD_CONFIG,
        "header": {"title": {"tag": "plain_text", "content": title},
                   "subtitle": {"tag": "plain_text", "content": subtitle},
                   "template": template,
                   "text_tag_list": header_tags},
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
    """情报块：重要 -> 常规 -> 次要；块数超限收进末行（纪律与 change-watch 一致）。"""
    ordered = sorted(records, key=lambda r: SIG_ORDER.get(r.get("significance"), 3))
    shown, blocks = ordered[:limit], []
    for r in shown:
        sig_label, sig_color = SIG_LABEL.get(r.get("significance"), ("常规", "blue"))
        head = "<text_tag color='%s'>%s</text_tag> <text_tag color='neutral'>%s</text_tag> **%s**" % (
            sig_color, sig_label, r.get("type") or "动态", r.get("title") or "")
        blocks.append(_block([head, r.get("summary") or "", _sources_line(r.get("sources") or [])]))
    if not blocks:
        return blocks, 0
    if len(ordered) > limit:
        blocks.append({"tag": "markdown",
                       "content": "<font color='grey'>另有 %d 条次要动态</font>" % (len(ordered) - limit),
                       "text_size": "notation"})
    else:
        blocks[-1]["margin"] = "0px"
    return blocks, len(ordered)


def lead_blocks(leads, limit=5):
    shown = leads[:limit]
    blocks = []
    for ld in shown:
        head = "<text_tag color='blue'>线索</text_tag> <text_tag color='neutral'>%s</text_tag> **%s** · 匹配度 %d" % (
            "/".join(ld.get("industries") or []) or "未分类", ld.get("entity_name") or "未命名", ld.get("score", 0))
        rules_line = "命中规则：" + "；".join(ld.get("matched_group_names") or ld.get("matched_groups") or [])
        ev_lines = [e.get("detail") for e in (ld.get("evidence") or []) if e.get("detail")][:3]
        body = rules_line + ("：" + "；".join(ev_lines) if ev_lines else "")
        blocks.append(_block([head, body, _sources_line(ld.get("sources") or [])], color="green"))
    if len(leads) > limit:
        blocks.append({"tag": "markdown",
                       "content": "<font color='grey'>另有 %d 条线索</font>" % (len(leads) - limit),
                       "text_size": "notation"})
    if blocks:
        blocks[-1]["margin"] = "0px"
    return blocks


def write_card(ws, name, card):
    path = os.path.join(ws, "out", name)
    core.jdump(path, card)
    return path


# ---------- 主流程：judged.json 落盘 ----------

def cmd_input(ws, input_path):
    now = core.now_iso()
    latest = core.jload(os.path.join(ws, "out", "latest.json"), None)
    judged = core.jload(input_path, None)
    if not judged:
        raise SystemExit("[error] 读不到判断文件: %s" % input_path)
    intel = judged.get("intel") or []
    fact_records = judged.get("facts") or []
    for r in intel:
        core.check_keys(r, INTEL_KEYS, "intel record")

    # 1) 覆盖度校验：漏答一条 = 判断不完整（latest.json 缺失时显式告警，不做静默全过）
    if latest is None:
        sys.stderr.write("[warn] out/latest.json 缺失，本轮不做覆盖度校验（手工补录场景）\n")
        missing = []
    else:
        missing = coverage_check(latest, intel, fact_records)
    if missing:
        sys.stderr.write("[coverage] 以下 change_id 未判（补全后重跑，pending 已保留）:\n  %s\n"
                         % "\n  ".join(missing))
        raise SystemExit(2)
    changes_by_id = {c["change_id"]: c for c in ((latest or {}).get("changes") or [])}

    entities = facts_mod.load_entities(ws)
    rules = match_mod.load_rules(ws)
    notify_cfg = load_notify(ws)

    # 2) 情报流水（intel + discard 分流落盘；按 change_id 幂等——修复后重跑不产生重复记录）
    seen_intel = {r.get("change_id") for r in
                  core.read_jsonl(os.path.join(ws, "log", "intel.jsonl"))}
    seen_discard = {r.get("change_id") for r in
                    core.read_jsonl(os.path.join(ws, "log", "discarded.jsonl"))}
    notified_intel = []
    for r in intel:
        if r.get("discard"):
            if r.get("change_id") in seen_discard:
                continue
            seen_discard.add(r.get("change_id"))
            core.append_jsonl(os.path.join(ws, "log", "discarded.jsonl"),
                              {"change_id": r.get("change_id"), "reason": r.get("discard_reason") or "未说明",
                               "logged_at": now})
            continue
        if r.get("change_id") in seen_intel:
            continue
        seen_intel.add(r.get("change_id"))
        rec = {k: r.get(k) for k in INTEL_KEYS if k in r and not k.startswith("_")}
        rec["logged_at"] = now
        ch = changes_by_id.get(r.get("change_id"))
        nums, grounded_notes = grounding_check(r, ch)
        if grounded_notes:
            rec["grounding_notes"] = grounded_notes  # 换算备注留档，供人工审计
        if nums:
            rec["grounding"] = "unverified"
            sys.stderr.write("[grounding] change %s 的说明含原文没有的数字: %s（请回原文核实，"
                             "若为单位/汇率换算可在 number_notes 申报）\n"
                             % (r.get("change_id"), ",".join(nums)))
        rec.setdefault("significance", "unknown")
        if rec["significance"] == "unknown":
            rec["notify"] = False  # 没判断过的东西不许发（脚本强制）
        core.append_jsonl(os.path.join(ws, "log", "intel.jsonl"), rec)
        if rec.get("notify"):
            notified_intel.append(rec)

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
    new_leads = []
    existing_keys = {l.get("dedupe_key") for l in core.read_jsonl(os.path.join(ws, "log", "leads.jsonl"))}
    threshold = match_mod.threshold(rules)
    group_at = match_mod.notify_group_at(rules)
    for eid in sorted(touched):
        fact = facts_mod.load_fact(ws, eid)
        if not fact:
            continue
        result = match_mod.evaluate_entity(fact, rules)
        if not result["matched_groups"]:
            continue
        dedupe = core.sha1(eid + "|" + ",".join(sorted(result["matched_groups"])))
        lead = {
            "lead_id": core.sha1(eid + "|" + now)[:12],
            "dedupe_key": dedupe,
            "entity_id": eid,
            "entity_name": fact.get("name") or eid,
            "industries": fact.get("industries") or [],
            "matched_groups": result["matched_groups"],
            "matched_group_names": result.get("matched_group_names") or result["matched_groups"],
            "score": result["score"],
            "evidence": result["evidence"],
            "summary": lead_summary(fact),
            "url": lead_source_url(fact),
            "sources": lead_sources(fact),
            "notify": result["score"] >= threshold,
            "status": "new",
            "logged_at": now,
        }
        recent_cut = time.time() - 7 * 86400
        dup = dedupe in existing_keys and any(
            (core.parse_iso(l.get("logged_at")) or 0) > recent_cut
            for l in core.read_jsonl(os.path.join(ws, "log", "leads.jsonl"))
            if l.get("dedupe_key") == dedupe)
        if dup:
            continue  # 7 天内已报过同组合线索：不重复打扰
        core.append_jsonl(os.path.join(ws, "log", "leads.jsonl"), lead)
        existing_keys.add(dedupe)
        if lead["notify"]:
            new_leads.append(lead)

    core.jdump(os.path.join(ws, "out", "leads-latest.json"),
               {"run_at": now, "leads": new_leads,
                "summary": {"new_leads": len(new_leads), "matched_entities": len(touched)}})

    # 5) 产卡（脚本产卡、直接发送；群卡仅高分/高优时出现）
    cards = []
    if notified_intel:
        blocks, total = intel_blocks(notified_intel)
        tags = [("%d 条动态" % total, "blue")]
        if any(r.get("significance") == "high" for r in notified_intel):
            tags.append(("含重要动态", "red"))
        card = build_card("市场情报简报", core.now_iso().replace("T", " ")[:16] + " · 本轮判定",
                          blocks + [{"tag": "markdown", "text_size": "notation",
                                     "content": "<font color='grey'>明细已归档，可按时间线 / 类型 / 对象检索。</font>"}],
                          tags=tags)
        cards.append(write_card(ws, "card-intel-%s.json" % stamp(), card))
        if any(r.get("significance") == "high" for r in notified_intel):
            gcard = build_card("市场情报简报 · 重要", core.now_iso().replace("T", " ")[:16],
                               blocks, template="red", tags=tags)
            cards.append(write_card(ws, "card-intel-%s-group.json" % stamp(), gcard))
    if new_leads:
        group_leads = [l for l in new_leads if l["score"] >= group_at]
        blocks = lead_blocks(new_leads)
        card = build_card("潜在客户线索", core.now_iso().replace("T", " ")[:16] + " · 规则匹配命中",
                          blocks + [{"tag": "markdown",
                                     "content": "<font color='grey'>确认后可标记状态，线索档案可按企业检索。</font>",
                                     "text_size": "notation"}],
                          template="green", tags=[("%d 条线索" % len(new_leads), "green")])
        cards.append(write_card(ws, "card-leads-%s.json" % stamp(), card))
        if group_leads:
            gcard = build_card("潜在客户线索 · 高分", core.now_iso().replace("T", " ")[:16],
                               lead_blocks(group_leads), template="green",
                               tags=[("%d 条高分线索，请关注" % len(group_leads), "red")])
            cards.append(write_card(ws, "card-leads-%s-group.json" % stamp(), gcard))

    # 6) 核销待判队列
    judged_ids = set()
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
    for p in cards:
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
    for hs in fact.get("hiring_signals") or []:
        if hs.get("page_url"):
            out.append({"name": "招聘页", "url": hs["page_url"]})
    return out[:3]


def lead_source_url(fact):
    src = lead_sources(fact)
    return src[0]["url"] if src else None


def stamp():
    return time.strftime("%m%d-%H%M")


# ---------- 每日摘要（delta 锚点式，沿用 change-watch 的宁重勿漏） ----------

def cmd_digest(ws, commit=False):
    now = core.now_iso()
    anchor_rec = core.jload(os.path.join(ws, "state", "digest_anchor.json"), {"anchor": None})
    anchor = anchor_rec.get("anchor")
    records = [r for r in core.read_jsonl(os.path.join(ws, "log", "intel.jsonl"))
               if not anchor or (core.parse_iso(r.get("logged_at")) or 0) > (core.parse_iso(anchor) or 0)]
    leads = [l for l in core.read_jsonl(os.path.join(ws, "log", "leads.jsonl"))
             if not anchor or (core.parse_iso(l.get("logged_at")) or 0) > (core.parse_iso(anchor) or 0)]
    if commit:
        all_logged = [r.get("logged_at") for r in core.read_jsonl(os.path.join(ws, "log", "intel.jsonl"))]
        new_anchor = max([a for a in all_logged if a] + [anchor or ""])
        core.jdump(os.path.join(ws, "state", "digest_anchor.json"), {"anchor": new_anchor, "committed_at": now})
        print("摘要锚点已推进到: %s" % new_anchor)
        return 0
    by_type = {}
    for r in records:
        by_type[r.get("type") or "未分类"] = by_type.get(r.get("type") or "未分类", 0) + 1
    blocks = [_block(["**本时段市场情报 %d 条**，新线索 %d 条" % (len(records), len(leads)),
                      "、".join("%s %d" % (k, v) for k, v in sorted(by_type.items(), key=lambda x: -x[1])) or "无"],
                     color="blue")]
    blocks += intel_blocks(sorted(records, key=lambda r: SIG_ORDER.get(r.get("significance"), 3))[:4])[0]
    blocks.append({"tag": "markdown",
                   "content": "<font color='grey'>窗口起点：%s</font>" % (anchor or "首次运行"),
                   "text_size": "notation"})
    card = build_card("市场情报日报", core.now_iso().replace("T", " ")[:16], blocks,
                      tags=[("%d 条动态" % len(records), "blue"), ("%d 条新线索" % len(leads), "green")])
    path = write_card(ws, "card-digest-%s.json" % stamp(), card)
    print("摘要卡已产出: %s（发送成功后请跑 --digest --commit 推进锚点）" % path)
    return 0


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
    print("状态卡已产出: %s" % path)
    return 0


def main():
    ap = argparse.ArgumentParser(description="market-intel 判断落盘与线索产出")
    ap.add_argument("--workspace")
    ap.add_argument("--input", help="judged.json 路径")
    ap.add_argument("--digest", action="store_true", help="产出每日摘要卡（delta 窗口）")
    ap.add_argument("--commit", action="store_true", help="与 --digest 连用：推进摘要锚点")
    ap.add_argument("--status-card", action="store_true", help="产出扫描状态简报卡")
    args = ap.parse_args()
    ws = core.workspace(args.workspace)
    if args.digest:
        raise SystemExit(cmd_digest(ws, commit=args.commit))
    if args.status_card:
        raise SystemExit(cmd_status(ws))
    if args.input:
        raise SystemExit(cmd_input(ws, args.input))
    ap.print_help()


if __name__ == "__main__":
    main()
