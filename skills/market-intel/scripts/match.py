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

import re

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
GROUP_KEYS = {"id", "name", "weight", "tier", "all_of"}
COND_KEYS = {"type", "within_days", "min_amount_cny", "keywords_any", "round_any",
             "any_of", "kind_any", "_comment"}
KNOWN_EVALUATORS = ("industry_tag", "funding_event", "hiring",
                    "other_event", "chinese_company", "going_global")

# scope 允许的域限定维度 -> 默认 evaluator（显式写 type 时以 type 为准）
SCOPE_DEFAULT_EVAL = {"industry": "industry_tag", "origin": "chinese_company",
                      "global": "going_global"}


def validate_rules(rec):
    """结构校验：未知字段 / 未知信号类型直接报错（白名单纪律，与 change-watch 一致）。"""
    core.check_keys(rec, RULE_KEYS, "rules.json")
    scope = rec.get("scope") or {}
    for dim, cond in scope.items():
        if dim not in SCOPE_DEFAULT_EVAL:
            raise SystemExit("[config-error] rules.scope 未知维度: %r（允许: %s）"
                             % (dim, ", ".join(SCOPE_DEFAULT_EVAL)))
        if not isinstance(cond, dict):
            raise SystemExit("[config-error] rules.scope.%s 必须是条件对象" % dim)
        etype = cond.get("type") or SCOPE_DEFAULT_EVAL[dim]
        if etype not in KNOWN_EVALUATORS:
            raise SystemExit("[config-error] rules.scope.%s 未知信号类型: %r" % (dim, etype))
    ind = (scope.get("industry") or {}).get("any_of") or []
    if not ind:
        raise SystemExit("[config-error] rules.scope.industry.any_of 不能为空")
    groups = rec.get("signal_groups") or []
    if not groups:
        raise SystemExit("[config-error] signal_groups 不能为空")
    ids = set()
    for g in groups:
        core.check_keys(g, GROUP_KEYS, "signal_group")
        gid = g.get("id")
        if not gid or gid in ids:
            raise SystemExit("[config-error] signal_group.id 缺失或重复: %r" % gid)
        if (g.get("tier") or "P1") not in ("P0", "P1"):
            raise SystemExit("[config-error] signal_group.tier 只允许 P0/P1: %r" % gid)
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


@evaluator("other_event")
def ev_other_event(cond, fact):
    """其他事件信号（业务暴增/新品发布/榜单新晋等）：other_events 里 kind 命中 + 时间窗。

    kind 取值约定（AI 判断时写入，见 references/lead-criteria.md）：
    business_surge=业务数据暴增（客户介绍通稿）；product_launch=发布新产品；
    rank_new_entry=海外软件榜单新晋（排除霸榜，只看新上榜）。
    """
    within = cond.get("within_days")
    kinds = [k.lower() for k in cond.get("kind_any") or []]
    kws_any = [k.lower() for k in cond.get("keywords_any") or []]
    now = core.time.time()
    for oe in fact.get("other_events") or []:
        kind = (oe.get("kind") or "").lower()
        if kinds and kind not in kinds:
            continue
        if within:
            ts = core.parse_iso(oe.get("extracted_at"))
            if ts and (now - ts) / 86400.0 > within:
                continue
        detail = oe.get("detail") or ""
        if kws_any and not any(k in detail.lower() for k in kws_any):
            continue
        return True, "%s: %s" % (oe.get("kind"), (detail or "")[:80])
    return False, None


_CN_CITY_WORDS = ("深圳", "北京", "上海", "杭州", "广州", "成都", "武汉", "南京", "苏州", "厦门",
                  "香港", "珠海", "长沙", "西安", "重庆", "青岛", "合肥", "福州", "东莞", "佛山")


