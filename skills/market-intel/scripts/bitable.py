# -*- coding: utf-8 -*-
"""猎隼线索池多维表格写入（notify.json -> bitable 块驱动）。

设计纪律：
- 断点：`--bitable` 先产出 out/bitable-pending.json 预览（不触网不写入）；
  `--bitable --commit` 才真正经 lark-cli 写入（预览 -> 确认 -> 写入）。
- 去重：写入前按「公司名称」精确检索（主名 + 档案别名候选），已存在则跳过并记录，
  绝不重复建行；写入失败的线索保留待写状态，下轮自动重试。
- 选项安全：提交前拉取最新 field-list 校验选项值，不在选项里的值丢弃并告警，
  绝不自造选项（select 写错值会静默失败或污染下拉）。
- 分层填写：机器可靠字段直接写；技术底座/CEO/规模/破冰话术等走 AI 调研补全
  （契约见 references/bitable-fields.md，落到 log/enrichments.jsonl），select 值
  同样过选项校验——宁可少填不可错填；对接销售/文本 9 留人工。
"""
import json
import os
import re
import subprocess
import sys
import time

import core

# 机器可填字段（映射逻辑见 build_record）；其余字段留人工，勿自作主张。
DATETIME_FIELDS = {"抓取时间"}
SELECT_FIELDS = {"所属赛道", "标签细分", "融资动态", "企业规模", "成立年限",
                 "所在城市/区域", "跟进状态", "曝光媒体/合作渠道"}

# AI 调研补全契约（enrichments.jsonl fields -> 表字段），标准见 references/bitable-fields.md
ENRICH_TEXT_FIELDS = {"tech_stack": "技术底座", "ceo": "CEO/高管姓名",
                      "contact": "联系方式(LinkedIn/邮箱)", "investors": "背后资方/投资机构",
                      "icebreaker": "打单破冰话术", "profile": "一句话业务描述",
                      "opportunity": "GCP切入点", "website": "官网链接"}
ENRICH_SELECT_FIELDS = {"size": "企业规模", "founded": "成立年限", "city": "所在城市/区域"}

# 调研完整度门槛：14 个必研字段，缺一不可——每个字段要么给出调研值，
# 要么填该字段的兜底值（查不到≠可以不写，兜底值本身就是调研结论）。
# 这是线索入池的硬前置：调研不齐全的线索 --commit 会拒绝写入（blocked），
# 因为残缺画像会让破冰话术失真、后续潜在线索判断失去依据。
REQUIRED_ENRICH_FIELDS = {
    "profile": "一句话业务描述（按证据链概括）",
    "business": "主营业务/产品线（查不到填 未公开）",
    "funding": "融资动态：最新轮次+金额+时间（查不到填 未公开）",
    "investors": "背后资方/投资机构（查不到填 未公开）",
    "financials": "经营规模：员工/营收量级（查不到填 未公开）",
    "tech_stack": "技术底座（查不到填 未查明）",
    "ceo": "CEO/高管姓名（查不到填 未查询到）",
    "size": "企业规模（查不到填 未公开）",
    "founded": "成立年限（查不到填 未公开）",
    "city": "所在城市/区域（查不到填 未公开）",
    "contact": "联系方式（查不到填 未查询到有效信息）",
    "website": "官网链接（确无独立官网填 无官网）",
    "opportunity": "GCP切入点（结合业务模式推演，必须给出）",
    "icebreaker": "打单破冰话术 100~150 字",
}


# 写入终态：不再进待写队列。failed 不是终态——网络抖动/限流等瞬时故障必须可重试，
# 此前按「!= pending」过滤会把写入失败的线索永久静默淘汰（一次失败=丢单）。
TERMINAL_BITABLE_STATUS = ("pushed", "exists", "filtered")


def retryable(ld):
    """线索是否（仍）需要写入猎隼池：非终态即待写（含无状态新线索与 failed 重试）。"""
    return (ld or {}).get("bitable_status") not in TERMINAL_BITABLE_STATUS


