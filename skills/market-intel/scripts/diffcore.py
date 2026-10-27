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
    """差分一个源。返回 (added_entries, removed_entries, rolled_entries)。

    windowed 源的「窗口滚动」判定（2026-10-27 修正）：
    定长窗口流（RSS 只给最近 N 条）里，消失条目**物理上只可能是被新条目挤出窗口**，
    不存在「真下架」语义。旧实现要求 `pub < oldest_cur` 才判滚动，对非严格时序的源
    （活动行列表含推荐位、量子位首页含混排）永远不成立 → 每轮几十条 removed 噪音。
    新判定按「是否满窗 + 是否有挤出」识别滚动：
      - 满窗（len(cur) >= len(prev)，无收缩迹象）→ 消失必为挤出 → 全部判 rolled；
      - 轻微收缩（缺口 ≤ max(2, 10% prev)）→ 视为源条数自然抖动，仍判 rolled；
      - 明显收缩（缺口更大）→ 保守判 removed（宁可多报）；
      - 时间戳只用于附加判断：消失条目明确早于当前窗口最旧条目时也判 rolled
        （收缩但确实是被窗口挤出的旧闻，避免时间陈旧条目误报）。
    2026-10-27 二次修正（陷阱 52）：`shrink` 用绝对比较 `len(cur) < len(prev)`
    过于敏感——活动行列表页条数在 20/21 之间随机抖动（实测备份 20 条、本轮 21 条），
    1 条差异就被当成「源真变少」→ 4 条消失全判 removed 噪音。改为**容差比较**：
    小幅抖动算满窗，只有明显收缩才认为源真的变少。另附：该源 `published` 几乎
    全为 None（20 条仅 3 条可解析），收缩分支的时间比较本就恒不成立，
    故「小幅收缩 + 无可靠时间轴」必须判 rolled，否则每轮必产噪音。
    """
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
        # 收缩迹象：缺口超过容差才算「源真的变少」（列表页条数会自然抖动 ±1~2）
        gap = len(prev_keys) - len(cur_keys)
        # 容差按比例，不设绝对下限：10 条以内的小样本没有「抖动余地」，
        # 少 1 条就是真的少（否则 2 条的源少 1 条会被当成抖动，吞掉真消失）
        tolerance = int(len(prev_keys) * 0.1)
        shrink = gap > tolerance
        for k in removed_keys:
            entry = prev_map[k]
            if not shrink:
                # 满窗/轻微抖动：窗口源必然挤出，不可能真下架
                rolled.append(entry)
                continue
            pub = core.parse_iso(entry.get("published"))
            if oldest_cur is not None and pub is not None and pub < oldest_cur:
                rolled.append(entry)   # 早于当前窗口最旧条目：窗口滚动
            elif pub is None:
                # 无时间证据：无法证明「时间陈旧」，但源确实明显变少——
                # 按窗口滚动处理（该源本就无可靠时间轴，主张"真消失"缺依据）
                rolled.append(entry)
            else:
                removed.append(entry)  # 明显收缩且时间不旧：保守按真消失
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

    # 重试同一变化不能刷新首次发现时间，否则待判永不过期。
    originals = {c.get("change_id"): c for c in alive}
    merged = [{**c, "first_seen": originals[c["change_id"]].get("first_seen")}
              if c.get("change_id") in originals else c for c in changes]
    seen = {c["change_id"] for c in changes}
    for it in alive:
        if it.get("change_id") not in seen:
            merged.append({**it, "reopened": True})
            seen.add(it["change_id"])
    return merged, len(expired)


def save_pending(ws, changes, judged_ids, now):
    """核销已判、保留未判。judged_ids 为本轮已给出结论的 change_id 集合。"""
    keep = [c for c in changes if c.get("change_id") not in judged_ids]
    # 保留来源类型和详情富化字段；reopened 仅为当前轮展示标记。
    payload = [{k: v for k, v in c.items() if k != "reopened"} for c in keep]
    core.jdump(pending_path(ws), {"updated_at": now, "items": payload})
    return len(keep)


def enqueue_pending(ws, changes, now):
    """快照推进前保存待判；中断重试按稳定 ID 合并且不刷新年龄。"""
    current = core.jload(pending_path(ws), {"items": []}).get("items", [])
    items = {c["change_id"]: c for c in current}
    for change in changes:
        items.setdefault(change["change_id"], change)
    save_pending(ws, list(items.values()), set(), now)
