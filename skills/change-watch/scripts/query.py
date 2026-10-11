#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
change-watch :: 查询沉淀

所有"视图"（时间线 / 按类型 / 按产品 / 检索）都从变更流水里查出来，不需要额外页面。

用法：
  query.py --timeline                     # 按时间倒序（默认全部，按天分组）
  query.py --type 模型发布 --since 2026-10-01
  query.py --object gemini --grep 定价
  query.py --stats                        # 各类型 / 各目标 / 各重要性计数
  query.py --rolled-out                   # 被判为「窗口滚动」、没当变更上报的条目
  query.py --format json                  # 机器可读
"""
import argparse
import datetime
import json
import os
import re
import sys
from collections import Counter

DEFAULT_WS = os.path.join(os.path.expanduser("~"), ".change-watch")

# 机器值 → 人话。用户在命令行里看的是这个，不该让他去猜 low / removed 是什么。
SIG_LABEL = {"high": "重要", "normal": "常规", "low": "次要", "unknown": "待确认"}
KIND_LABEL = {"added": "新增", "removed": "下架", "changed": "变化"}


def human(field, value):
    if field == "significance":
        return SIG_LABEL.get(value, value)
    if field == "change_kind":
        return KIND_LABEL.get(value, value)
    return value


def load_log(ws):
    path = os.path.join(ws, "log", "changes.jsonl")
    rows = []
    if not os.path.exists(path):
        return rows
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                try:
                    rows.append(json.loads(line))
                except ValueError:
                    pass
    return rows


def load_rolled_out(ws, limit=50):
    """读取被判为「窗口滚动」的条目。

    它们不是变更，所以不进 changes.jsonl；但必须能查——否则有人发现"这条帖子怎么没报"
    时会以为是脚本漏了，而实际上是它从源的可见窗口底部滚出去了。沉默必须有据。
    """
    path = os.path.join(ws, "log", "rolled_out.jsonl")
    if not os.path.exists(path):
        return []
    rows = []
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                try:
                    rows.append(json.loads(line))
                except ValueError:
                    pass
    rows.sort(key=lambda r: str(r.get("logged_at", "")), reverse=True)
    return rows[:limit]


def parse_day(text):
    if not text:
        return None
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})", str(text))
    if not m:
        return None
    return datetime.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))


def main():
    ap = argparse.ArgumentParser(description="change-watch 查询沉淀")
    ap.add_argument("--workspace", default=DEFAULT_WS)
    ap.add_argument("--timeline", action="store_true", help="按时间倒序、按天分组输出")
    ap.add_argument("--stats", action="store_true", help="输出计数统计")
    ap.add_argument("--rolled-out", action="store_true",
                    help="列出被判为窗口滚动、未当作变更的条目")
    ap.add_argument("--target")
    ap.add_argument("--type")
    ap.add_argument("--object")
    ap.add_argument("--significance")
    ap.add_argument("--since", help="起始日期 YYYY-MM-DD")
    ap.add_argument("--until", help="结束日期 YYYY-MM-DD")
    ap.add_argument("--grep", help="关键词（匹配标题/说明/对象/来源）")
    ap.add_argument("--limit", type=int, default=50)
    ap.add_argument("--format", choices=("text", "json"), default="text")
    args = ap.parse_args()

    ws = os.path.abspath(os.path.expanduser(args.workspace))

    if args.rolled_out:
        ro = load_rolled_out(ws, args.limit)
        if not ro:
            print("没有窗口滚动记录。")
            return 0
        print("窗口滚动 %d 条（旧条目移出源的可见窗口，非内容消失，故未当作变更）：\n" % len(ro))
        for r in ro:
            print("  %s  %-22s %s" % (str(r.get("logged_at", ""))[:16],
                                      r.get("source_id", "?"), r.get("title", "")))
            print("      %s" % r.get("reason", ""))
        return 0

    rows = load_log(ws)

    since = parse_day(args.since)
    until = parse_day(args.until)

    def keep(rec):
        if args.target and rec.get("target") != args.target:
            return False
        if args.type and args.type not in str(rec.get("type", "")):
            return False
        if args.significance and rec.get("significance") != args.significance:
            return False
        if args.object and args.object.lower() not in str(rec.get("object", "")).lower():
            return False
        day = parse_day(rec.get("logged_at"))
        if since and (not day or day < since):
            return False
        if until and (not day or day > until):
            return False
        if args.grep:
            hay = " ".join([str(rec.get("title", "")), str(rec.get("summary", "")),
                            str(rec.get("object", "")), str(rec.get("type", "")),
                            " ".join(rec.get("sources") or [])]).lower()
            if args.grep.lower() not in hay:
                return False
        return True

    hits = [r for r in rows if keep(r)]
    hits.sort(key=lambda r: str(r.get("logged_at", "")), reverse=True)

    # 撤回传导：流水只追加、不回改。某个对象/链接后来被记为 removed 时，
    # 旧记录在**视图层**标一句"后续已下架"，既不撒谎也不破坏审计链。
    # 用文件中的先后（行号）而不是时间戳比较：同一秒落盘的两条靠时间戳分不出先后。
    removed_at, own_at = {}, {}
    for i, r in enumerate(rows):
        own_at[id(r)] = i
        if r.get("change_kind") == "removed":
            for k in (r.get("object"), r.get("url")):
                if k:
                    removed_at[k] = max(removed_at.get(k, -1), i)

    def superseded(rec):
        i = own_at.get(id(rec), -1)
        if i < 0:
            return False
        return any(removed_at.get(k, -1) > i for k in (rec.get("object"), rec.get("url")) if k)

    if args.stats:
        print("共 %d 条（筛选后 %d 条）" % (len(rows), len(hits)))
        for label, field in (("类型", "type"), ("目标", "target"),
                             ("重要性", "significance"), ("变更种类", "change_kind")):
            c = Counter(human(field, str(r.get(field, "?"))) for r in hits)
            print("\n%s：" % label)
            for k, v in c.most_common():
                print("  %-14s %d" % (k, v))
        return 0

    if args.format == "json":
        print(json.dumps(hits[:args.limit], ensure_ascii=False, indent=2))
        return 0

    if not hits:
        print("没有匹配的变更记录。")
        return 0

    shown = hits[:args.limit]
    current_day = None
    for rec in shown:
        day = str(rec.get("logged_at", ""))[:10]
        if args.timeline and day != current_day:
            current_day = day
            print("\n=== %s ===" % day)
        print("[%s] %-10s %-12s %s%s" % (
            SIG_LABEL.get(rec.get("significance", "?"), rec.get("significance", "?")),
            rec.get("type", "?"),
            (rec.get("object") or KIND_LABEL.get(rec.get("change_kind", ""), rec.get("change_kind", "")))[:22],
            rec.get("title", ""),
            "  【后续已下架】" if superseded(rec) else ""))
        if rec.get("summary"):
            print("      %s" % str(rec["summary"])[:160])
        srcs = rec.get("sources") or ([rec["url"]] if rec.get("url") else [])
        if srcs:
            print("      来源：%s" % " ｜ ".join(
                ("%s <%s>" % (s.get("name") or s.get("url", ""), s.get("url", ""))
                 if isinstance(s, dict) else str(s)) for s in srcs))
    if len(hits) > len(shown):
        print("\n（共 %d 条，已显示 %d 条，用 --limit 调整）" % (len(hits), len(shown)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