def enrichment_gaps(enr):
    """调研完整度校验：返回缺失的必研字段中文名列表。

    入参兼容两种形态：完整调研记录 {"fields": {...}} 或直接传 fields dict
    （load_enrichments 的返回值就是 fields dict）。空 dict / None 全缺。
    """
    if isinstance(enr, dict) and isinstance(enr.get("fields"), dict):
        f = enr["fields"]
    else:
        f = enr if isinstance(enr, dict) else {}
    return [label for key, label in REQUIRED_ENRICH_FIELDS.items()
            if f.get(key) in (None, "", [], {})]


def load_enrichments(ws):
    """读 AI 调研补全 log/enrichments.jsonl -> {entity_id: fields}（后写覆盖先写）。"""
    out = {}
    for r in core.read_jsonl(os.path.join(ws, "log", "enrichments.jsonl")):
        eid = r.get("entity_id")
        if eid and isinstance(r.get("fields"), dict):
            out[eid] = r["fields"]
    return out


def website_markdown(url):
    """官网链接惯例：有官网 [官网](url)；AI 调研同样只给 URL，由这里统一包成 markdown。"""
    u = str(url or "").strip()
    if not u:
        return None
    return u if u.startswith("[") else "[官网](%s)" % u


def _cli(args, cfg):
    cmd = ["lark-cli", "base"] + args + ["--base-token", cfg["app_token"],
                                         "--table-id", cfg["table_id"],
                                         "--as", cfg.get("identity", "user")]
    out = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    try:
        return json.loads(out.stdout)
    except Exception:
        raise RuntimeError("lark-cli 输出解析失败: %s | stderr=%s"
                           % (out.stdout[:200], out.stderr[:200]))


def norm_round(text, options=None):
    """融资轮次归一为表格选项口径：'A+'->'A+轮'，'pre-A'->'Pre-A轮'，'战略'->'战略融资'。"""
    if not text:
        return None
    t = core.norm(str(text)).replace(" ", "").upper()
    t = t.replace("PRE-", "Pre-").replace("PREA", "Pre-A")
    if not t:
        return None
    if "战略" in t:
        return "战略融资"
    if t == "未融资":
        return "未融资"
    if not t.endswith("轮"):
        t += "轮"
    # 大小写规整：Pre-A轮 / A+轮 / B轮
    t = re.sub(r"PRE-([ABCD])", lambda m: "Pre-" + m.group(1), t, flags=re.IGNORECASE)
    t = re.sub(r"^([ABCD])\+", lambda m: m.group(1) + "+", t)
    if options is not None and t not in options:
        return None
    return t


def _map_first(values, mapping):
    """行业 -> 单值映射（取第一个可映射项）。"""
    for v in values or []:
        m = mapping.get(v)
        if m:
            return m
    return None


def map_tags(industries, evidence_text, mapping, options):
    """行业 -> 标签细分（多选）：映射表命中 + 证据文本里的地区关键词。去重保序。

    options 为空集时表示离线构建阶段不做选项约束（提交前还有一次全量校验兜底）。
    """
    tags = []
    for v in industries or []:
        t = mapping.get(v)
        if t and (not options or t in options) and t not in tags:
            tags.append(t)
    for tag, pattern in (mapping.get("_regions") or {}).items():
        if (not options or tag in options) and re.search(pattern, evidence_text or "", re.IGNORECASE) \
                and tag not in tags:
            tags.append(tag)
    return tags[:5]


def latest_funding(fact):
    """档案里最近一笔融资（按 date 退化到 extracted_at 取最新）。"""
    evs = [e for e in (fact or {}).get("funding_events") or [] if e]
    if not evs:
        return None
    evs.sort(key=lambda e: e.get("date") or e.get("extracted_at") or "")
    return evs[-1]


def apply_enrichment(rec, lead, cfg, enrichment, options=None):
    """把 AI 调研 fields 并进记录（select 值过选项校验；空值不写）。

    调研值覆盖机器值（更权威来源优先）；profile/opportunity 也允许调研修订。
    """
    if not enrichment:
        return rec
    fld = cfg.get("field_map") or {}
    o = options or {}
    for key, fname in ENRICH_TEXT_FIELDS.items():
        v = enrichment.get(key)
        if v in (None, "", []):
            continue
        fname = fld.get(key, fname)
        if fname == "官网链接":
            v = website_markdown(v)
        rec[fname] = str(v)
    for key, fname in ENRICH_SELECT_FIELDS.items():
        v = enrichment.get(key)
        if v in (None, "", []):
            continue
        fname = fld.get(key, fname)
        allowed = set(o.get(fname) or [])
        vals = v if isinstance(v, list) else [v]
        vals = [x for x in vals if x and (not allowed or str(x) in allowed)]
        if vals:
            rec[fname] = [str(x) for x in vals]
    return rec


