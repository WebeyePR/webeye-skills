#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 live 工作区配置同步为仓库内的脱敏模板 config/targets.json.example。

背景：仓库只收脱敏模板（新用户 clone 后靠它完成首跑引导）；live 配置
（默认 ~/.change-watch/targets.json）含真实推送 ID——notify.chat_id 群 ID、
notify.user_id 个人 openId——永不入库。旧方案靠 git assume-unchanged 标记
手动同步，标记是粗粒度屏蔽，会连带挡住模板自身的正常更新（曾导致仓库模板
停更）。现在模板是普通受版控文件，改完 live 跑一次本脚本即可。

安全闸：写出前对整份序列化文本扫描 oc_ / ou_ / cli_ 形态的真实 ID，
命中即拒绝写出（exit 2）——宁可同步失败，也不让敏感数据进仓库。

用法：
    python3 scripts/sync_template.py                   # 默认路径同步
    python3 scripts/sync_template.py --from A --to B   # 指定路径
    python3 scripts/sync_template.py --check           # 只比对不写（有差异 exit 1，供 CI）
"""
import argparse
import json
import os
import re
import sys

SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_FROM = os.path.join(os.path.expanduser("~"), ".change-watch", "targets.json")
DEFAULT_TO = os.path.join(SKILL_DIR, "config", "targets.json.example")
CHAT_PLACEHOLDER = "REPLACE_WITH_YOUR_GROUP_CHAT_ID"

# 目标模板里 notify._comment 的规范文案；目标文件已有 _comment 时保留目标版本，
# 避免本脚本覆盖人工修改。live 自己的 _comment（往往提及具体群）一律不带入模板。
CANONICAL_NOTIFY_COMMENT = (
    "推送目标：user_id=私信给谁（飞书 openId，空=不发私信）；"
    "chat_id=额外发到的群（空/占位符=不发群）。两者都留空则完全不推送"
    "（仅落盘 out/notify-unsent-*.md）。交互运行 `python3 scripts/watch.py --init` "
    "会自动按「群名 / 自己」查询飞书 ID 并写入这里，无需手动粘贴；也可手动填。"
    "@all 需群主在群设置开启「@所有人」权限，否则把 mention 改成具体 [open_id, ...]。"
)

# 真实 ID 形态（飞书 chat_id / open_id / app id）。模板里只允许出现占位符。
LEAK_RE = re.compile(r"\b(?:oc|ou|cli)_[0-9a-z]{20,}\b")


def load_json(path):
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def sanitize(cfg, notify_comment):
    """脱敏：notify 块只保留非敏感结构，ID 一律换占位符。返回新 dict（不改动入参）。"""
    out = json.loads(json.dumps(cfg, ensure_ascii=False))
    notify = out.get("notify")
    if not isinstance(notify, dict):
        return out
    mention = notify.get("mention")
    if mention not in (None, "all"):
        # mention 若是 [open_id, ...] 列表，同样携带真实 ID，不能进模板
        print("[WARN] notify.mention=%r 含具体 ID，模板中已重置为 \"all\"" % (mention,),
              file=sys.stderr)
        mention = "all"
    # 键序与既有模板保持一致：user_id, chat_id, mention, _comment
    out["notify"] = {
        "user_id": "",
        "chat_id": CHAT_PLACEHOLDER,
        "mention": "all" if mention is None else mention,
        "_comment": notify_comment or CANONICAL_NOTIFY_COMMENT,
    }
    return out


def serialize(cfg):
    """与既有模板一致的序列化风格：indent=2、 ensure_ascii=False、无结尾换行。"""
    return json.dumps(cfg, ensure_ascii=False, indent=2)


def summarize(cfg):
    ids, kinds = [], {}
    for tgt in cfg.get("targets", []):
        for field in ("streams", "registries"):
            for spec in tgt.get(field, []):
                sid = spec.get("id")
                ids.append(sid)
                kinds[sid] = spec.get("kind")
    return {
        "n": len(ids),
        "ids": ids,
        "kinds": kinds,
        "settings": cfg.get("settings", {}),
        "digest": {k: v for k, v in (cfg.get("digest") or {}).items()
                   if not str(k).startswith("_")},
        "heartbeat": cfg.get("heartbeat", {}),
    }


def print_diff(old_cfg, new_cfg):
    o, n = summarize(old_cfg), summarize(new_cfg)
    print("  源：%d → %d" % (o["n"], n["n"]))
    for sid in n["ids"]:
        if sid not in o["ids"]:
            print("    + %s（%s）" % (sid, n["kinds"].get(sid)))
    for sid in o["ids"]:
        if sid not in n["ids"]:
            print("    - %s（%s）" % (sid, o["kinds"].get(sid)))
    for key in ("settings", "digest", "heartbeat"):
        if o[key] != n[key]:
            print("  %s 块有变化" % key)


def main():
    ap = argparse.ArgumentParser(description="同步 live 配置为脱敏模板 targets.json.example")
    ap.add_argument("--from", dest="src", default=DEFAULT_FROM,
                    help="live 配置路径（默认 ~/.change-watch/targets.json）")
    ap.add_argument("--to", dest="dst", default=DEFAULT_TO,
                    help="目标模板路径（默认 <技能目录>/config/targets.json.example）")
    ap.add_argument("--check", action="store_true",
                    help="只比对不写；模板与 live 不一致时 exit 1（供 CI / 巡检）")
    args = ap.parse_args()

    if not os.path.exists(args.src):
        print("[FAIL] 源配置不存在：%s" % args.src, file=sys.stderr)
        return 2
    try:
        live = load_json(args.src)
    except (ValueError, OSError) as exc:
        print("[FAIL] 源配置读取失败：%s（%s）" % (args.src, exc), file=sys.stderr)
        return 2

    # 保留目标模板里已有的 notify._comment（可能是人工改过的版本）
    comment = None
    if os.path.exists(args.dst):
        try:
            old_cfg = load_json(args.dst)
            comment = (old_cfg.get("notify") or {}).get("_comment")
        except (ValueError, OSError):
            old_cfg = None
    else:
        old_cfg = None

    new_cfg = sanitize(live, comment)
    text = serialize(new_cfg)

    leak = LEAK_RE.search(text)
    if leak:
        print("[FAIL] 输出中仍检出疑似真实 ID：%r……已拒绝写出。" % leak.group(0),
              file=sys.stderr)
        print("       请检查配置里还有没有别的字段携带 oc_/ou_/cli_ 形态的 ID。",
              file=sys.stderr)
        return 2

    old_text = None
    if os.path.exists(args.dst):
        with open(args.dst, "r", encoding="utf-8") as fh:
            old_text = fh.read()

    if args.check:
        if old_text == text:
            print("[OK] 模板已与 live 一致（%d 个源）" % summarize(new_cfg)["n"])
            return 0
        print("[DIFF] 模板与 live 不一致：")
        if old_cfg is None:
            print("   （目标文件不存在或无法解析）")
        else:
            print_diff(old_cfg, new_cfg)
        return 1

    if old_text == text:
        print("[OK] 无变化，模板已是最新（%d 个源）" % summarize(new_cfg)["n"])
        return 0

    if old_cfg is not None:
        print_diff(old_cfg, new_cfg)
    with open(args.dst, "w", encoding="utf-8") as fh:
        fh.write(text)
    print("[OK] 已同步 → %s（%d 个源，notify 块已脱敏）" % (args.dst, summarize(new_cfg)["n"]))
    print("      git add %s 即可提交，无需任何 git 标记操作。" % args.dst)
    return 0


if __name__ == "__main__":
    sys.exit(main())
