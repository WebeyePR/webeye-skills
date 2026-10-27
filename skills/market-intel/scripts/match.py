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
import sys

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
        # 与 sources 同一纪律：模板回退保留（不崩），但必须显式告知——
        # 静默回退会让用户误以为自己在用的规则（改过轮次白名单/信号组）生效了
        sys.stderr.write(
            "[config] 工作区无 rules.json，本次使用 config/rules.json.example 模板运行。\n"
            "         你的自定义规则（信号组/轮次白名单/阈值）不会生效；建议先跑 --init。\n")
        rec = core.jload(core.os.path.join(core.SCRIPT_DIR, "..", "config", "rules.json.example"), None)
    if not rec:
        raise SystemExit("[config-error] 找不到规则文件（工作区 rules.json 与模板均缺失）")
    validate_rules(rec)
    return rec


# ---------- 绝对负向黑名单（2026-10-27 用户明令） ----------

DENY_KEYS = {"version", "deny_entities", "deny_service_providers", "deny_by_round",
             "allow_round", "deny_unlisted_check", "_comment", "_comment_round",
             "_comment_allow_round", "_comment_unlisted", "comment_zh",
             "use_builtin_default"}

# 内置默认黑名单模板（随技能一起提交，等于"开箱即有保护"）
BUILTIN_DENYLIST = core.os.path.join(core.SCRIPT_DIR, "..", "config", "denylist.json.example")


def _merge_builtin(rec):
    """把内置模板作为底座并入用户配置：用户条目优先，模板只补空缺。

    语义与用户口径（2026-10-27 注）一致：「如果用户不反对或没有则默认使用这个」。
    只补**空的**维度（缺字段或空数组），用户显式给了内容就不动。

    2026-10-27 补 `deny_unlisted_check`：它此前不在补齐清单里，于是用户只覆盖
    实体名单时（`{"version":1,"deny_entities":[...]}`）该开关恒为 None →
    「已上市」检测整段失效（实测：含"纳斯达克上市"的档案未拦截）。
    该键是**布尔**，不能用 `not out.get(key)` 判空——用户显式写 false 会被模板
    的 true 覆盖回来（等于剥夺出口）。故布尔键只在**键完全缺失**时补。
    """
    base = core.jload(BUILTIN_DENYLIST, None) or {}
    out = dict(rec or {})
    for key in ("deny_entities", "deny_service_providers", "deny_by_round", "allow_round"):
        if not out.get(key):
            if base.get(key):
                out[key] = list(base[key])
    for key in ("deny_unlisted_check",):
        if key not in out and base.get(key) is not None:
            out[key] = base[key]
    return out


def load_denylist(ws):
    """加载潜客黑名单。优先级：工作区 denylist.json > 内置模板（config/denylist.json.example）。

    命中即**硬丢弃**（不落 leads.jsonl、不进池、不上报）——用户口径：
    推巨头/成熟服务商 = 纯噪音，比漏报更糟。

    三层语义（2026-10-27 按用户注 2 强化）：
      1. 工作区没有 denylist.json → 直接用内置模板（开箱即有 117 巨头 + 49 服务商 + 12 禁轮次）；
      2. 工作区有文件但某个维度为空（`{"version":1}` / `deny_entities: []`）
         → **自动用模板补该维度**（否则"配了文件却零过滤"，正是用户抱怨的场景复现）；
         想真正清空必须显式写 `"use_builtin_default": false`；
      3. 用户显式给了内容 → 完全用用户的，不掺模板。
    """
    path = core.os.path.join(ws, "denylist.json")
    rec = core.jload(path, None)
    explicit_off = isinstance(rec, dict) and rec.get("use_builtin_default") is False

    if rec is None:
        # 场景 1：工作区无文件 → 纯内置模板
        rec = core.jload(BUILTIN_DENYLIST, None)
        if rec is not None:
            sys.stderr.write("[denylist] 工作区无 denylist.json，使用内置默认黑名单"
                             "（%d 巨头 / %d 服务商 / %d 禁轮次）。"
                             "要自定义请 copy 到工作区后修改。\n"
                             % (len(rec.get("deny_entities") or []),
                                len(rec.get("deny_service_providers") or []),
                                len(rec.get("deny_by_round") or [])))
        else:
            sys.stderr.write("[denylist][warn] 内置模板缺失且工作区无配置，本轮不做巨头过滤\n")
            return {"deny_entities": [], "deny_service_providers": [], "deny_by_round": [],
                    "allow_round": [], "deny_unlisted_check": False}
    elif not explicit_off:
        # 场景 2：有文件但名单空 → 用模板补空缺维度，并明确告知
        filled = _merge_builtin(rec)
        empty_dims = [k for k in ("deny_entities", "deny_service_providers", "deny_by_round")
                      if not (rec.get(k) or []) and (filled.get(k) or [])]
        if empty_dims:
            sys.stderr.write("[denylist] 你的 denylist.json 中 %s 为空，已自动并入内置默认"
                             "（避免「配了文件却零过滤」）。要真正清空请显式写 "
                             "\"use_builtin_default\": false\n" % "、".join(empty_dims))
        rec = filled

    unknown = set(rec) - DENY_KEYS
    if unknown:
        sys.stderr.write("[denylist][warn] 未知字段（可能拼写错，已忽略）: %s\n"
                         % ", ".join(sorted(unknown)))
    return rec


