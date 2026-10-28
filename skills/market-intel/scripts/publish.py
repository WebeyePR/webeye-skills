"""内参卡发送与自动推锚（`judge.py --digest --publish`）。

解决的问题（2026-10-28 用户明令）：
    业务人员「只会触发」，不会（也不该）手工执行 `--digest --commit`。
    旧流程「产卡 → 人工发送 → 人工 commit」里，最后一步漏做会导致锚点不动，
    下一轮把同一批内容再推一遍（实测：13:46 的 33 条在窗口里挂了一下午）。

设计纪律（改动前请先读）：
1. **发送成功才推锚**：任一目标/任一分片失败即不确认、不推锚，清单保留，
   由下一次 `--publish` 续发（宁重勿漏，绝不静默丢内容）。
2. **幂等键防重发**：`sha1(卡片内容 + 目标身份)[:40]` 作为 `--idempotency-key`，
   同一张卡重试不会在飞书产生第二条消息；卡片内容变了才会产生新消息。
3. **逐片落盘**：每发成功一片就把 message_id 写回清单，进程中途退出后可续发，
   不会把已送达的分片再发一遍。
4. **身份纪律**：私信走 `--as bot`，群卡一律 `--as user`（bot 发群 @所有人 不生效）。
5. **不猜测目标**：目标来自 `notify.json` 的 `digest.publish_target`
   （user=本人私信，chat=群，both=两者都发）；缺失即报错，不默认群发。
"""

import hashlib
import json
import os
import subprocess

import core
import delivery

PENDING_NAME = "digest_pending_send.json"
DEFAULT_TIMEOUT = 90
VALID_TARGETS = ("user", "chat", "both")


def pending_path(ws):
    return os.path.join(ws, "state", PENDING_NAME)


def load_batch(ws):
    """读取待发送清单；不存在返回 None（调用方据此给出「无待发批次」指引）。"""
    return core.jload(pending_path(ws), None)


def save_batch(ws, batch):
    core.jdump(pending_path(ws), batch)


def resolve_targets(cfg, override=None):
    """解析发送目标：优先命令行 override，其次 notify.digest.publish_target，最后 user。

    user=本人私信（bot 身份）；chat=群（user 身份）；both=先私信后群。
    只认这三个值，出现其他值直接报错——避免拼写错误变成「发不出去」或「发错地方」。
    """
    raw = override
    if not raw:
        raw = ((cfg or {}).get("digest") or {}).get("publish_target") or "user"
    if isinstance(raw, (list, tuple)):
        items = [str(x).strip() for x in raw if str(x).strip()]
    else:
        items = [str(raw).strip()]
    out = []
    for it in items:
        if it not in VALID_TARGETS:
            raise ValueError("publish_target 只能是 user/chat/both，收到: %r" % (it,))
        # both 展开为先后两条通道（顺序即送达顺序）
        out.extend(["user", "chat"] if it == "both" else [it])
    seen = []
    for t in out:
        if t not in seen:
            seen.append(t)
    return seen


def idempotency_key(path, target):
    """幂等键：卡片内容哈希 + 目标身份。

    内容不变 → 键不变 → 飞书侧不会重复送达（重试安全）；
    内容变了（重新产卡）→ 键变 → 允许作为新消息发出。
    """
    digest = delivery.file_hash(path)
    raw = ("%s|%s" % (digest, target)).encode("utf-8")
    return hashlib.sha1(raw).hexdigest()[:40]


def build_argv(target, cfg, path):
    """按目标身份组装 lark-cli 参数；缺目标 ID 时抛错（不静默改投）。"""
    notify = (cfg or {}).get("notify") or (cfg or {})
    if target == "user":
        uid = notify.get("user_id")
        if not uid:
            raise ValueError("notify.json 缺少 notify.user_id，无法私信发送")
        return ["im", "+messages-send", "--as", "bot", "--user-id", uid,
                "--msg-type", "interactive", "--content", "-"]
    if target == "chat":
        cid = notify.get("chat_id")
        if not cid:
            raise ValueError("notify.json 缺少 notify.chat_id，无法群发送")
        return ["im", "+messages-send", "--as", "user", "--chat-id", cid,
                "--msg-type", "interactive", "--content", "-"]
    raise ValueError("未知发送目标: %r" % (target,))


def _run(argv, payload, timeout=DEFAULT_TIMEOUT):
    """默认执行器：把卡片 JSON 从 stdin 喂给 lark-cli。"""
    proc = subprocess.run(["lark-cli"] + argv, input=payload, capture_output=True,
                          text=True, timeout=timeout)
    return proc.returncode, proc.stdout, proc.stderr


def _parse(stdout):
    """lark-cli 输出前面可能夹日志，截取首个 { 到末个 } 的 JSON 主体。"""
    i = stdout.find("{")
    j = stdout.rfind("}")
    if i < 0 or j <= i:
        return None
    try:
        return json.loads(stdout[i:j + 1])
    except ValueError:
        return None