def build_record(lead, fact, cfg, options=None, enrichment=None):
    """线索+档案 -> 表格记录（字段名 -> 单元格值）。options 为 {字段: set(选项)} 用于校验。"""
    maps = cfg.get("maps") or {}
    o = options or {}
    fld = cfg.get("field_map") or {}
    rec = {}

    def opt(field):
        return set(o.get(field) or [])

    def put(field, value):
        if value is None or value == "" or value == []:
            return
        if field in SELECT_FIELDS:
            allowed = opt(field)
            vals = value if isinstance(value, list) else [value]
            vals = [v for v in vals if not allowed or v in allowed]
            if not vals:
                return
            rec[field] = vals
        elif field in DATETIME_FIELDS:
            rec[field] = value  # 毫秒时间戳
        else:
            rec[field] = str(value)

    name = lead.get("entity_name") or ""
    put(fld.get("company", "公司名称"), name or None)
    # 业务描述：档案画像优先；占位文案回退为证据链摘要
    desc = (fact or {}).get("profile")
    summary = lead.get("summary") or ""
    if not desc and (not summary or "命中线索规则" in summary):
        ev = [str(e.get("detail") or "") for e in (lead.get("evidence") or [])
              if e.get("detail") and not str(e.get("group") or "").startswith("scope")]
        desc = "；".join(ev[:3]) or None
    put(fld.get("desc", "一句话业务描述"), desc or (summary or None))
    track = _map_first(lead.get("industries") or [], maps.get("industry_to_track") or {})
    put(fld.get("track", "所属赛道"), track)
    evidence_text = " ".join([str(e.get("detail") or "") for e in lead.get("evidence") or []]
                             + [lead.get("summary") or ""])
    tags = map_tags(lead.get("industries") or [], evidence_text,
                    maps.get("industry_to_tags") or {}, opt(fld.get("tags", "标签细分")))
    put(fld.get("tags", "标签细分"), tags)
    fund = latest_funding(fact)
    if fund:
        put(fld.get("round", "融资动态"), norm_round(fund.get("round"), opt(fld.get("round", "融资动态"))))
        put(fld.get("investors", "背后资方/投资机构"), fund.get("investors"))
    src_names = sorted({(s.get("name") or "").strip() for s in lead.get("sources") or [] if s.get("name")})
    media = [maps.get("source_to_media", {}).get(n, n) for n in src_names]
    media = [m for m in media if m and (not opt(fld.get("media", "曝光媒体/合作渠道"))
                                        or m in opt(fld.get("media", "曝光媒体/合作渠道")))]
    put(fld.get("media", "曝光媒体/合作渠道"), sorted(set(media)))
    put(fld.get("gcp", "GCP切入点"), (fact or {}).get("opportunity"))
    put(fld.get("status", "跟进状态"), cfg.get("default_status", "未跟进"))
    put(fld.get("captured_at", "抓取时间"),
        int((core.parse_iso(lead.get("logged_at") or core.now_iso()) or time.time()) * 1000))
    # 官网链接：无官网则按表内惯例用新闻报道链接兜底
    url = (fact or {}).get("website") or ""
    if url:
        put(fld.get("website", "官网链接"), "[官网](%s)" % url)
    else:
        news = lead.get("url") or ""
        if news:
            put(fld.get("website", "官网链接"), "[暂无官网(新闻报道)](%s)" % news)
    apply_enrichment(rec, lead, cfg, enrichment, options)
    return rec


def pending_path(ws):
    return os.path.join(ws, "out", "bitable-pending.json")


