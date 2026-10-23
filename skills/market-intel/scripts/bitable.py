# -*- coding: utf-8 -*-
"""猎隼线索池多维表格写入（notify.json -> bitable 块驱动）。

设计纪律：
- 断点：`--bitable` 先产出 out/bitable-pending.json 预览（不触网不写入）；
  `--bitable --commit` 才真正经 lark-cli 写入（预览 -> 确认 -> 写入）。
- 去重：写入前按「公司名称」精确检索，已存在则跳过并记录，绝不重复建行。
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
    """
    import facts as facts_mod
    import match as match_mod
    rules = core.jload(os.path.join(ws, "rules.json"), None) or {}
    enrichments = load_enrichments(ws)
    stale, items, updates = [], [], {}
    for ld in core.read_jsonl(os.path.join(ws, "log", "leads.jsonl")):
        if ld.get("bitable_status", "pending") != "pending":
            continue
        fact = facts_mod.load_fact(ws, ld.get("entity_id")) or {}
        result = match_mod.evaluate_entity(fact, rules) if fact else {"matched_groups": []}
        if not result["matched_groups"]:
            stale.append(ld.get("entity_name") or ld.get("entity_id"))
            updates[ld.get("lead_id")] = {"bitable_status": "filtered",
                                          "bitable_note": "当前规则不再命中"}
            continue
        items.append({
            "lead_id": ld.get("lead_id"),
            "entity_id": ld.get("entity_id"),
            "company": ld.get("entity_name"),
            "record": build_record(ld, fact, cfg, enrichment=enrichments.get(ld.get("entity_id"))),
        })
    if updates:
        _update_leads(ws, updates)
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


def _search_company(name, cfg):
    """按公司名称检索是否已有记录（精确匹配校验）。返回 record_id 或 None。"""
    d = _cli(["+record-search", "--keyword", name, "--search-field", "公司名称",
              "--field-id", "公司名称", "--limit", "20", "--format", "json"], cfg)
    if not d.get("ok"):
        return None
    data = (d.get("data") or {})
    rows = data.get("data") or []
    rids = data.get("record_id_list") or []
    for row, rid in zip(rows, rids):
        # 行是投影字段值数组（公司名称在首位）；精确相等才认定已存在
        cells = row if isinstance(row, list) else [row]
        if cells and str(cells[0]).strip() == str(name).strip() and rid:
            return rid
    return None


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
    """把 pending 记录取回、校验、去重后写入表格。返回 (pushed, exists, failed)。"""
    if not cfg.get("enabled"):
        print("bitable 未启用（notify.json bitable.enabled=false），先校准映射后启用。")
        return 0, 0, 0
    payload = core.jload(pending_path(ws), None)
    if not payload or not payload.get("records"):
        print("无可写记录（先跑 --bitable 生成预览）。")
        return 0, 0, 0
    options = _fetch_options(cfg)
    allowed = {}
    fld = cfg.get("field_map") or {}
    for key, fname in fld.items():
        if fname in options:
            allowed[fname] = options[fname]
    # 选项校验（丢弃不在选项里的值并告警）
    dropped = []
    for it in payload["records"]:
        rec = it["record"]
        for fname, opts in allowed.items():
            if fname in rec and fname in SELECT_FIELDS:
                vals = rec[fname] if isinstance(rec[fname], list) else [rec[fname]]
                bad = [v for v in vals if v not in opts]
                if bad:
                    dropped.append((it["company"], fname, bad))
                    keep = [v for v in vals if v in opts]
                    rec[fname] = keep if keep else None
        for k in list(rec):
            if rec[k] is None:
                del rec[k]
    for company, fname, bad in dropped:
        sys.stderr.write("[bitable] %s 的 %s 含无效选项 %s，已丢弃\n" % (company, fname, ",".join(bad)))

    pushed, exists, failed = 0, 0, 0
    updates, logs = {}, []
    for it in payload["records"]:
        name = it["company"] or ""
        rec = it["record"]
        if not name:
            failed += 1
            continue
        rid = _search_company(name, cfg)
        if rid:
            exists += 1
            updates[it["lead_id"]] = {"bitable_status": "exists", "bitable_record_id": rid}
            logs.append({"lead_id": it["lead_id"], "company": name, "result": "exists",
                         "record_id": rid, "at": core.now_iso()})
            continue
        body = json.dumps({"create_records": [rec]}, ensure_ascii=False)
        d = _cli(["+record-batch-create", "--json", body] + (["--yes"] if yes else []), cfg)
        if d.get("ok"):
            new_rid = ((d.get("data") or {}).get("records") or [{}])[0].get("record_id")
            if not new_rid:
                # 防御：个别响应结构解析不到 record_id，用检索兜底，保证「查看线索」定位链不断
                new_rid = _search_company(name, cfg)
                if new_rid:
                    sys.stderr.write("[bitable] %s 写入成功但响应未含 record_id，检索兜底到 %s\n"
                                     % (name, new_rid))
            pushed += 1
            updates[it["lead_id"]] = {"bitable_status": "pushed", "bitable_record_id": new_rid}
            logs.append({"lead_id": it["lead_id"], "company": name, "result": "pushed",
                         "record_id": new_rid, "at": core.now_iso()})
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
    return pushed, exists, failed
