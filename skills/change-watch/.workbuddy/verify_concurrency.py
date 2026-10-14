#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""change-watch 并发采集专项夹具（v20261014）

覆盖：结果顺序、异常隔离、整轮超时、并发度上限、单域名限流、X 串行、
      单实例锁、settings 容错、parse_tables 分组行跳过。

跑法：python3 .workbuddy/verify_concurrency.py
"""
import os
import sys
import threading
import time
import urllib.parse

SKILL = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(SKILL, "scripts"))
import watch  # noqa: E402

PASS, FAIL = [], []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print("[%s] %-46s %s" % ("PASS" if ok else "FAIL", name, detail))


def spec(i, host="a.example"):
    return {"id": "s%d" % i, "label": "S%d" % i, "kind": "rss",
            "url": "https://%s/f%d" % (host, i)}


def make_fake(delay=0.05, fail=(), hang=(), wait=0.5):
    """假 collect_source：模拟真实实现的并发行为（含单域名限流与异常）。"""
    peak, cur, lock = {"n": 0}, {"n": 0}, threading.Lock()

    def body(idx, tid, sp, layer):
        with lock:
            cur["n"] += 1
            peak["n"] = max(peak["n"], cur["n"])
        try:
            if idx in hang:
                time.sleep(30)
            time.sleep(delay)
            if idx in fail:
                raise RuntimeError("boom-%d" % idx)
            st = watch.new_status(tid, sp, layer)
            st["ok"] = True
            st["items"] = idx
            return st, {"k%d" % idx: {"hash": "h%d" % idx}}
        finally:
            with lock:
                cur["n"] -= 1

    def _cs(cfg_settings, layer, tid, sp, host_concurrency=0):
        idx = int(sp["id"][1:])
        sem = watch.host_semaphore(urllib.parse.urlsplit(sp["url"]).netloc, host_concurrency)
        # 与真实实现一致：域名闸只做有限等待（这里 wait 设短便于测试）
        if sem is not None and not sem.acquire(timeout=wait):
            sem = None
        try:
            return body(idx, tid, sp, layer)
        finally:
            if sem is not None:
                sem.release()

    return _cs, peak


def run(jobs, settings, fake):
    """用假实现跑一次 collect_all，返回 (results, 耗时)。"""
    orig = watch.collect_source
    watch.collect_source = fake
    try:
        t0 = time.monotonic()
        res = watch.collect_all(settings, jobs)
        return res, time.monotonic() - t0
    finally:
        watch.collect_source = orig


# ---------------------------------------------------------------- T1 结果顺序
jobs = [("google", "stream", spec(i)) for i in range(8)]                      # 同域名
jobs_diff = [("google", "stream", spec(i, "h%d.example" % i)) for i in range(8)]  # 不同域名
fake, _ = make_fake(delay=0.05)
res, _dt = run(jobs, {"collect_concurrency": 8}, fake)
order = [r[0]["source_id"] for r in res]
check("T1 结果按 jobs 顺序返回", order == ["s%d" % i for i in range(8)],
      "顺序=%s" % ",".join(order))

# ------------------------------------------------- T2 单源异常不外溢
fake2, _ = make_fake(delay=0.02, fail={3})
res2, _ = run(jobs, {"collect_concurrency": 4}, fake2)
ok2 = (all(res2[i][0]["ok"] for i in range(8) if i != 3)
       and not res2[3][0]["ok"]
       and "WorkerError" in (res2[3][0]["error"] or ""))
check("T2 单源异常不外溢（其余照常）", ok2, "s3.error=%s" % res2[3][0]["error"])

# ------------------------------------------------- T3 整轮壁钟上限
fake3, _ = make_fake(delay=0.02, hang={5})
res3, dt3 = run(jobs_diff, {"collect_concurrency": 8, "host_concurrency": 0,
                            "collect_wall_clock_seconds": 5.0}, fake3)
ok3 = (not res3[5][0]["ok"] and "TimeoutError" in (res3[5][0]["error"] or "")
       and all(res3[i][0]["ok"] for i in range(8) if i != 5)
       and dt3 < 8.0)
check("T3 超时源判失败且整轮不卡死", ok3, "耗时=%.2fs s5.error=%s" % (dt3, res3[5][0]["error"]))

# 卡住的源不能占着域名闸把同域名其它源全拖死（闸只做有限等待）
fake3b, _ = make_fake(delay=0.02, hang={5}, wait=0.5)
res3b, dt3b = run(jobs, {"collect_concurrency": 8, "host_concurrency": 1,
                         "collect_wall_clock_seconds": 6.0}, fake3b)
# 有源挂起时整轮必然耗到壁钟上限（这是它的定义），关键是**其余 7 个不受牵连**
ok3b = sum(1 for r in res3b if r[0]["ok"]) >= 7 and dt3b < 7.0
check("T3b 慢源不拖死同域名其它源", ok3b,
      "成功=%d/8（仅 s5 超时）耗时=%.2fs" % (sum(1 for r in res3b if r[0]["ok"]), dt3b))

# ------------------------------------------------- T4 全局并发度上限
# 关掉单域名限流（host_concurrency=0）+ 8 个不同域名，才能观察到全局并发度的效果
fake4, peak4 = make_fake(delay=0.15)
run(jobs_diff, {"collect_concurrency": 3, "host_concurrency": 0}, fake4)
check("T4 全局并发不超过设定值", peak4["n"] <= 3, "峰值=%d（设 3）" % peak4["n"])
check("T4b 确实并发了（不是退化成串行）", peak4["n"] >= 2, "峰值=%d" % peak4["n"])

# ------------------------------------------------- T5 单域名限流
same_host = [("google", "stream", spec(i, "same.example")) for i in range(8)]
fake5, peak5 = make_fake(delay=0.15)
run(same_host, {"collect_concurrency": 8, "host_concurrency": 2}, fake5)
check("T5 单域名并发不超过 host_concurrency", peak5["n"] <= 2, "峰值=%d（设 2）" % peak5["n"])

# 不同域名不受单域名闸影响
diff_host = [("google", "stream", spec(i, "h%d.example" % i)) for i in range(8)]
fake6, peak6 = make_fake(delay=0.15)
run(diff_host, {"collect_concurrency": 8, "host_concurrency": 1}, fake6)
check("T5b 不同域名不互相阻塞", peak6["n"] >= 4, "峰值=%d（8 个不同域名，设 host=1）" % peak6["n"])

# ------------------------------------------------- T6 X 通道串行
x_peak, x_cur, x_lock = {"n": 0}, {"n": 0}, threading.Lock()
orig_impl = watch._x_gql_impl


def fake_impl(endpoint, timeout, **vo):
    with x_lock:
        x_cur["n"] += 1
        x_peak["n"] = max(x_peak["n"], x_cur["n"])
    try:
        time.sleep(0.1)
        return {"data": {}}
    finally:
        with x_lock:
            x_cur["n"] -= 1


watch._x_gql_impl = fake_impl
ths = [threading.Thread(target=lambda: watch.x_gql("u", 5)) for _ in range(6)]
for t in ths:
    t.start()
for t in ths:
    t.join(10)
watch._x_gql_impl = orig_impl
check("T6 X guest 通道串行（并发峰值=1）", x_peak["n"] == 1, "峰值=%d（X_CONCURRENCY=%d）"
      % (x_peak["n"], watch.X_CONCURRENCY))

# ------------------------------------------------- T7 单实例锁
import tempfile  # noqa: E402
ws = tempfile.mkdtemp(prefix="cwlock")
os.makedirs(os.path.join(ws, "state"), exist_ok=True)
ok1, msg1 = watch.acquire_run_lock(ws)
ok_r, msg_r = watch.acquire_run_lock(ws)     # 第二个进程/线程也拿不到（同 fd 表内 flock 也互斥）
check("T7 单实例锁：首轮拿到", ok1, msg1 or "")
check("T7b 单实例锁：并发第二轮被拒", not ok_r, msg_r[:60])
try:
    watch._LOCK_FH.close()
except Exception:
    pass
watch._LOCK_FH = None
ok_again, _ = watch.acquire_run_lock(ws)
check("T7c 锁释放后可再次获取", ok_again, "")

# ------------------------------------------------- T8 settings 容错
fake8, _ = make_fake(delay=0.01)
r8, _ = run(jobs, {"collect_concurrency": "abc", "host_concurrency": -5,
                   "collect_wall_clock_seconds": None}, fake8)
check("T8 settings 写错降级默认且不崩", all(r[0]["ok"] for r in r8), "8 源全 ok")

# ------------------------------------------------- T9 parse_tables 分组行
html_group = """
<table>
<tr><th>Model</th><th>Release date</th><th>Shutdown date</th><th>Replacement</th></tr>
<tr><td>gemini-2.5-pro</td><td>June 17, 2025</td><td>No shutdown date announced</td><td></td></tr>
<tr><td>Preview models</td></tr>
<tr><td>gemini-2.5-flash-preview</td><td>May 20, 2025</td><td>Nov 17, 2026</td><td>gemini-3.8</td></tr>
<tr><td>Preview models</td></tr>
<tr><td>veo-3.1-preview</td><td>Sep 9, 2025</td><td>Jun 30, 2026</td><td>veo-3.1</td></tr>
</table>
"""
pairs = watch.parse_tables(html_group, key_column=0)
keys = [k for k, _ in pairs]
check("T9 colspan 分组行被跳过", len(pairs) == 3 and all("Preview" not in k for k in keys),
      "keys=%s" % keys)

html_single = """
<table>
<tr><td>model-a</td></tr>
<tr><td>model-b</td></tr>
<tr><td>model-c</td></tr>
</table>
"""
pairs_s = watch.parse_tables(html_single, key_column=0)
check("T9b 单列名单表不受影响", len(pairs_s) == 3, "n=%d" % len(pairs_s))

# 防回归：gemini-api-models 实测有 20 条真实记录是 2 列（名称 + 模型 ID），
# 而该表典型是 3 列 —— 规则若放宽成"少于典型列数"会把它们整批误删。
html_two_col = """
<table>
<tr><td>Model</td><td>Model ID</td><td>Description</td></tr>
<tr><td>Gemini 3 Flash</td><td>gemini-3-flash-preview</td></tr>
<tr><td>Gemini 2.5 Flash</td><td>gemini-2.5-flash</td><td>Best price-performance</td></tr>
</table>
"""
pairs_2c = watch.parse_tables(html_two_col, key_column=0)
check("T9d 2 列真实数据行必须保留", len(pairs_2c) == 2, "keys=%s" % [k for k, _ in pairs_2c])

html_header = """
<table>
<tr><td>Model</td><td>Shutdown date</td></tr>
<tr><td>gemini-1.0-pro</td><td>April 21, 2025</td></tr>
</table>
"""
pairs_h = watch.parse_tables(html_header, key_column=0)
check("T9c 隐式表头行仍被跳过", len(pairs_h) == 1, "keys=%s" % [k for k, _ in pairs_h])

# ------------------------------------------------- T10 并发与串行等价
res_par, _ = run(jobs, {"collect_concurrency": 6}, make_fake(delay=0.03)[0])
res_ser, _ = run(jobs, {"collect_concurrency": 1}, make_fake(delay=0.03)[0])
same = [(a[0]["source_id"], a[0]["items"]) for a in res_par] == \
       [(b[0]["source_id"], b[0]["items"]) for b in res_ser]
check("T10 并发与串行产出一致", same, "并发=%s" % [r[0]["items"] for r in res_par])

print("\n结果: %d/%d 通过" % (len(PASS), len(PASS) + len(FAIL)))
sys.exit(1 if FAIL else 0)