def build_pending(ws, cfg):
    """把待写线索构建为表格记录预览（离线，不触网）。返回 items。

    写入前用当前规则重算匹配：旧规则时代遗留、现在已不满足 scope 硬筛的线索
    （如非中国企业）标记 filtered 并排除——线索池只收当前规则命中的线索。

    **同样重算黑名单**（2026-10-27 补）：`judge.cmd_input` 生成线索时已做
    `check_denylist` 硬拦截，但这里是**写库前最后一道计算入口**——上一轮就已
    产出、尚未写入的 pending 线索，若其公司后来被用户加入 `denylist.json`
    （实测：声网、C 轮公司等），不重新检查就会被写进线索池，直接违背
    「命中即硬丢弃、绝不上报」。与 scope 失效同样处理：标记 filtered 并排除。
    """
    import facts as facts_mod
    import match as match_mod
    rules = core.jload(os.path.join(ws, "rules.json"), None) or {}
    deny = match_mod.load_denylist(ws)
    enrichments = load_enrichments(ws)
    stale, denied, items, updates, used_eids = [], [], [], {}, set()
    dropped_eids = set()  # 本轮被黑名单/规则淘汰的 entity_id（orphan 护栏要排除它们）
    for ld in core.read_jsonl(os.path.join(ws, "log", "leads.jsonl")):
        if not retryable(ld):
            continue
        fact = facts_mod.load_fact(ws, ld.get("entity_id")) or {}
        # 黑名单复检（命中即 excluded，绝不入池）——比 scope 更硬，放在最前
        hit, why = match_mod.check_denylist(
            fact if fact else {"name": ld.get("entity_name") or "",
                               "aliases": ld.get("aliases") or []}, deny)
        if hit:
            denied.append("%s（%s）" % (ld.get("entity_name") or ld.get("entity_id"), why))
            updates[ld.get("lead_id")] = {"bitable_status": "filtered",
                                          "bitable_note": "入池前复检：%s" % why}
            dropped_eids.add(ld.get("entity_id"))
            continue
        result = match_mod.evaluate_entity(fact, rules) if fact else {"matched_groups": []}
        if not result["matched_groups"]:
            stale.append(ld.get("entity_name") or ld.get("entity_id"))
            updates[ld.get("lead_id")] = {"bitable_status": "filtered",
                                          "bitable_note": "当前规则不再命中"}
            dropped_eids.add(ld.get("entity_id"))
            continue
        eid = ld.get("entity_id")
        enr = enrichments.get(eid)
        # 兜底：调研记录 entity_id 与线索不一致时按 entity_name 匹配（后写覆盖先写）
        if enr is None:
            name = (ld.get("entity_name") or "").split("（")[0]
            for r in reversed(list(core.read_jsonl(os.path.join(ws, "log", "enrichments.jsonl")))):
                if (r.get("entity_name") or "").split("（")[0] == name and isinstance(r.get("fields"), dict):
                    enr = r["fields"]
                    sys.stderr.write("[bitable] %s 的调研按 entity_name 兜底匹配"
                                     "（entity_id=%s 与线索 %s 不一致，建议修正）\n"
                                     % (name, r.get("entity_id"), eid))
                    break
        if enr:
            used_eids.add(eid)
        gaps = enrichment_gaps(enr)
        if gaps:
            sys.stderr.write("[bitable] %s 调研未达完整度门槛（缺 %d 项）：%s\n"
                             % (ld.get("entity_name") or eid, len(gaps), "、".join(gaps)))
        items.append({
            "lead_id": ld.get("lead_id"),
            "entity_id": eid,
            "company": ld.get("entity_name"),
            # 别名带下去：写入前去重检索时兜底候选，防同公司异写重复建行
            "aliases": (fact or {}).get("aliases") or [],
            "research_gaps": gaps,
            "record": build_record(ld, fact, cfg, enrichment=enr),
        })
    # 护栏：存在调研却没被任何待写线索消费——多半是 entity_id 写错（静默丢字段的根因）
    # 注意排除本轮被黑名单/规则淘汰的线索：它们本就该排除，其调研未被消费属预期，
    # 不排除会把「正常拦截」误报成「entity_id 不一致」，掩盖真正的笔误（2026-10-27）。
    filtered_eids = {ld.get("entity_id") for ld in
                     core.read_jsonl(os.path.join(ws, "log", "leads.jsonl"))
                     if ld.get("bitable_status") == "filtered"} | dropped_eids
    orphan = [e for e in enrichments if e not in used_eids and e not in filtered_eids]
    if orphan:
        sys.stderr.write("[bitable] 警告：%d 条调研未匹配到任何待写线索（entity_id 不一致？）: %s\n"
                         % (len(orphan), "、".join(orphan)))
    if updates:
        _update_leads(ws, updates)
    if denied:
        sys.stderr.write("[bitable] 命中黑名单，已排除（绝不入池）: %s\n" % "、".join(denied))
    if stale:
        sys.stderr.write("[bitable] 按当前规则不再命中，已排除: %s\n" % "、".join(stale))
    payload = {"built_at": core.now_iso(), "base_token": cfg.get("app_token"),
               "table_id": cfg.get("table_id"), "count": len(items), "records": items}
    core.jdump(pending_path(ws), payload)
    return items


