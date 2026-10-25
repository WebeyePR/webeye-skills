#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""market-intel 编排入口：初始化 / 建基线 / 常规一轮（采集 + 差分）。

退出码约定沿用 change-watch：
  0 = 本轮正常（可能无变更）
  1 = 配置错误（看 stderr，不要静默重试）
  2 = 有源失败但仍有结果，或待判队列有遗留（继续后续流程，不要中止）
  3 = 另一轮正在运行（单实例锁未拿到），本轮跳过——不是故障，不要重试覆盖

AI 判断与通知的完整串接见 SKILL.md「定时与自动化」。
"""

import argparse
import fcntl
import json
import os
import sys

import collect
import core
import diffcore

SOURCE_KEYS = {"id", "name", "kind", "url", "enabled", "serves", "tier", "owner_entity",
               "tags", "windowed", "stale_after_hours", "timeout_seconds", "config", "notes"}
SETTINGS_KEYS = {"stale_after_hours", "request_timeout_seconds", "request_retries",
                 "collect_concurrency", "host_concurrency", "collect_wall_clock_seconds",
                 "rsshub_base"}

# RSSHub 自部署服务默认地址；改部署地址只需改 sources.json settings.rsshub_base 一处
RSSHUB_BASE_DEFAULT = "http://127.0.0.1:1200"


def load_sources(ws):
    rec = core.jload(os.path.join(ws, "sources.json"), None)
    if rec is None:
        rec = core.jload(os.path.join(core.SCRIPT_DIR, "..", "config", "sources.json.example"), None)
    if not rec:
        raise SystemExit("[config-error] 找不到信源配置（工作区 sources.json 与模板均缺失）")
    core.check_keys(rec, {"version", "settings", "sources"}, "sources.json")
    settings = rec.get("settings") or {}
    core.check_keys(settings, SETTINGS_KEYS, "sources.settings")
    rsshub_base = (settings.get("rsshub_base") or RSSHUB_BASE_DEFAULT).rstrip("/")
    sources = []
    for s in rec.get("sources") or []:
        core.check_keys(s, SOURCE_KEYS, "source %s" % s.get("id", "?"))
        if not s.get("id") or not s.get("url"):
            raise SystemExit("[config-error] 源缺少 id 或 url: %r" % s.get("id"))
        url = s["url"]
        if url.startswith("rsshub://"):
            # rsshub://<路由路径> → 自部署 RSSHub 实际地址（部署迁移时只改 settings.rsshub_base）
            s = dict(s, url=rsshub_base + "/" + url[len("rsshub://"):])
        if s.get("enabled", True):
            sources.append(s)
    return sources, settings


def stale_check(sources, settings, ws, now):
    """停更检测：源最新条目距今超过阈值即在报告里点名（信源侧静默，不是脚本故障）。"""
    out = []
    for s in sources:
        thr = s.get("stale_after_hours", settings.get("stale_after_hours", 168))
        if thr is None:
            continue
        snap = diffcore.load_snapshot(ws, s["id"])
        if not snap:
            continue
        pubs = [core.parse_iso(e.get("published")) for e in snap.values()]
        pubs = [p for p in pubs if p is not None]
        if not pubs:
            continue
        age_h = ((core.parse_iso(now) or 0) - max(pubs)) / 3600.0
        if age_h > thr:
            out.append({"id": s["id"], "label": s.get("name") or s["id"], "age_hours": round(age_h, 1),
                        "threshold_hours": thr})
    return out


def acquire_lock(ws):
    lock_path = os.path.join(ws, "state", "run.lock")
    fh = open(lock_path, "w")
    try:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        fh.close()
        sys.stderr.write("[lock] 另一轮采集正在运行，本轮跳过（不是故障，不要重试覆盖）\n")
        raise SystemExit(3)
    return fh


def run_round(ws, baseline=False):
    now = core.now_iso()
    sources, settings = load_sources(ws)
    lock = acquire_lock(ws)
    try:
        results = collect.run_collection(sources, settings)
        health = core.jload(os.path.join(ws, "state", "health.json"), {})
        all_changes, rolled, failed = [], [], []
        for s, (ok, entries, err) in zip(sources, results):
            core.update_health(health, s["id"], ok, err, now)
            if not ok:
                failed.append({"id": s["id"], "label": s.get("name") or s["id"], "error": (err or "")[:200]})
                continue
            prev = diffcore.load_snapshot(ws, s["id"])
            if baseline:
                diffcore.save_snapshot(ws, s["id"], entries, now)
                continue
            added, removed, rolled_entries = diffcore.diff_source(
                prev, entries, windowed=bool(s.get("windowed")))
            for e in rolled_entries:
                core.append_jsonl(os.path.join(ws, "log", "rolled_out.jsonl"),
                                  {"source_id": s["id"], "key": e.get("key"), "title": e.get("title"),
                                   "url": e.get("link"), "at": now})
            rolled += rolled_entries
            for kind, group in (("added", added), ("removed", removed)):
                for e in group:
                    # change_id：源 + 种类 + 条目键 + 本轮时刻 的 12 位哈希（轮内唯一，pending 回放稳定）
                    cid = core.sha1("|".join([s["id"], kind, e.get("key") or "", now]))[:12]
                    all_changes.append(diffcore.make_change(cid, s, kind, e, now))
            diffcore.save_snapshot(ws, s["id"], entries, now)

        if baseline:
            core.jdump(os.path.join(ws, "state", "health.json"), health)
            core.append_jsonl(os.path.join(ws, "log", "runs.jsonl"),
                              {"run_at": now, "mode": "baseline", "sources": len(sources), "failed": len(failed)})
            print("基线已建立：%d 个源（本轮不产出变更、不通知——避免把历史当新消息）" % len(sources))
            return 0

        changes, expired = diffcore.load_and_merge_pending(ws, all_changes, now)
        core.jdump(os.path.join(ws, "state", "health.json"), health)
        stale = stale_check(sources, settings, ws, now)
        latest = {
            "run_at": now,
            "summary": {"total": len([c for c in changes if not c.get("reopened")]),
                        "pending_carried": len([c for c in changes if c.get("reopened")]),
                        "pending_expired": expired,
                        "sources_total": len(sources),
                        "sources_down": len([1 for k, v in health.items() if v.get("status") == "down"]),
                        "stale_sources": stale},
            "health": health,
            "changes": changes,
        }
        core.jdump(os.path.join(ws, "out", "latest.json"), latest)
        core.append_jsonl(os.path.join(ws, "log", "runs.jsonl"),
                          {"run_at": now, "mode": "run", "sources": len(sources),
                           "failed": len(failed), "changes": len(changes), "rolled": len(rolled)})
        # 人类可读小结（供 AI / 巡检读取后决定是否进入判断）
        print(json.dumps({"summary": latest["summary"], "failed": failed}, ensure_ascii=False, indent=2))
        return 2 if (failed or expired) else 0
    finally:
        lock.close()
        try:
            os.remove(os.path.join(ws, "state", "run.lock"))
        except OSError:
            pass


def init_ws(ws):
    cfg_dir = os.path.join(core.SCRIPT_DIR, "..", "config")
    todo = []
    for name in ("sources.json", "entities.json", "rules.json", "notify.json"):
        dst = os.path.join(ws, name)
        src = os.path.join(cfg_dir, name + ".example")
        if os.path.exists(dst):
            print("已存在，跳过: %s" % dst)
        elif os.path.exists(src):
            with open(src, "r", encoding="utf-8") as fi, open(dst, "w", encoding="utf-8") as fo:
                fo.write(fi.read())
            print("已复制模板: %s" % dst)
        else:
            todo.append("缺模板: %s" % src)
    notify = core.jload(os.path.join(ws, "notify.json"), {})
    nid = (notify.get("notify") or {})
    if not nid.get("user_id"):
        todo.append("notify.user_id 为空：私信不发送（AI 协助查询飞书 openId 后写回，或用 lark-cli 查）")
    if not nid.get("chat_id") or "REPLACE" in str(nid.get("chat_id")):
        todo.append("notify.chat_id 未配置：群卡片不发送（lark-cli im +chat-search --query 群名 查询）")
    print("\n--init 完成。待办 %d 项：" % len(todo))
    for t in todo:
        print("  - %s" % t)
    return 0


def main():
    ap = argparse.ArgumentParser(description="market-intel 采集与差分编排")
    ap.add_argument("--workspace", help="工作区目录（默认 ~/.market-intel）")
    ap.add_argument("--init", action="store_true", help="初始化工作区（复制配置模板并输出待办）")
    ap.add_argument("--baseline", action="store_true", help="建基线：快照当前全部条目，不产出变更不通知")
    ap.add_argument("--run", action="store_true", help="常规一轮：采集 + 差分 -> out/latest.json")
    args = ap.parse_args()
    ws = core.workspace(args.workspace)
    if args.init:
        raise SystemExit(init_ws(ws))
    if args.baseline:
        raise SystemExit(run_round(ws, baseline=True))
    if args.run:
        raise SystemExit(run_round(ws))
    ap.print_help()


if __name__ == "__main__":
    main()
