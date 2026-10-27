"""内参批次清单与确认；仅管理本地状态，不执行发送。"""
import hashlib
import json
import os

import core


def fingerprint(record):
    """稳定摘要不受字段顺序影响；发送标记不属于卡片内容。"""
    content = {k: v for k, v in record.items() if k not in ("suppressed_by_guard", "notify")}
    return hashlib.sha256(json.dumps(content, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def file_hash(path):
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def in_window(record, key, anchor):
    """兼容旧时间锚；新锚附带边界 ID，覆盖同秒及暂未发送的较早记录。"""
    cutoff = core.parse_iso(anchor.get("anchor"))
    if cutoff is None:
        return True
    ts = core.parse_iso(record.get("logged_at")) or 0
    if anchor.get("version") != 2:
        return ts > cutoff
    prefix = "record" if key == "change_id" else "lead"
    rid = record.get(key)
    return (ts > cutoff or rid in anchor.get("pending_" + prefix + "_ids", [])
            or (ts == cutoff and rid not in anchor.get("sent_" + prefix + "_ids_at_anchor", [])))


def manifest(paths, records, leads, anchor, now):
    """保存本次产物指纹；修改正文或文件后必须重新产卡与预览。"""
    return {"version": 2, "created_at": now, "anchor_before": anchor,
            "cards": paths, "card_count": len(paths), "committed": False,
            "card_hashes": {p: file_hash(p) for p in paths},
            "record_ids": [r.get("change_id") for r in records],
            "lead_ids": [r.get("lead_id") for r in leads],
            "record_hashes": {r.get("change_id"): fingerprint(r) for r in records},
            "lead_hashes": {r.get("lead_id"): fingerprint(r) for r in leads}}


def confirm(ws, batch, anchor, now):
    """调用者确认全部分片已送达后，只确认该批 ID；失败不推进锚点。"""
    if not batch.get("cards") or "record_ids" not in batch or "lead_ids" not in batch:
        raise SystemExit("[digest] 旧清单缺少完整覆盖范围，请重新产卡并核对后提交")
    for path in batch["cards"]:
        if not os.path.isfile(path):
            raise SystemExit("[digest] 分片文件缺失，拒绝提交；待发送清单已保留")
        expected = (batch.get("card_hashes") or {}).get(path)
        if expected and file_hash(path) != expected:
            raise SystemExit("[digest] 分片文件已变化，重新产卡并核对后才能提交")
    intel_path = os.path.join(ws, "log", "intel.jsonl")
    records = core.read_jsonl(intel_path)
    leads = core.read_jsonl(os.path.join(ws, "log", "leads.jsonl"))
    groups = [("record", "change_id", records), ("lead", "lead_id", leads)]
    for prefix, key, rows in groups:
        indexed = {r.get(key): r for r in rows}
        for rid in batch[prefix + "_ids"]:
            row = indexed.get(rid)
            expected = (batch.get(prefix + "_hashes") or {}).get(rid)
            if row is None or (expected and fingerprint(row) != expected):
                raise SystemExit("[digest] 清单覆盖的内容已修改或撤回，拒绝确认旧批次")
    # 只有全部验证通过后才允许写状态；显式批次 ID 不受等待期间新增记录影响。
    covered = set(batch["record_ids"])
    for row in records:
        if row.get("change_id") in covered:
            row["suppressed_by_guard"] = True
            row["notify"] = False
    old = anchor.get("anchor")
    stamps = [r.get("logged_at") for prefix, key, rows in groups for r in rows
              if r.get(key) in set(batch[prefix + "_ids"]) and r.get("logged_at")]
    stamps += [old] if old else []
    new_anchor = max(stamps, key=lambda s: core.parse_iso(s) or 0) if stamps else old
    result = {"version": 2, "anchor": new_anchor, "committed_at": now}
    for prefix, key, rows in groups:
        ids = set(batch[prefix + "_ids"])
        pending = [r.get(key) for r in rows if r.get(key) not in ids
                   and not r.get("suppressed_by_guard") and in_window(r, key, anchor)]
        result["pending_" + prefix + "_ids"] = pending
        result["sent_" + prefix + "_ids_at_anchor"] = [
            r.get(key) for r in rows if r.get(key) not in pending
            and core.parse_iso(r.get("logged_at")) == core.parse_iso(new_anchor)]
    core.replace_jsonl(intel_path, records)
    core.jdump(os.path.join(ws, "state", "digest_anchor.json"), result)
    return new_anchor