def _fetch_options(cfg):
    """拉取最新字段选项：{字段名: set(选项值)}，用于提交前校验。"""
    d = _cli(["+field-list"], cfg)
    if not d.get("ok"):
        raise RuntimeError("field-list 失败: %s" % json.dumps(d.get("error"), ensure_ascii=False)[:200])
    opts = {}
    for f in (d.get("data") or {}).get("fields", []):
        if f.get("options"):
            opts[f["name"]] = {x.get("name") for x in f["options"] if x.get("name")}
    return opts


def _extract_new_rid(d):
    """从 +record-batch-create 的响应提取新记录 id。

    lark-cli 真实结构：data.record_id_list（数组）；records[].record_id 为旧格式兼容。
    """
    bdata = d.get("data") or {}
    rid_list = bdata.get("record_id_list") or \
        [r.get("record_id") for r in (bdata.get("records") or []) if r.get("record_id")]
    return rid_list[0] if rid_list else None


def _search_company(name, cfg):
    """按公司名称检索是否已有记录（精确匹配校验，忽略大小写）。返回 record_id 或 None。"""
    # 使用已安装 CLI 的 offset 分页；预算耗尽不能当成查无此公司。
    for offset in range(0, 2000, 20):
        d = _cli(["+record-search", "--keyword", name, "--search-field", "公司名称",
                  "--field-id", "公司名称", "--limit", "20", "--offset", str(offset),
                  "--format", "json"], cfg)
        if not d.get("ok"):
            raise RuntimeError("公司查重失败，停止本条创建")
        data = d.get("data")
        if not isinstance(data, dict) or not isinstance(data.get("data"), list) or not isinstance(data.get("record_id_list"), list):
            raise RuntimeError("公司查重响应结构不完整，停止本条创建")
        rows, rids = data["data"], data["record_id_list"]
        if len(rows) != len(rids):
            raise RuntimeError("公司查重行数与记录 ID 数不一致")
        for row, rid in zip(rows, rids):
            cells = row if isinstance(row, list) else [row]
            if cells and str(cells[0]).strip().lower() == str(name).strip().lower() and rid:
                return rid
        if len(rows) < 20:
            return None
    raise RuntimeError("公司查重超过分页预算，停止本条创建")


def _dedupe_candidates(item):
    """去重检索候选名：公司名优先，档案别名兜底（同公司异写防重复建行），上限 4 次检索。

    例：表里已有「月之暗面科技有限公司」，本轮申报「月之暗面」——仅检索主名会
    误判为新公司重复建行；带别名候选逐个检索可命中既有记录。
    """
    names = []
    for n in [item.get("company")] + list(item.get("aliases") or []):
        n = str(n or "").strip()
        if n and n not in names:
            names.append(n)
    return names[:4]


