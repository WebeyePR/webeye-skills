#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""检索与统计：情报流水、线索库、企业档案随时可查。

用法：
  python3 scripts/query.py --leads               # 最近线索
  python3 scripts/query.py --entity 月之暗面       # 按企业检索（含档案详情）
  python3 scripts/query.py --intel --since 2026-10-01
  python3 scripts/query.py --stats               # 各类型 / 目标计数
  python3 scripts/query.py --rolled-out          # 被判为窗口滚动的条目（沉默必须有据）
"""

import argparse
import json
import os

import core


def _print_records(records, fields):
    for r in records[-50:]:
        print(json.dumps({k: r.get(k) for k in fields}, ensure_ascii=False))
    if len(records) > 50:
        print("（仅显示最近 50 条，共 %d 条）" % len(records))


def main():
    ap = argparse.ArgumentParser(description="market-intel 检索与统计")
    ap.add_argument("--workspace")
    ap.add_argument("--leads", action="store_true")
    ap.add_argument("--intel", action="store_true")
    ap.add_argument("--entity", help="按企业名 / 别名检索（档案 + 相关流水）")
    ap.add_argument("--stats", action="store_true")
    ap.add_argument("--rolled-out", action="store_true")
    ap.add_argument("--since", help="按时间过滤（ISO 日期）")
    args = ap.parse_args()
    ws = core.workspace(args.workspace)
    since = args.since

    if args.leads:
        recs = core.read_jsonl(os.path.join(ws, "log", "leads.jsonl"))
        if since:
            recs = [r for r in recs if (r.get("logged_at") or "") >= since]
        _print_records(recs, ["lead_id", "entity_name", "industries", "matched_group_names",
                              "score", "status", "summary", "url", "logged_at"])
        return 0
    if args.intel:
        recs = core.read_jsonl(os.path.join(ws, "log", "intel.jsonl"))
        if since:
            recs = [r for r in recs if (r.get("logged_at") or "") >= since]
        _print_records(recs, ["change_id", "type", "significance", "title", "url", "logged_at"])
        return 0
    if args.rolled_out:
        _print_records(core.read_jsonl(os.path.join(ws, "log", "rolled_out.jsonl")),
                       ["source_id", "key", "title", "at"])
        return 0
    if args.entity:
        import facts as facts_mod
        entities = facts_mod.load_entities(ws)
        eid, _ = facts_mod.normalize_entity(args.entity, entities)
        fact = facts_mod.load_fact(ws, eid)
        if not fact:
            print("未找到企业档案: %s（归一为 %s）" % (args.entity, eid))
            return 1
        print(json.dumps(fact, ensure_ascii=False, indent=2))
        leads = [l for l in core.read_jsonl(os.path.join(ws, "log", "leads.jsonl"))
                 if l.get("entity_id") == eid]
        if leads:
            print("\n相关线索：")
            _print_records(leads, ["lead_id", "matched_group_names", "score", "status", "logged_at"])
        return 0
    if args.stats:
        intel = core.read_jsonl(os.path.join(ws, "log", "intel.jsonl"))
        leads = core.read_jsonl(os.path.join(ws, "log", "leads.jsonl"))
        by_type, by_sig = {}, {}
        for r in intel:
            by_type[r.get("type") or "未分类"] = by_type.get(r.get("type") or "未分类", 0) + 1
            by_sig[r.get("significance") or "unknown"] = by_sig.get(r.get("significance") or "unknown", 0) + 1
        print(json.dumps({"情报总数": len(intel), "按类型": by_type, "按重要性": by_sig,
                          "线索总数": len(leads),
                          "待确认线索": len([l for l in leads if l.get("status") == "new"])},
                         ensure_ascii=False, indent=2))
        return 0
    ap.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