def _deny_words(deny):
    """把两类实体名单合并成 (归一化词条 -> 分类标签) 映射。"""
    words = {}
    for w in deny.get("deny_entities") or []:
        n = core.norm(w).lower()
        if n:
            words[n] = "超级巨头/独角兽"
    for w in deny.get("deny_service_providers") or []:
        n = core.norm(w).lower()
        if n:
            words.setdefault(n, "成熟出海服务商/友商")
    return words


def _is_cjk(text):
    """词条是否含中日韩字符——决定用子串匹配还是词边界匹配。"""
    return any("\u4e00" <= ch <= "\u9fff" for ch in text)


def _word_hit(word, blob):
    """词条在名称文本中是否命中（v0.6.5 修：CJK 走子串、拉丁走词边界）。

    **CJK 词条用子串匹配**：中文没有词边界，且中文公司名多为「XX科技」后缀组合，
    「希音」出现在「希音集团」里必须命中，子串是唯一可行语义。

    **拉丁词条必须按词边界匹配**——这是 v0.6.5 修的严重误伤：
    旧实现一律子串匹配，黑名单里的短英文名会命中大量无关公司，实测 14/14 全误伤：
      `nio`（蔚来）命中 Union AI / Senior Robotics / Companion Labs / Onion Games；
      `meta`（Meta）命中 Metaverse Works / MetaStudio；
      `apple`（Apple）命中 Pineapple Studio；
      `grab`/`goto`（Grab/GoTo）命中 Grabber Tech / GoTopper；
      `branch`（Branch 归因服务商）命中 Branchline Media。
    用户的核心诉求恰恰是"专盯腰部黑马"——把黑马当巨头丢掉，比漏过滤更伤业务
    （漏过滤只是多一条噪音，误伤是**目标客户永久消失**，且线索池无从发现）。
    故拉丁词按 `(?<![a-z0-9])词(?![a-z0-9])` 匹配：词条前后不能紧邻字母数字，
    但允许连字符/空格分隔（`Meta Platforms` 命中，`Metamorphic` 不命中）。
    """
    if _is_cjk(word):
        return word in blob
    return re.search(r"(?<![a-z0-9])" + re.escape(word) + r"(?![a-z0-9])", blob) is not None


_ROUND_SEQ = {"种子": "种子", "天使": "天使", "pre-a": "pre-a", "prea": "pre-a",
              "pre-b": "pre-b", "preb": "pre-b"}


def _norm_round(raw):
    """融资轮次归一化：抹平空格/连字符/全角，把英文 `Series X` 折成 `x轮` 形态。

    v0.6.5 修两个实测漏拦：
      ① **空格导致漏拦**：`C 轮` / `E 轮` 因词条是 `c轮`、原文带空格 → 子串不成立
         → C 轮公司被当成长型公司放行（用户明令"已上市或 C 轮及以上绝对丢弃"）。
         归一化去掉所有空白与 `-`/`_` 后比较。
      ② **英文写法完全不在名单里**：外刊源写 `Series C` / `series d`，而 deny 只有
         `C轮`/`D轮` → 全部漏拦。折成 `c轮` 后即可命中。
    只用于**轮次**比较：轮次是封闭枚举，激进归一不会误伤业务语义。
    """
    if not raw:
        return ""
    t = core.norm(str(raw)).lower()
    t = re.sub(r"[\s\-_·]+", "", t)
    # series c / series c+ → c轮 / c+轮
    m = re.search(r"series([a-f])(\+?)", t)
    if m:
        return "%s%s轮" % (m.group(1), m.group(2))
    # 纯字母轮次（如 round=C）在很多英文源里出现，同义折成 c轮
    m = re.search(r"^(?:round:?)?([a-f])(\+?)$", t)
    if m:
        return "%s%s轮" % (m.group(1), m.group(2))
    return t