def _find_message_id(node):
    """递归找 message_id（真实发送落在 data.message_id，dry-run 结构不同）。"""
    if isinstance(node, dict):
        if isinstance(node.get("message_id"), str) and node["message_id"]:
            return node["message_id"]
        for v in node.values():
            got = _find_message_id(v)
            if got:
                return got
    elif isinstance(node, list):
        for v in node:
            got = _find_message_id(v)
            if got:
                return got
    return None


def send_shard(target, cfg, path, runner=None, timeout=DEFAULT_TIMEOUT, dry_run=False):
    """发送一张分片；返回 {ok, message_id, error}。异常一律收敛为 ok=False。"""
    runner = runner or _run
    try:
        argv = build_argv(target, cfg, path)
    except ValueError as exc:
        return {"ok": False, "message_id": None, "error": str(exc)}
    if dry_run:
        argv = argv + ["--dry-run"]
    else:
        argv = argv + ["--idempotency-key", idempotency_key(path, target)]
    try:
        with open(path, "r", encoding="utf-8") as fh:
            payload = fh.read()
    except OSError as exc:
        return {"ok": False, "message_id": None, "error": "读取卡片失败: %s" % exc}
    try:
        code, out, err = runner(argv, payload, timeout)
    except Exception as exc:  # noqa: BLE001  # 超时/命令缺失都按失败处理，不阻断调用方决策
        return {"ok": False, "message_id": None, "error": "%s: %s" % (type(exc).__name__, exc)}
    body = _parse(out or "")
    if body is None:
        return {"ok": False, "message_id": None,
                "error": "无法解析返回（exit=%s, stderr=%s）" % (code, (err or "")[:160])}
    if body.get("ok") is not True:
        return {"ok": False, "message_id": None,
                "error": "lark-cli 返回失败: %s" % json.dumps(body, ensure_ascii=False)[:200]}
    mid = _find_message_id(body)
    if dry_run:
        return {"ok": True, "message_id": mid, "error": None}
    if not mid:
        # ok=true 但没拿到 message_id：视为成功但留痕，避免误判失败导致重复发送
        return {"ok": True, "message_id": None, "error": "返回缺 message_id（已按成功记录）"}
    return {"ok": True, "message_id": mid, "error": None}


def send_batch(ws, batch, targets=None, runner=None, dry_run=False, timeout=DEFAULT_TIMEOUT,
               echo=None):
    """按目标 × 分片顺序发送；逐片把回执写回清单。

    返回 (all_ok, batch)。all_ok=True 表示**所有分片都确认送达**，
    调用方此时才允许 `delivery.confirm()` 推锚。
    """
    echo = echo or (lambda msg: print(msg))
    cfg = core.jload(os.path.join(ws, "notify.json"), {}) or {}
    targets = targets or resolve_targets(cfg)
    paths = list(batch.get("cards") or [])
    if not paths:
        return False, batch
    sent = (batch.get("publish") or {}).get("sent") or {}
    all_ok = True
    for target in targets:
        for path in paths:
            if not os.path.isfile(path):
                echo("[publish] 分片缺失，拒绝发送: %s" % path)
                return False, batch
            slot = "%s|%s" % (target, os.path.basename(path))
            prev = sent.get(slot) or {}
            cur_hash = delivery.file_hash(path)
            if prev.get("hash") == cur_hash and prev.get("message_id"):
                echo("[publish] 已送达，跳过重发: %s → %s" % (os.path.basename(path), target))
                continue
            res = send_shard(target, cfg, path, runner=runner, timeout=timeout, dry_run=dry_run)
            name = os.path.basename(path)
            if res["ok"]:
                if dry_run:
                    echo("[publish][dry-run] 将发送 %s → %s" % (name, target))
                    continue
                sent[slot] = {"message_id": res.get("message_id"), "hash": cur_hash,
                              "at": core.now_iso(), "target": target}
                batch.setdefault("publish", {})["targets"] = list(targets)
                batch["publish"]["sent"] = sent
                save_batch(ws, batch)  # 逐片落盘：中途退出可续发
                echo("[publish] 已送达 %s → %s（message_id=%s）"
                     % (name, target, res.get("message_id") or "(未返回)"))
            else:
                all_ok = False
                echo("[publish] 发送失败 %s → %s：%s" % (name, target, res.get("error")))
    return all_ok, batch


def resume(ws, targets=None, runner=None, dry_run=False, echo=None):
    """续发既有清单（`judge.py --publish` 不带 --digest 时走这里）。

    清单里已成功分片会被跳过，只补发缺失部分；全部送达则交由调用方推锚。
    """
    batch = load_batch(ws)
    if not batch:
        return None, "没有待发送批次（先跑 --digest 产卡）"
    return send_batch(ws, batch, targets=targets, runner=runner,
                      dry_run=dry_run, echo=echo), None