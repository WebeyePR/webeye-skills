#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""线索规则引擎：三层模型、确定性求值。

命中 = scope 成立 AND（任一 signal_group 成立）。组内 all_of 是 AND，组间是 OR。
行业条件只写在 scope 一处、所有组隐式继承——这是"多组条件耦合"的唯一书写位置。

evaluator 注册制：每个信号 type 对应一个求值函数，新增信号类型加一个 @evaluator 即可，
引擎与调度零改动。求值只读企业档案（facts），不碰网络、不调模型——可复现、可回测。

求值结果契约：
{"matched_groups": [组id...], "score": int,
 "evidence": [{"evaluator", "group", "detail", "url"}]}
"""

import core


EVALUATORS = {}


def evaluator(name):
    def deco(fn):
        EVALUATORS[name] = fn
        return fn
    return deco


def load_rules(ws):
    rec = core.jload(core.os.path.join(ws, "rules.json"), None)
    if rec is None:
        rec = core.jload(core.os.path.join(core.SCRIPT_DIR, "..", "config", "rules.json.example"), None)
    if not rec:
        raise SystemExit("[config-error] 找不到规则文件（工作区 rules.json 与模板均缺失）")
    validate_rules(rec)
    return rec


RULE_KEYS = {"version", "scope", "signal_groups", "scoring", "actions"}
GROUP_KEYS = {"id", "name", "weight", "all_of"}
COND_KEYS = {"type", "within_days", "min_amount_cny", "keywords_any", "round_any", "any_of", "_comment"}
KNOWN_EVALUATORS = ("industry_tag", "funding_event", "hiring")


def validate_rules(rec):
    """结构校验：未知字段 / 未知信号类型直接报错（白名单纪律，与 change-watch 一致）。"""
    core.check_keys(rec, RULE_KEYS, "rules.json")
    scope = rec.get("scope") or {}
    core.check_keys(scope, {"industry"}, "rules.scope")
    ind = (scope.get("industry") or {}).get("any_of") or []
    if not ind:
        raise SystemExit("[config-error] rules.scope.industry.any_of 不能为空（域限定是耦合条件的唯一书写处）")
    groups = rec.get("signal_groups") or []
    if not groups:
        raise SystemExit("[config-error] signal_groups 不能为空")
    ids = set()
    for g in groups:
        core.check_keys(g, GROUP_KEYS, "signal_group")
        gid = g.get("id")
        if not gid or gid in ids:
            raise SystemExit("[config-error] signal_group.id 缺失或重复: %r" % gid)
        ids.add(gid)
        for cond in g.get("all_of") or []:
            core.check_keys(cond, COND_KEYS, "condition")
            if cond.get("type") not in KNOWN_EVALUATORS:
                raise SystemExit("[config-error] 未知信号类型: %r（已注册: %s）"
                                 % (cond.get("type"), ", ".join(KNOWN_EVALUATORS)))


# ---------- evaluators ----------

@evaluator("industry_tag")
def ev_industry_tag(cond, fact):
    """行业匹配：档案 industries 与 any_of 有交集。scope 与组内都可使用。"""
    any_of = cond.get("any_of") or []
    hit = [i for i in fact.get("industries") or [] if i in any_of]
    if hit:
        return True, "行业匹配: %s" % "/".join(hit)
    return False, None


@evaluator("funding_event")
def ev_funding(cond, fact):
    """融资事件：within_days 内有事件；可选 min_amount_cny / round_any 过滤。"""
    within = cond.get("within_days")
    min_amount = cond.get("min_amount_cny")
    rounds = cond.get("round_any") or []
    now = core.time.time()
    for fe in fact.get("funding_events") or []:
        ts = core.parse_iso(fe.get("date") or fe.get("extracted_at"))
        if within and ts and (now - ts) / 86400.0 > within:
            continue
        if rounds and (fe.get("round") or "") not in rounds:
            continue
        amount = fe.get("amount_cny")
        if amount is None:
            amount = core.parse_amount_cny(fe.get("amount_text"))
        if min_amount is not None:
            if amount is None:
                continue  # 无法确认金额时不过这道门槛（保守：宁可不命中，不虚报）
            if amount < min_amount:
                continue
        detail = "融资: %s %s%s" % (fe.get("date") or fe.get("extracted_at", "")[:10],
                                   fe.get("round") or "", (fe.get("amount_text") or ""))
        return True, detail.strip()
    return False, None


@evaluator("hiring")
def ev_hiring(cond, fact):
    """招聘信号：within_days 内监测到任一关键词。"""
    within = cond.get("within_days")
    kws_any = [k.lower() for k in cond.get("keywords_any") or []]
    now = core.time.time()
    for hs in fact.get("hiring_signals") or []:
        if within:
            ts = core.parse_iso(hs.get("last_seen") or hs.get("first_seen"))
            if ts and (now - ts) / 86400.0 > within:
                continue
        hit = [k for k in (hs.get("keywords") or [])
               if k.lower() in kws_any or any(a in k.lower() for a in kws_any)]
        if hit:
            return True, "在招: %s（%s）" % ("/".join(hit), hs.get("page_url") or "招聘页")
    return False, None


# ---------- 求值 ----------

def evaluate_entity(fact, rules):
    """对一个企业档案求值。返回结果契约；未命中 matched_groups 为空。"""
    evidence = []
    # 1) scope：域限定，所有组隐式继承
    scope_ok, scope_ev = True, None
    ind_cond = ((rules.get("scope") or {}).get("industry") or {})
    if ind_cond:
        fn = EVALUATORS["industry_tag"]
        scope_ok, scope_ev = fn(ind_cond, fact)
        if scope_ev:
            evidence.append({"evaluator": "industry_tag", "group": "scope", "detail": scope_ev, "url": None})
    if not scope_ok:
        return {"matched_groups": [], "score": 0, "evidence": evidence}

    # 2) 信号组：组间 OR，组内 AND
    matched = []
    for g in rules.get("signal_groups") or []:
        group_ok = True
        for cond in g.get("all_of") or []:
            fn = EVALUATORS.get(cond.get("type"))
            if fn is None:
                group_ok = False
                break
            ok, ev = fn(cond, fact)
            if ok and ev:
                evidence.append({"evaluator": cond.get("type"), "group": g["id"], "detail": ev, "url": None})
            if not ok:
                group_ok = False
                break
        if group_ok and (g.get("all_of") or []):
            matched.append(g)

    if not matched:
        return {"matched_groups": [], "score": 0, "evidence": evidence}

    # 3) 评分：最高组 weight + 每多一组加成（只做排序与阈值，不做硬淘汰）
    scoring = rules.get("scoring") or {}
    weights = {g["id"]: int(g.get("weight", 60)) for g in matched}
    score = max(weights.values()) + int(scoring.get("extra_group_bonus", 10)) * (len(matched) - 1)
    return {
        "matched_groups": [g["id"] for g in matched],
        "matched_group_names": [g.get("name") or g["id"] for g in matched],
        "score": score,
        "evidence": evidence,
    }


def threshold(rules):
    return int((rules.get("scoring") or {}).get("threshold", 60))


def notify_group_at(rules):
    return int((rules.get("scoring") or {}).get("notify_group_at", 80))