def _update_leads(ws, updates):
    """回写 leads.jsonl 的 bitable_status / bitable_record_id。updates: {lead_id: {...}}"""
    path = os.path.join(ws, "log", "leads.jsonl")
    rows = core.read_jsonl(path)
    out = []
    for r in rows:
        u = updates.get(r.get("lead_id"))
        if u:
            r.update(u)
        out.append(r)
    with open(path, "w", encoding="utf-8") as f:
        for r in out:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def commit(ws, cfg, yes=False):
    """把 pending 记录取回、校验、去重后写入表格。返回 (pushed, exists, failed, blocked)。

    调研完整度硬门槛：pending 里 research_gaps 非空的线索拒绝写入（blocked）——
    残缺画像会污染线索池、误导后续潜在线索判断；先补调研（兜底值也算完成）再重跑。
    """
    if not cfg.get("enabled"):
        print("bitable 未启用（notify.json bitable.enabled=false），先校准映射后启用。")
        return 0, 0, 0, 0
    payload = core.jload(pending_path(ws), None)
    if not payload or not payload.get("records"):
        print("无可写记录（先跑 --bitable 生成预览）。")
        return 0, 0, 0, 0
    options = _fetch_options(cfg)
    allowed = {}
    fld = cfg.get("field_map") or {}
    for key, fname in fld.items():
        if fname in options:
            allowed[fname] = options[fname]
    # 选项校验（丢弃不在选项里的值并告警）
    dropped = []
    dropped_map = {}  # lead_id -> 丢弃明细（随写入日志留痕，便于复盘字段映射）
    for it in payload["records"]:
        rec = it["record"]
        for fname, opts in allowed.items():
            if fname in rec and fname in SELECT_FIELDS:
                vals = rec[fname] if isinstance(rec[fname], list) else [rec[fname]]
                bad = [v for v in vals if v not in opts]
                if bad:
                    dropped.append((it["company"], fname, bad))
                    dropped_map.setdefault(it["lead_id"], []).append({"field": fname, "values": bad})
                    keep = [v for v in vals if v in opts]
                    rec[fname] = keep if keep else None
        for k in list(rec):
            if rec[k] is None:
                del rec[k]
    for company, fname, bad in dropped:
        sys.stderr.write("[bitable] %s 的 %s 含无效选项 %s，已丢弃\n" % (company, fname, ",".join(bad)))

    pushed, exists, failed, blocked = 0, 0, 0, 0
    updates, logs = {}, []
    for it in payload["records"]:
        name = it["company"] or ""
        rec = it["record"]
        if not name:
            failed += 1
            continue
        # 调研完整度硬门槛：缺必研字段（连兜底值都没填）的线索不入池
        gaps = it.get("research_gaps") or []
        if gaps:
            blocked += 1
            sys.stderr.write("[bitable] %s 调研未齐全，拒绝入池（缺 %d 项：%s）。"
                             "先按 references/bitable-fields.md 补调研（兜底值也算完成）再重跑。\n"
                             % (name, len(gaps), "、".join(gaps)))
            logs.append({"lead_id": it["lead_id"], "company": name, "result": "blocked",
                         "research_gaps": gaps, "at": core.now_iso()})
            continue
        # 写入前去重：公司名 + 别名候选逐个精确检索，任一命中即已存在（不重复建行）
        rid, hit_name = None, None
        try:
            for cand in _dedupe_candidates(it):
                rid = _search_company(cand, cfg)
                if rid:
                    hit_name = cand
                    break
        except (RuntimeError, OSError, subprocess.SubprocessError) as exc:
            failed += 1
            updates[it["lead_id"]] = {"bitable_status": "failed", "bitable_error": str(exc)}
            logs.append({"lead_id": it["lead_id"], "company": name, "result": "failed",
                         "error": str(exc), "at": core.now_iso()})
            sys.stderr.write("[bitable] %s：%s；未创建，保留重试\n" % (name, exc))
            continue
        if rid:
            exists += 1
            updates[it["lead_id"]] = {"bitable_status": "exists", "bitable_record_id": rid}
            logs.append({"lead_id": it["lead_id"], "company": name, "result": "exists",
                         "record_id": rid,
                         **({"matched_name": hit_name} if hit_name != name else {}),
                         "at": core.now_iso(),
                         **({"dropped_options": dropped_map[it["lead_id"]]} if it["lead_id"] in dropped_map else {})})
            continue
        body = json.dumps({"create_records": [rec]}, ensure_ascii=False)
        d = _cli(["+record-batch-create", "--json", body] + (["--yes"] if yes else []), cfg)
        if d.get("ok"):
            new_rid = _extract_new_rid(d)
            if not new_rid:
                # 防御：个别响应结构解析不到 record_id，用检索兜底，保证「查看线索」定位链不断
                try:
                    new_rid = _search_company(name, cfg)
                except (RuntimeError, OSError, subprocess.SubprocessError):
                    # 创建已成功，查 ID 失败不得把它改为可重试创建。
                    new_rid = None
                if new_rid:
                    sys.stderr.write("[bitable] %s 写入成功但响应未含 record_id，检索兜底到 %s\n"
                                     % (name, new_rid))
            pushed += 1
            updates[it["lead_id"]] = {"bitable_status": "pushed", "bitable_record_id": new_rid}
            logs.append({"lead_id": it["lead_id"], "company": name, "result": "pushed",
                         "record_id": new_rid, "at": core.now_iso(),
                         **({"dropped_options": dropped_map[it["lead_id"]]} if it["lead_id"] in dropped_map else {})})
        else:
            failed += 1
            err = json.dumps(d.get("error", d), ensure_ascii=False)[:200]
            sys.stderr.write("[bitable] %s 写入失败: %s\n" % (name, err))
            updates[it["lead_id"]] = {"bitable_status": "failed", "bitable_error": err}
            logs.append({"lead_id": it["lead_id"], "company": name, "result": "failed",
                         "error": err, "at": core.now_iso()})
    if updates:
        _update_leads(ws, updates)
    for lg in logs:
        core.append_jsonl(os.path.join(ws, "log", "bitable.jsonl"), lg)
    return pushed, exists, failed, blocked