@evaluator("chinese_company")
def ev_chinese(cond, fact):
    """中国企业判定：优先档案显式 origin=CN（AI 判断申报 / 词表实体标注），
    兜底启发式——融资币种为人民币、公司主体词（有限公司/科技/网络）或中国城市词。
    启发式命中置信低于显式申报，仅用于 unknown 档案兜底，不虚报。"""
    if (fact.get("origin") or "").upper() == "CN":
        return True, "中国企业（判断申报/词表标注）"
    for fe in fact.get("funding_events") or []:
        txt = (fe.get("amount_text") or "") + (fe.get("investors") or "")
        if "人民币" in txt or "亿元" in txt or "万元" in txt:
            return True, "中国企业（融资以人民币计）"
    blob = " ".join([fact.get("name", "")] + list(fact.get("aliases") or [])
                    + [oe.get("detail") or "" for oe in fact.get("other_events") or []])
    if re.search(r"有限公司|科技公司|网络科技|信息技术", blob):
        return True, "中国企业（公司主体词）"
    if any(c in blob for c in _CN_CITY_WORDS):
        return True, "中国企业（城市词 %s）" % next(c for c in _CN_CITY_WORDS if c in blob)
    return False, None


_GLOBAL_KWS = ("出海", "跨境", "全球化", "海外", "国际版", "overseas", "global")


@evaluator("going_global")
def ev_going_global(cond, fact):
    """业务出海判定：档案显式 going_global（AI 申报/词表标注），或任一事件/招聘/行业
    文本里出现出海关键词（出海/跨境/全球化/海外等）。短剧等行业天然出海，行业词表
    里以"短剧出海"标注。"""
    if fact.get("going_global"):
        return True, "业务出海（判断申报/词表标注）"
    if any("出海" in i for i in fact.get("industries") or []):
        return True, "业务出海（行业 %s）" % next(i for i in fact["industries"] if "出海" in i)
    texts = [oe.get("detail") or "" for oe in fact.get("other_events") or []]
    texts += [fe.get("detail") if isinstance(fe.get("detail"), str) else "" for fe in fact.get("funding_events") or []]
    texts += [fe.get("amount_text") or "" for fe in fact.get("funding_events") or []]
    texts += [" ".join(hs.get("keywords") or []) for hs in fact.get("hiring_signals") or []]
    blob = " ".join(texts).lower()
    for k in _GLOBAL_KWS:
        if k in blob:
            return True, "业务出海（关键词「%s」）" % k
    return False, None


# ---------- 求值 ----------

def evaluate_entity(fact, rules):
    """对一个企业档案求值。返回结果契约；未命中 matched_groups 为空。

    scope 是多维域限定（维度间 AND）：industry / origin / global，所有组隐式继承。
    命中组里任一 tier=P0 → 线索分级 P0（市场优先建联）；仅 P1 组命中 → P1。
    """
    evidence = []
    # 1) scope：多维域限定，全部成立才继续
    for dim, cond in (rules.get("scope") or {}).items():
        fn = EVALUATORS.get(cond.get("type") or SCOPE_DEFAULT_EVAL.get(dim, "industry_tag"))
        ok, ev = (fn or (lambda c, f: (True, None)))(cond, fact)
        if ok and ev:
            evidence.append({"evaluator": cond.get("type") or SCOPE_DEFAULT_EVAL.get(dim),
                             "group": "scope:" + dim, "detail": ev, "url": None})
        if not ok:
            return {"matched_groups": [], "score": 0, "priority": None, "evidence": evidence}

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
        return {"matched_groups": [], "score": 0, "priority": None, "evidence": evidence}

    # 3) 分级与评分：任一 P0 组命中即 P0；score = 最高组 weight + 多组加成（仅排序/阈值用）
    priority = "P0" if any((g.get("tier") or "P1") == "P0" for g in matched) else "P1"
    scoring = rules.get("scoring") or {}
    weights = {g["id"]: int(g.get("weight", 60)) for g in matched}
    score = max(weights.values()) + int(scoring.get("extra_group_bonus", 10)) * (len(matched) - 1)
    return {
        "matched_groups": [g["id"] for g in matched],
        "matched_group_names": [g.get("name") or g["id"] for g in matched],
        "priority": priority,
        "score": score,
        "evidence": evidence,
    }


def threshold(rules):
    return int((rules.get("scoring") or {}).get("threshold", 60))


def notify_group_at(rules):
    return int((rules.get("scoring") or {}).get("notify_group_at", 80))
