#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""差分与状态管理：基线快照、变更识别、有限窗口流、待判队列。

语义与 change-watch 对齐（同一套心智模型，运维不用换脑子）：
- change_kind: added / removed（v1 不做 changed——流式源内容修订靠"新增"重报，页面监测的关键词
  增减已由 added/removed 表达）；
- windowed 源按「窗口滚动」识别：源声明 windowed + 确有收缩迹象 + 消失条目早于当前窗口最旧条目，
  三条件同时成立才判滚动；任一不成立按真消失处理——宁可多报，不把真消失当滚动吃掉；
- 未判变更进 state/pending.json，下一轮带回（reopened: true），按未判年龄（默认 24 小时）过期，
  与采集频率彻底解耦；过期写 log/unjudged.jsonl——沉默必须有据。
"""

import os

import core

MAX_PENDING_AGE_H = 24


def stream_path(ws, source_id):
    return os.path.join(ws, "state", "streams", source_id + ".json")


def load_snapshot(ws, source_id):
    rec = core.jload(stream_path(ws, source_id), None)
    if not rec:
        return {}
    return {e["key"]: e for e in rec.get("entries", [])}


def save_snapshot(ws, source_id, entries, collected_at):
    core.jdump(stream_path(ws, source_id),
               {"collected_at": collected_at,
                "entries": [{"key": e["key"], "title": e.get("title"), "link": e.get("link"),
                             "published": e.get("published"), "summary": e.get("summary")}
                            for e in entries]})


def diff_source(prev_map, cur_entries, windowed=False):
    """差分一个源。返回 (added_entries, removed_entries, rolled_entries)。"""
    cur = {e["key"]: e for e in cur_entries}
    prev_keys = set(prev_map)
    cur_keys = set(cur)
    added = [cur[k] for k in cur_keys - prev_keys]
    removed_keys = prev_keys - cur_keys

    rolled, removed = [], []
    if windowed and removed_keys:
        cur_pubs = [core.parse_iso(cur[k].get("published")) for k in cur_keys]
        cur_pubs = [p for p in cur_pubs if p is not None]
        oldest_cur = min(cur_pubs) if cur_pubs else None
        shrink = bool(added) or len(cur_keys) < len(prev_keys)
        for k in removed_keys:
            pub = core.parse_iso(prev_map[k].get("published"))
            if oldest_cur is not None and pub is not None and pub < oldest_cur and shrink:
                rolled.append(prev_map[k])  # 窗口滚动：不进变更集，留痕可查
            else:
                removed.append(prev_map[k])  # 保守：任一条件不成立按真消失
    else:
        removed = [prev_map[k] for k in removed_keys]
    return added, removed, rolled


def make_change(change_id, source, kind, entry, now):
    """组装一条变更（字段命名与 change-watch 的 latest.json 对齐，便于工具复用）。

    change_id 由调用方生成（源+种类+条目键+时刻的 12 位哈希），轮内唯一、pending 回放稳定。
    """
    return {
        "change_id": change_id,
        "source_id": source["id"],
        "source_name": source.get("name") or source["id"],
        "serves": source.get("serves", []),
        "tier": source.get("tier", "T2"),
        "owner_entity": source.get("owner_entity"),
        "source_kind": source.get("kind", "rss"),
        "change_kind": kind,
        "key": entry.get("key"),
        "url": entry.get("link") or source.get("url"),
        "title": entry.get("title"),
        "summary": entry.get("summary"),
        "published": entry.get("published"),
        "first_seen": now,
    }


# ---------- 待判队列 ----------

def pending_path(ws):
    return os.path.join(ws, "state", "pending.json")


def load_and_merge_pending(ws, changes, now):
    """把上一轮未判的变更并回本轮（reopened: true），过期的移入 log/unjudged.jsonl。

    返回 (本轮生效的变更列表, 过期条数)。
    """
    state = core.jload(pending_path(ws), {"items": []})
    items = state.get("items", [])
    alive, expired = [], []
    for it in items:
        age = core.hours_since(it.get("first_seen"), now=core.parse_iso(now) or None)
        # age 用自然时间差：直接用当前时刻与 first_seen 比较
        if age is not None and age > MAX_PENDING_AGE_H:
            expired.append(it)
        else:
            alive.append(it)
    for it in expired:
        core.append_jsonl(os.path.join(ws, "log", "unjudged.jsonl"),
                          {**it, "expired_at": now, "reason": "未判超过 %d 小时" % MAX_PENDING_AGE_H})

    seen = {c["change_id"] for c in changes}
    merged = list(changes)
    for it in alive:
        if it.get("change_id") not in seen:
            merged.append({**it, "reopened": True})
            seen.add(it["change_id"])
    return merged, len(expired)


def save_pending(ws, changes, judged_ids, now):
    """核销已判、保留未判。judged_ids 为本轮已给出结论的 change_id 集合。"""
    keep = [c for c in changes if c.get("change_id") not in judged_ids]
    slim = [{k: c.get(k) for k in ("change_id", "source_id", "source_name", "serves", "tier",
                                   "owner_entity", "change_kind", "key", "url", "title",
                                   "summary", "published", "first_seen")}
            for c in keep]
    core.jdump(pending_path(ws), {"updated_at": now, "items": slim})
    return len(keep)
