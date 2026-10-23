#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""企业档案（facts）：把判断出的事件聚合到企业维度。

entity 归一沿用 AIHOT 的词表思路（config/entities.json：id -> {name, aliases, industries, domains}），
未知公司先以 unknown:<slug> 建档，待人工或后续轮次归并。
防张冠李戴沿用 change-watch / AIHOT 的 grounding 原则：事实里的公司名必须来自判断所依据的原文。

档案文件：facts/<entity_id>.json
{
  "entity_id", "name", "aliases", "industries",
  "funding_events":  [{date, round, amount_text, amount_cny, investors, source_name, source_url, confidence, extracted_at}],
  "hiring_signals":  [{keywords, page_url, first_seen, last_seen}],
  "other_events":    [{kind, detail, source_url, extracted_at}],
  "updated_at"
}
"""

import os
import re

import core


def facts_dir(ws):
    return os.path.join(ws, "facts")


def load_entities(ws):
    rec = core.jload(os.path.join(ws, "entities.json"), None)
    if rec is None:
        rec = core.jload(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                      "..", "config", "entities.json.example"), None)
    return rec or {"industries": [], "entities": {}}


def slugify(name):
    s = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "-", (name or "").strip()).strip("-").lower()
    return s or "unknown"


def normalize_entity(name, entities):
    """按词表归一：先精确别名，再包含匹配（公司别名都较短，包含匹配足够且便于命中全称）。

    entities 可传词表整体（{"entities": {...}}）或直接传 entities 字典，两种都兼容。
    命中返回 (entity_id, entity_rec)；未命中返回 ("unknown:<slug>", 最小建档)。
    """
    if isinstance(entities, dict) and isinstance(entities.get("entities"), dict):
        entities = entities["entities"]
    target = core.norm(name)
    if not target:
        return "unknown:unnamed", {"name": name or "未命名", "aliases": [], "industries": []}
    for eid, rec in (entities or {}).items():
        names = [rec.get("name", "")] + list(rec.get("aliases", []))
        if target in [core.norm(n) for n in names if n]:
            return eid, rec
    for eid, rec in (entities or {}).items():
        names = [rec.get("name", "")] + list(rec.get("aliases", []))
        if any(n and n in target for n in names if n):
            return eid, rec
    slug = "unknown:" + slugify(target)
    return slug, {"name": target, "aliases": [target], "industries": []}


def fact_path(ws, entity_id):
    safe = entity_id.replace(":", "__")
    return os.path.join(facts_dir(ws), safe + ".json")


def load_fact(ws, entity_id):
    return core.jload(fact_path(ws, entity_id), None)


def apply_fact(ws, fact, entities, now):
    """把一条判断出的事实并入企业档案。返回 entity_id。

    fact 契约（AI 按 references/lead-criteria.md 产出）：
    {"entity_name", "aliases"?, "industries"[], "event_type": funding|hiring|other,
     "event": {...}, "change_ids"[], "confidence": high|medium|low}
    """
    entity_id, erec = normalize_entity(fact.get("entity_name"), entities)
    path = fact_path(ws, entity_id)
    rec = core.jload(path, None) or {
        "entity_id": entity_id, "name": erec.get("name") or fact.get("entity_name"),
        "aliases": [], "industries": [], "funding_events": [], "hiring_signals": [],
        "other_events": [],
    }
    # 词表里的官方信息优先；unknown 档案允许被事实补充
    if not entity_id.startswith("unknown:"):
        rec["name"] = erec.get("name") or rec.get("name")
    # origin / going_global：词表标注 > 判断申报 > 保留既有值（只升不降，避免覆盖）
    if erec.get("origin"):
        rec["origin"] = erec["origin"]
    elif fact.get("origin"):
        rec["origin"] = fact["origin"]
    if erec.get("going_global") is not None:
        rec["going_global"] = bool(erec["going_global"])
    elif fact.get("going_global"):
        rec["going_global"] = True
    # 词表实体的官方行业同样优先并入（判断给出的行业增量保留）
    for ind in (erec.get("industries") or []) + (fact.get("industries") or []):
        if ind and ind not in rec["industries"]:
            rec["industries"].append(ind)
    # AI 判断给出的画像与机会点（可选，供卡片与猎隼池字段使用）
    for k in ("profile", "opportunity"):
        if fact.get(k) and fact.get(k) != rec.get(k):
            rec[k] = fact[k]
    rec.setdefault("aliases", [])
    for a in [fact.get("entity_name")] + list(fact.get("aliases") or []):
        if a and a not in rec["aliases"]:
            rec["aliases"].append(a)

    event = fact.get("event") or {}
    etype = fact.get("event_type", "other")
    if etype == "funding":
        dedupe = core.sha1(core.norm_url(event.get("source_url") or "") + "|" +
                           (event.get("date") or "") + "|" + (event.get("round") or ""))
        if not any(fe.get("_dedupe") == dedupe for fe in rec["funding_events"]):
            rec["funding_events"].append({
                "_dedupe": dedupe,
                "date": event.get("date"),
                "round": event.get("round"),
                "amount_text": event.get("amount_text"),
                "amount_cny": core.parse_amount_cny(event.get("amount_text")),
                "investors": event.get("investors"),
                "source_name": event.get("source_name"),
                "source_url": event.get("source_url"),
                "confidence": fact.get("confidence", "medium"),
                "extracted_at": now,
            })
    elif etype == "hiring":
        kws = sorted({k for k in (event.get("keywords") or fact.get("keywords_hit") or []) if k})
        page_url = core.norm_url(event.get("page_url") or "")
        hit = None
        for hs in rec["hiring_signals"]:
            if core.norm_url(hs.get("page_url") or "") == page_url and sorted(hs.get("keywords") or []) == kws:
                hit = hs
                break
        if hit:
            hit["last_seen"] = now
        else:
            rec["hiring_signals"].append({"keywords": kws, "page_url": page_url or event.get("page_url"),
                                          "first_seen": now, "last_seen": now})
    else:
        rec["other_events"].append({
            "kind": event.get("kind", "other"),
            "detail": event.get("detail", ""),
            "source_url": event.get("source_url"),
            "confidence": fact.get("confidence", "low"),
            "extracted_at": now,
        })

    rec["updated_at"] = now
    core.jdump(path, rec)
    return entity_id
