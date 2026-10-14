import sys
sys.path.insert(0, "/Users/apple/.workbuddy/skills/change-watch/scripts")
import watch

def item(pub, key):
    return {"published": pub, "link": "https://x/%s" % key, "title": key}

def mk(pubs):
    return {("k%s" % i): item(p, "k%s" % i) for i, p in enumerate(pubs)}

# 时间越大越新
OLD, MID, NEW = "2026-10-07T16:46:00+00:00", "2026-10-09T00:00:00+00:00", "2026-10-12T00:00:00+00:00"
WIN = {"windowed": True}
NOWIN = {}

def case(name, spec, added, removed_pubs, prev_pubs, curr_pubs, exp_removed, exp_rolled):
    prev = mk(prev_pubs)
    curr = mk(curr_pubs)
    removed = [("kR%s" % i, item(p, "kR%s" % i), None) for i, p in enumerate(removed_pubs)]
    kept, rolled = watch.split_rolled_out(spec, added, removed, curr, prev)
    ok = (len(kept) == exp_removed and len(rolled) == exp_rolled)
    print("[%s] %-52s kept=%d rolled=%d (期望 %d/%d)" % ("PASS" if ok else "FAIL", name, len(kept), len(rolled), exp_removed, exp_rolled))
    return ok

results = []
# T1 情形A：有新条目，消失项早于最旧 -> 滚动
results.append(case("T1 windowed+有新条目+旧项掉底", WIN, [("n", item(NEW,"n"), None)], [OLD], [OLD, MID], [MID], 0, 1))
# T2 情形B(新)：无新条目+窗口收窄(15->12)+消失早于最旧 -> 滚动
results.append(case("T2 windowed+无新条目+窗口收窄", WIN, [], [OLD], [OLD]*1 + [MID]*14, [MID]*12, 0, 1))
# T3 保守：无新条目+窗口未收窄 -> 真消失
results.append(case("T3 windowed+无新条目+窗口未收窄=真消失", WIN, [], [OLD], [MID]*12, [MID]*12, 1, 0))
# T4 非 windowed 不受影响 -> 真消失
results.append(case("T4 非windowed+有新条目=真消失", NOWIN, [("n", item(NEW,"n"), None)], [OLD], [OLD, MID], [MID], 1, 0))
# T5 消失项在窗口内（不早于最旧）-> 真消失
results.append(case("T5 windowed+有新条目+消失项在窗口内=真消失", WIN, [("n", item(NEW,"n"), None)], [MID], [MID, NEW], [MID, NEW], 1, 0))
# T6 无 removed -> 空
results.append(case("T6 无 removed", WIN, [("n", item(NEW,"n"), None)], [], [MID], [MID], 0, 0))
# T7 prev 为 None（首轮/基线）-> 保守真消失
kept, rolled = watch.split_rolled_out(WIN, [], [("kR0", item(OLD,"kR0"), None)], mk([MID]), None)
ok = len(kept) == 1 and len(rolled) == 0
print("[%s] %-52s kept=%d rolled=%d (期望 1/0)" % ("PASS" if ok else "FAIL", "T7 prev=None(首轮)=保守真消失", len(kept), len(rolled)))
results.append(ok)

print("\n结果: %d/%d 通过" % (sum(results), len(results)))
sys.exit(0 if all(results) else 1)