def audit_enrichments(ws):
    """调研完整度审计（--research-check）：逐企业核对 14 必研字段。

    检查对象 = leads.jsonl 里所有非 filtered 线索对应的企业：
    - 无调研记录 → 未调研；
    - 有调研但缺字段（连兜底值都没填）→ 不完整。
    孤儿调研（没对应任何线索）只告警不计失败（多为 entity_id 笔误，另见 build_pending 提示）。
    返回失败企业数（0 = 全部达标）。
    """
    leads = [l for l in core.read_jsonl(os.path.join(ws, "log", "leads.jsonl"))
             if (l.get("bitable_status") or "pending") != "filtered"]
    enrichments = load_enrichments(ws)
    lead_eids = {l.get("entity_id") for l in leads}
    name_by_eid = {l.get("entity_id"): l.get("entity_name") or l.get("entity_id") for l in leads}
    orphan = [e for e in enrichments if e not in lead_eids]
    bad, table = [], []
    for eid in sorted(lead_eids):
        enr = enrichments.get(eid)
        if enr is None:
            # 与 build_pending 相同的 entity_name 兜底
            name = (name_by_eid.get(eid) or "").split("（")[0]
            for r in reversed(list(core.read_jsonl(os.path.join(ws, "log", "enrichments.jsonl")))):
                if (r.get("entity_name") or "").split("（")[0] == name and isinstance(r.get("fields"), dict):
                    enr = r["fields"]
                    break
        gaps = enrichment_gaps(enr)
        if not enr:
            gaps = ["【未调研】"] + list(REQUIRED_ENRICH_FIELDS.values())
        label = name_by_eid.get(eid) or eid
        if gaps:
            bad.append(eid)
            table.append("✗ %s：%s" % (label, "；".join(gaps[:4]) + ("等 %d 项" % len(gaps) if len(gaps) > 4 else "")))
        else:
            table.append("✓ %s：14/14 齐全" % label)
    for line in table:
        print(line)
    if orphan:
        sys.stderr.write("[audit] 孤儿调研（未匹配任何线索，entity_id 笔误？）：%s\n" % "、".join(orphan))
    if bad:
        sys.stderr.write("[audit] %d/%d 企业调研未达完整度门槛——先补调研再 --bitable --commit\n"
                         % (len(bad), len(lead_eids)))
    else:
        print("调研完整度审计通过：%d/%d 企业齐全" % (len(lead_eids), len(lead_eids)))
    return len(bad)