def check_denylist(fact, deny):
    """判定一个企业档案是否命中黑名单。

    返回 (hit: bool, reason: str)。命中即硬丢弃。
    匹配语义：实体名/别名中出现黑名单词条，大小写不敏感。
      - 中文词条：子串匹配（中文无词边界）；
      - 拉丁词条：词边界匹配（防 `nio`/`meta`/`apple` 误伤无敌公司，见 `_word_hit`）。
    """
    if not deny:
        return False, None
    words = _deny_words(deny)
    names = [fact.get("name") or ""] + list(fact.get("aliases") or [])
    name_blob = " ".join(core.norm(n).lower() for n in names if n)
    for word, label in words.items():
        if word and _word_hit(word, name_blob):
            return True, "命中黑名单（%s）：%s" % (label, word)

    # 轮次否决：C 轮及以上 / 已上市 / 战略融资 / 并购 → 丢弃
    deny_rounds = [_norm_round(r) for r in (deny.get("deny_by_round") or []) if r]
    allow_rounds = [_norm_round(r) for r in (deny.get("allow_round") or []) if r]
    for fe in fact.get("funding_events") or []:
        rnd = _norm_round(fe.get("round"))
        if not rnd:
            continue
        # 先判白名单：明确属于种子~B 就不因 deny 列表里的模糊词误伤
        if any(a and a in rnd for a in allow_rounds):
            continue
        for d in deny_rounds:
            if d and d in rnd:
                return True, "融资轮次超出口径（%s，只要种子~B 轮）" % fe.get("round")

    # 已上市字样（中英文都查：外刊源常写 "listed on Nasdaq"）
    if deny.get("deny_unlisted_check"):
        blob = " ".join(core.norm(str(v)).lower() for v in [
            fact.get("name"), fact.get("profile"), fact.get("business"),
        ] if v)
        for kw in ("已上市", "上市公司", "纳斯达克上市", "纽交所上市", "ipo 上市"):
            if kw in blob:
                return True, "已上市企业（命中「%s」）" % kw
        for kw in ("nasdaq", "nyse", "listed on", "went public", "stock code"):
            if _word_hit(kw, blob):
                return True, "已上市企业（命中「%s」）" % kw
    return False, None


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
    """融资事件：within_days 内有事件；可选 min_amount_cny / round_any 过滤。

    **时间不可解析时按「不满足时效」处理，不当命中原样放过**（2026-10-27 修复）：
    旧实现 `if within and ts and ...` —— `ts is None` 时整条时效判断被跳过，
    一条"去年10月"这类无法解析的旧融资会被当成"近 30 天内融资"命中 P0 线索
    （契约未强制 date 格式，AI 写中文日期就会踩）。线索误报比漏报更伤信任：
    市场同事按名单建联却扑空 = 线索池失效。故改为：within_days 生效时，
    ts 解析不出 → 该事件不参与命中（保守不虚报），并在 detail 里注明原因。
    """
    within = cond.get("within_days")
    min_amount = cond.get("min_amount_cny")
    rounds = cond.get("round_any") or []
    now = core.time.time()
    for fe in fact.get("funding_events") or []:
        raw_date = fe.get("date")
        ts = core.parse_iso(raw_date)
        if within:
            if ts is None:
                continue  # 时效要求下，时间不可解析 = 无法确认"近期"，不命中
            if not 0 <= (now - ts) / 86400.0 <= within:
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
        detail = "融资: %s %s%s" % (raw_date or "",
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
            if ts is None or not 0 <= (now - ts) / 86400.0 <= within:
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
            # 榜单新晋按首次观察；发布／增长按事件日期，禁止用今天抽取替代。
            date = oe.get("date")
            if kind == "rank_new_entry":
                date = date or oe.get("extracted_at")
            ts = core.parse_iso(date)
            if ts is None or not 0 <= (now - ts) / 86400.0 <= within:
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
    origin = str(fact.get("origin") or "").strip().upper()
    if origin == "CN":
        return True, "中国企业（判断申报/词表标注）"
    if origin not in ("", "UNKNOWN", "未知"):
        return False, None  # 明确的非中国企业不接受关键词覆盖。
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
    explicit = fact.get("going_global")
    if explicit is False or str(explicit).lower() == "false":
        return False, None  # 缺少标注与明确否定必须区分。
    if explicit is True or str(explicit).lower() == "true":
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
