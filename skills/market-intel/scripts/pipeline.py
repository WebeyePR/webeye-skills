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
                 "rsshub_base", "detail_enrich_max_per_round", "cold_start_max_age_days"}

# RSSHub 自部署服务默认地址；改部署地址只需改 sources.json settings.rsshub_base 一处
RSSHUB_BASE_DEFAULT = "http://127.0.0.1:1200"


def load_sources(ws):
    rec = core.jload(os.path.join(ws, "sources.json"), None)
    if rec is None:
        # 模板回退保留（新环境未 --init 时也能跑起来，不直接崩），但必须显式告知：
        # 静默回退会让用户以为在用自己改过的配置，实际用的是模板（2026-10-27 实测，
        # 空工作区 --run 直接 exit 0 跑通，全程无任何提示）。
        sys.stderr.write(
            "[config] 工作区无 sources.json，本次使用 config/sources.json.example 模板运行。\n"
            "         你的自定义源/开关不会生效；建议先跑 --init 生成工作区配置再改。\n")
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


def make_cid(source_id, kind, key):
    """内容寻址 change_id：源 + 种类 + 条目键 的 12 位哈希（跨轮/跨洁净重置稳定）。

    不掺运行时刻——否则同一内容每轮都拿新 id，已判记录无法复用、intel.jsonl
    幂等与 --already-sent 防重发全部失效，出窗回灌的旧文会被当新消息重复轰炸
    （实战踩坑：量子位/36氪回灌轮只能靠 AI 逐条识别防重）。
    """
    return core.sha1("|".join([source_id, kind, key or ""]))[:12]


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
        # 冷启动判定（2026-10-27 修）：全新环境（首次部署 / --clean 之后）里**所有**源
        # 都没有快照，此时「无 prev 就只建基线」会让整轮 total=0，用户拿不到任何内容
        # ——与 SOP「全量首扫产出全量变更」直接矛盾（实测 16 源全 baseline、total=0）。
        # 判别标准：本轮开始前 `state/streams/` 里**一个快照文件都没有** = 冷启动。
        # 冷启动时全部条目本就都是"首次见到"，产变更才是对的语义（与 --baseline 区分开：
        # --baseline 是显式要求"只建基线不产变更"）。
        streams_dir = os.path.join(ws, "state", "streams")
        had_any_snapshot = os.path.isdir(streams_dir) and any(
            f.endswith(".json") for f in os.listdir(streams_dir))
        cold_start = not had_any_snapshot and not baseline
        # 冷启动时再按时间收口（2026-10-27 补）：部分源是**全量归档**（DeepSeek 官方
        # 动态 19 条覆盖 2024-07 起两年），冷启动若全判 added，等于把两年旧文当新消息
        # 灌进判定窗——正是用户抱怨"线索严重过时"的同一根因换个入口复发（实测 76 条
        # 新增里 20 条是归档旧文）。
        # 口径：只对**能解析出时间**的条目过滤，`published=None` 一律保留
        # （活动行/榜单/页面监测本就无时间戳，且都是当前窗口内容，滤掉就全丢了）。
        max_age_days = int(settings.get("cold_start_max_age_days", 30))
        cutoff = None
        if cold_start and max_age_days > 0:
            import time as _time
            cutoff = _time.time() - max_age_days * 86400
        if cold_start:
            sys.stderr.write(
                "[cold-start] 工作区无任何历史快照 → 按全量首扫处理。"
                "归档型源的旧条目按 %d 天窗口收口（无时间戳的条目全部保留）。\n"
                "             若只想建基线不推送，请改用 --baseline。\n" % max_age_days)
        for s, (ok, entries, err) in zip(sources, results):
            core.update_health(health, s["id"], ok, err, now)
            if not ok:
                failed.append({"id": s["id"], "label": s.get("name") or s["id"], "error": (err or "")[:200]})
                continue
            prev = diffcore.load_snapshot(ws, s["id"])
            if baseline:
                diffcore.save_snapshot(ws, s["id"], entries, now)
                continue
            # 冷启动：把「时间过旧」的条目从**待产变更**里剔掉。
            # 关键：只改 `change_entries`（产变更用），**快照必须存完整 entries**
            # ——否则全被过滤时快照为空，下一轮又被判成"新源"重走 baseline
            # （2026-10-27 实测：qwen-blog 40 条全过滤 → 快照空 → 次轮报"无历史快照"）。
            change_entries = entries
            if cold_start and cutoff is not None:
                fresh, stale_n = [], 0
                for e in entries:
                    ts = core.parse_iso(e.get("published"))
                    if ts is not None and ts < cutoff:
                        stale_n += 1
                        continue
                    fresh.append(e)
                if stale_n:
                    sys.stderr.write("[cold-start] 源 %s：%d 条早于 %d 天窗口，不产变更"
                                     "（仍写入完整基线快照，避免次轮误判为新源）\n"
                                     % (s["id"], stale_n, max_age_days))
                change_entries = fresh
            # 老工作区**新增单个源**时（有其他源的快照、本源无快照）：只建基线，
            # 不产变更——否则该源的全部历史（可能两年）会被当 added 灌进判定窗。
            # 用户实测反馈"潜客线索严重过时"正是此因：DeepSeek/Qwen 新源首次抓取
            # 带进 19+40 条 2024 年起的旧文，全部被判成新动态。
            # 冷启动场景已由上面 cold_start 分支排除（那里应当产变更）。
            if not prev and not cold_start:
                diffcore.save_snapshot(ws, s["id"], entries, now)
                sys.stderr.write("[baseline] 源 %s 无历史快照，仅建基线（%d 条），"
                                 "本轮不产出变更——避免把历史当新消息。\n"
                                 "   ⚠️ 若该源并非本轮新增，则说明快照丢失"
                                 "（state/streams/%s.json 被删/损坏），本轮真实变更已跳过，"
                                 "请核查后再决定是否需要补判。\n"
                                 % (s["id"], len(entries), s["id"]))
                continue
            added, removed, rolled_entries = diffcore.diff_source(
                prev, change_entries, windowed=bool(s.get("windowed")))
            for e in rolled_entries:
                core.append_jsonl(os.path.join(ws, "log", "rolled_out.jsonl"),
                                  {"source_id": s["id"], "key": e.get("key"), "title": e.get("title"),
                                   "url": e.get("link"), "at": now})
            rolled += rolled_entries
            # 详情页富化：detail_enrich 源的新增条目抓详情页补全时间/地点（活动行场景）。
            # 只富化进变更的副本，快照仍存 RSS 原始条目（键与差分语义不受富化影响）。
            enrich_budget = [int(settings.get("detail_enrich_max_per_round", 12))]
            do_enrich = bool((s.get("config") or {}).get("detail_enrich"))
            for kind, group in (("added", added), ("removed", removed)):
                for e in group:
                    if kind == "added" and do_enrich:
                        e = collect.enrich_detail(s, e, settings, enrich_budget)
                    cid = make_cid(s["id"], kind, e.get("key"))
                    all_changes.append(diffcore.make_change(cid, s, kind, e, now))
            # 先保存本轮所有待判，再推进快照；任何源写盘失败均可从队列恢复。
            diffcore.enqueue_pending(ws, all_changes, now)
            diffcore.save_snapshot(ws, s["id"], entries, now)

        if baseline:
            core.jdump(os.path.join(ws, "state", "health.json"), health)
            core.append_jsonl(os.path.join(ws, "log", "runs.jsonl"),
                              {"run_at": now, "mode": "baseline", "sources": len(sources), "failed": len(failed)})
            print("基线已建立：%d 个源（本轮不产出变更、不通知——避免把历史当新消息）" % len(sources))
            return 0

        changes, expired = diffcore.load_and_merge_pending(ws, all_changes, now)
        diffcore.save_pending(ws, changes, set(), now)
        core.jdump(os.path.join(ws, "state", "health.json"), health)
        stale = stale_check(sources, settings, ws, now)
        latest = {
            "run_at": now,
            "summary": {"total": len([c for c in changes if not c.get("reopened")]),
                        "pending_carried": len([c for c in changes if c.get("reopened")]),
                        "needs_judgment": len(changes),
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


def rekey_baseline(ws):
    """存量基线键迁移：把 streams 快照里旧规则（guid:URL / URL#N 形态）的键
    统一重算为内容正交键（collect.entry_key_of 同一规则）。

    不迁移的后果：键分配修复后首轮差分把全部现有条目当成 removed+added
    一次性重报。同键重复条目迁移时去重（保留首条）。幂等：可安全重跑。
    """
    import collect as collect_mod
    sdir = os.path.join(ws, "state", "streams")
    if not os.path.isdir(sdir):
        print("无基线快照目录: %s" % sdir)
        return 0
    total, dup, changed = 0, 0, 0
    for fn in sorted(os.listdir(sdir)):
        if not fn.endswith(".json"):
            continue
        path = os.path.join(sdir, fn)
        rec = core.jload(path, None)
        if not rec:
            continue
        merged, seen = [], set()
        for e in rec.get("entries", []):
            total += 1
            nk = collect_mod.entry_key_of(e)
            if nk != e.get("key"):
                changed += 1
            e = dict(e, key=nk)
            if nk in seen:
                dup += 1  # 同内容多板块重复条目，合并为一条
                continue
            seen.add(nk)
            merged.append(e)
        core.jdump(path, dict(rec, entries=merged))
        print("  %s: %d 条 → %d 键" % (fn, len(rec.get("entries", [])), len(merged)))
    print("迁移完成：共 %d 条，改键 %d，合并重复 %d（幂等，可重跑）" % (total, changed, dup))
    return 0


def init_ws(ws):
    cfg_dir = os.path.join(core.SCRIPT_DIR, "..", "config")
    todo = []
    # denylist.json 必进模板复制清单（2026-10-27 补）：它是用户维护的黑名单，
    # `match.load_denylist` 虽有内置模板回退，但文件不落到工作区，用户就看不到、
    # 也没法按需增删（设计意图是「名单由用户改这个文件」）。缺文件 = 静默用内置，
    # 用户以为自己的增删生效了，其实没生效。
    for name in ("sources.json", "entities.json", "rules.json", "notify.json",
                 "denylist.json"):
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
    if not os.path.exists(os.path.join(ws, "denylist.json")):
        todo.append("denylist.json 未就位：本轮起会把黑名单外的巨头/服务商/晚期融资当线索，"
                    "务必从 config/denylist.json.example 复制并核对")
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


# 洁净重置的「删」与「留」单点定义：写死在代码里，避免每轮由 AI 现编 rm 命令
# （历史上出现过漏删 log/intel.jsonl 导致增量差分失效、以及多删配置的情况）。
# 洁净重置的「留/删」单点定义。**保留清单驱动**（不是删除清单）：
# 工作区只该有三类东西——配置（环境身份）、业务记忆、运行时产物；除前者两类外全清。
# 为什么用保留清单（2026-10-27 改进）：
#   早期是「列举要删的」，结果新增的运行时文件（如 state/health.json）被静默漏删；
#   改成列举要留的，未来新增的产物自动被清，不会重演漏删。
CLEAN_KEEP = [
    # ---- 环境身份（删了要重配）----
    "sources.json", "rules.json", "notify.json", "entities.json", "denylist.json",
    # ---- 业务记忆（删了会重复入池/重复调研/丢线索）----
    "facts", "log/leads.jsonl", "log/enrichments.jsonl",
    # ---- 用户自备素材缓存（重建代价高：需重新上传飞书）----
    "state/hero_images.json",
]
# 保留后仍需保证存在的空目录（下游脚本首次运行要求路径在）
CLEAN_ENSURE_DIRS = ["state/streams", "out", "log", "facts"]


def _clean_plan(ws):
    """算出要删的项：工作区顶层 + state/ + log/ 下，凡不在 CLEAN_KEEP 的都删。"""
    keep = set(CLEAN_KEEP)
    targets = []  # (kind, relpath, abspath)

    def consider(rel):
        if rel in keep:
            return
        p = os.path.join(ws, rel)
        if not os.path.exists(p):
            return
        victims = [rel]
        # 顶层目录若整体不在保留名单（如 out/），整目录删
        if os.path.isdir(p) and not any(k.startswith(rel + "/") or k == rel for k in keep):
            n = sum(len(f) for _, _, f in os.walk(p))
            targets.append(("目录", rel, p, n))
            return
        if os.path.isfile(p):
            targets.append(("文件", rel, p, os.path.getsize(p)))

    for name in sorted(os.listdir(ws)):
        consider(name)
    for sub in ("state", "log"):
        d = os.path.join(ws, sub)
        if not os.path.isdir(d):
            continue
        for name in sorted(os.listdir(d)):
            consider("%s/%s" % (sub, name))
    return targets


def clean_ws(ws, yes=False):
    """清基线（洁净验证/按新环境运行用）：清掉全部运行时产物，只留配置与业务记忆。

    默认**只打印不动手**（`--clean` 不带 `--yes` = 演练），确认后加 `--yes` 真删。
    保留清单见 `CLEAN_KEEP`：
      - 环境身份：sources/rules/notify/entities/**denylist**
      - 业务记忆：`facts/`、`log/leads.jsonl`、`log/enrichments.jsonl`
      - 素材缓存：`state/hero_images.json`（重建需重传飞书，代价高）
    其余一律清掉——包括 `state/health.json`（源健康账簿，继承旧 down 状态会误判）。
    """
    import shutil
    targets = _clean_plan(ws)
    print("=== 将删除（%d 项）===" % len(targets))
    for kind, rel, p, extra in targets:
        if kind == "目录":
            print("  [目录] %-28s（%d 个文件）" % (rel, extra))
        else:
            print("  [文件] %-28s（%d 字节）" % (rel, extra))
    if not targets:
        print("  （已是干净状态，无可删除项）")
    print("\n=== 保留（环境身份 + 业务记忆）===")
    for name in CLEAN_KEEP:
        if os.path.exists(os.path.join(ws, name)):
            print("  ✓ %s" % name)
    if not yes:
        print("\n[演练模式] 未删除任何文件。确认无误后加 --yes 真正执行：")
        print("  pipeline.py --workspace %s --clean --yes" % ws)
        return 0
    for kind, rel, p, _ in targets:
        if kind == "目录":
            shutil.rmtree(p, ignore_errors=True)
        else:
            try:
                os.remove(p)
            except FileNotFoundError:
                pass
    for sub in CLEAN_ENSURE_DIRS:
        os.makedirs(os.path.join(ws, sub), exist_ok=True)
    print("\n[已执行] 清理完成，共删除 %d 项。下一轮 --run 即全量首扫（真实新环境行为）。" % len(targets))
    return 0


def main():
    ap = argparse.ArgumentParser(description="market-intel 采集与差分编排")
    ap.add_argument("--workspace", help="工作区目录（默认 ~/.market-intel）")
    ap.add_argument("--init", action="store_true", help="初始化工作区（复制配置模板并输出待办）")
    ap.add_argument("--baseline", action="store_true", help="建基线：快照当前全部条目，不产出变更不通知")
    ap.add_argument("--run", action="store_true", help="常规一轮：采集 + 差分 -> out/latest.json")
    ap.add_argument("--rekey-baseline", action="store_true",
                    help="一次性基线键迁移：快照键统一重算为内容正交键（键分配修复后必须执行一次）")
    ap.add_argument("--clean", action="store_true",
                    help="清基线：删差分基线/卡片/判定流水，保留配置与业务记忆（默认只演练，需 --yes 才删）")
    ap.add_argument("--yes", action="store_true", help="与 --clean 连用：真正执行删除")
    args = ap.parse_args()
    ws = core.workspace(args.workspace)
    if args.init:
        raise SystemExit(init_ws(ws))
    if args.clean:
        raise SystemExit(clean_ws(ws, yes=args.yes))
    if args.rekey_baseline:
        raise SystemExit(rekey_baseline(ws))
    if args.baseline:
        raise SystemExit(run_round(ws, baseline=True))
    if args.run:
        raise SystemExit(run_round(ws))
    ap.print_help()


if __name__ == "__main__":
    main()
