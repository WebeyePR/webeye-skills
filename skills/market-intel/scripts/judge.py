#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""判断落盘与线索产出：judged.json -> 流水 / 企业档案 / 规则匹配 / 飞书卡片。

分工沿用 change-watch 的核心设计：
- 脚本（确定性）：覆盖度校验、落盘、档案聚合、规则求值、卡片生成——可复现、可回测；
- 模型（AI）：读 out/latest.json 的变更集，按 references/intel-criteria.md 与
  references/lead-criteria.md 判断，产出 judged.json——这是判断，不是查表。

judged.json 契约（两类记录并存，change_id 是对账主键）：
{
  "intel": [ {"change_id", "type", "significance": high|normal|low|unknown,
              "title", "summary", "url", "sources": [{"name","url"}],
              "notify": bool, "notify_reason", "merged_change_ids"?,
              "discard"?: true, "discard_reason"? } ],
  "facts": [ {"entity_name", "aliases"?, "industries"[], "event_type": funding|hiring|other,
              "event": {...}, "change_ids"[], "confidence": high|medium|low } ]
}

铁律（沿用 change-watch）：漏答一条 = 判断不完整，覆盖度校验会点名并退出码 2；
unknown 落盘不通知；凡通知必先判断；卡片由脚本生成，直接发送、不要手拼。
"""

import argparse
import json
import os
import re
import sys
import time

import core
import diffcore
import facts as facts_mod
import match as match_mod

INTEL_KEYS = {"change_id", "type", "significance", "object", "title", "summary", "url",
              "sources", "notify", "notify_reason", "merged_change_ids",
              "discard", "discard_reason", "activity", "number_notes",
              "suppressed_by_guard"}
NOTIFY_KEYS = {"version", "notify", "digest", "bitable"}
NOTIFY_BLOCK_KEYS = {"user_id", "chat_id", "mention"}

SIG_LABEL = {"high": ("重要", "red"), "normal": ("常规", "violet"), "low": ("次要", "neutral")}
SIG_ORDER = {"high": 0, "normal": 1, "low": 2}
PRIORITY_LABEL = {"P0": ("P0 · 优先建联", "red"), "P1": ("P1 · 储备", "blue")}


def load_notify(ws):
    rec = core.jload(os.path.join(ws, "notify.json"), None)
    if rec is None:
        rec = core.jload(os.path.join(core.SCRIPT_DIR, "..", "config", "notify.json.example"), None)
    if not rec:
        return {"notify": {}, "bitable": {}}
    core.check_keys(rec, NOTIFY_KEYS, "notify.json")
    if "notify" in rec:
        core.check_keys(rec["notify"], NOTIFY_BLOCK_KEYS, "notify.notify")
    return rec


def mention_md(cfg):
    """notify.mention -> 飞书卡片 @ 标记。all=<at id=all></at>；列表=逐个 @；空=空串。
    语法沿用 change-watch 群卡实测（card-status-*-group.json）。"""
    m = (cfg or {}).get("mention")
    if m == "all":
        return "<at id=all></at>"
    if isinstance(m, list) and m:
        return " ".join("<at id=%s></at>" % u for u in m)
    return ""


def _mention_lead(text, cfg):
    """群卡首行的 @ 提醒元素（mention 配置为空则不产）。"""
    at = mention_md(cfg)
    if not at:
        return None
    return {"tag": "markdown", "content": "%s %s" % (text, at)}


# ---------- grounding：说明里的数字必须能在变更原文里找到 ----------

# 换算兜底系数：单位换算（亿/万、B/M 与中文亿万的互转）+ 美元近似汇率 7.2。
# 说明文字里的"300亿美元 ← $30B"、"3万人 ← 30,000"属合法换算，不算虚构。
_CONV_FACTORS = (1e-4, 1e-2, 0.1, 1.0, 10.0, 1e2, 1e4, 7.2, 1 / 7.2)


def _conv_variants(values):
    """由原文数字生成常见换算候选集合（容差比较用）。"""
    out = set()
    for v in values:
        for k in _CONV_FACTORS:
            c = v * k
            out.add(round(c, 2))
            if abs(c - round(c)) < 1e-9:
                out.add(int(round(c)))
    return out


def grounding_check(rec, change):
    """title/summary 里的数字串应出现在该变更的原文（title+summary）里。

    三层判定（AI 协助申报 + 脚本换算兜底）：
    1. 数字直接出现在原文 → 通过；
    2. AI 在 rec["number_notes"] 里申报了换算依据（如 {"300": "300亿美元=$30B 换算"}）
       → 通过，备注留档（换算是否合理由人工复核，脚本不判断对错）；
    3. 命中常见换算系数（×10、÷10000、×7.2 汇率等）→ 通过，自动备注留档；
    4. 都不满足 → 返回该数字（调用方标 unverified 并告警）。

    返回 (unverified_numbers, grounded_notes)：grounded_notes 供落盘审计。
    """
    src_text = ((change or {}).get("title") or "") + " " + ((change or {}).get("summary") or "")
    probe = (rec.get("title") or "") + " " + (rec.get("summary") or "")
    src_nums = set(re.findall(r"\d+(?:\.\d+)?", src_text))
    declared = rec.get("number_notes") or {}
    variants = _conv_variants({float(x) for x in src_nums})
    grounded_notes, unverified = {}, []
    for n in set(re.findall(r"\d+(?:\.\d+)?", probe)) - src_nums:
        if n in declared:
            grounded_notes[n] = str(declared[n])
        elif _near(n, variants):
            grounded_notes[n] = "换算兜底（单位/汇率系数命中原文数字）"
        else:
            unverified.append(n)
    return sorted(unverified), grounded_notes


def _near(num_str, variants):
    """数字串是否落在换算候选集合的容差范围内。"""
    try:
        v = float(num_str)
    except ValueError:  # pragma: no cover
        return False
    return any(abs(v - c) <= max(0.01, abs(c) * 1e-6) for c in variants)


# ---------- 覆盖度校验 ----------

def coverage_check(latest, intel, fact_records):
    """每个 change_id 必须被 intel（含 merged/discard）或 facts.change_ids 覆盖。返回缺失列表。"""
    changes = (latest or {}).get("changes") or []
    covered = set()
    for r in intel:
        if r.get("change_id"):
            covered.add(r["change_id"])
        for mid in r.get("merged_change_ids") or []:
            covered.add(mid)
    for f in fact_records:
        for cid in f.get("change_ids") or []:
            covered.add(cid)
    missing = [c["change_id"] for c in changes if c["change_id"] not in covered]
    return missing


# ---------- 卡片生成（沿用 notify-card.md 的骨架与纪律） ----------
#
# 视觉差异纪律：market-intel 通知卡用紫色系（violet 头 + report 图标 + violet 块），
# 与 change-watch 的蓝色铃铛监控卡一眼区分；语义色不动——红=强提醒/P0、绿=P1/正常、
# 黄=异常。改样式前先在临时测试群实测渲染，再发正式群。

INTEL_ICON = {"tag": "standard_icon", "token": "report_outlined"}  # 官方图标枚举，实测渲染 ok

CARD_CONFIG = {
    "schema": "2.0",
    "config": {"update_multi": True, "width_mode": "default",
               "style": {"text_size": {
                   "title": {"default": "heading-2", "pc": "heading-2", "mobile": "heading-3"},
                   "body": {"default": "normal", "pc": "normal", "mobile": "normal"},
                   "caption": {"default": "notation", "pc": "notation", "mobile": "notation"}}}},
}


def _block(md_lines, color="violet", last=False):
    els = [m if isinstance(m, dict) else {"tag": "markdown", "content": m} for m in md_lines]
    return {"tag": "interactive_container", "width": "fill", "has_border": True,
            "border_color": color + "-100", "corner_radius": "8px", "background_style": color + "-50",
            "padding": "12px 12px 12px 12px", "vertical_spacing": "4px",
            "margin": "0px 0px 12px 0px" if not last else "0px",
            "elements": els}


def build_card(title, subtitle, blocks, template="violet", tags=None, icon=None):
    header_tags = [{"tag": "text_tag", "text": {"tag": "plain_text", "content": text}, "color": color}
                   for text, color in (tags or [])]
    header = {"title": {"tag": "plain_text", "content": title},
              "subtitle": {"tag": "plain_text", "content": subtitle},
              "template": template,
              "text_tag_list": header_tags}
    if icon:  # 缺省不带图标，行为与旧版一致
        header["icon"] = icon
    return {
        **CARD_CONFIG,
        "header": header,
        "body": {"direction": "vertical", "padding": "12px 12px 20px 12px", "elements": blocks},
    }


def _sources_line(sources):
    if not sources:
        return ""
    first = sources[0]
    more = "、[{}]({})".format(*[sources[1]["name"], sources[1]["url"]]) if len(sources) > 1 else ""
    extra = " 等%d个来源" % len(sources) if len(sources) > 2 else ""
    return "<font color='grey'>来源 · [{name}]({url}){more}{extra}</font>".format(
        name=first.get("name", "来源"), url=first.get("url", ""), more=more, extra=extra)


def intel_blocks(records, limit=5):
    """情报块：重要 -> 常规 -> 次要；块数超限收进末行（纪律与 change-watch 一致）。"""
    ordered = sorted(records, key=lambda r: SIG_ORDER.get(r.get("significance"), 3))
    shown, blocks = ordered[:limit], []
    for r in shown:
        sig_label, sig_color = SIG_LABEL.get(r.get("significance"), ("常规", "violet"))
        head = "<text_tag color='%s'>%s</text_tag> <text_tag color='neutral'>%s</text_tag> **%s**" % (
            sig_color, sig_label, r.get("type") or "动态", r.get("title") or "")
        blocks.append(_block([head, r.get("summary") or "", _sources_line(r.get("sources") or [])]))
    if not blocks:
        return blocks, 0
    if len(ordered) > limit:
        blocks.append({"tag": "markdown",
                       "content": "<font color='grey'>另有 %d 条次要动态</font>" % (len(ordered) - limit),
                       "text_size": "notation"})
    else:
        blocks[-1]["margin"] = "0px"
    return blocks, len(ordered)


def lead_pool_url(lead, view_url):
    """线索定位 URL：已入池（有 record_id）跳到表格里那条记录；否则回退表视图。"""
    rid = (lead or {}).get("bitable_record_id")
    if rid and view_url:
        return "%s&record=%s" % (view_url, rid) if "?" in view_url \
            else "%s?record=%s" % (view_url, rid)
    return view_url


def lead_exists(ld):
    """该公司此前已在猎隼线索池（写入层 exists 分支回写 bitable_status=exists）。

    语义纪律：已存在的记录绝不能再以「新潜在线索」的红色 P0 面孔推送——
    私信卡标注「已存在」，且不进群 @（群卡只催全新线索建联，重复轰炸=骚扰）。
    """
    return (ld or {}).get("bitable_status") == "exists"


def lead_card_split(leads):
    """线索卡分流：(fresh, exists)。群卡 @ 只催 fresh；exists 只在私信卡中性标注带过。"""
    leads = list(leads or [])
    return ([l for l in leads if not lead_exists(l)],
            [l for l in leads if lead_exists(l)])


def _active(leads):
    """过滤掉已被当前规则淘汰（bitable_status=filtered）的线索——它们没入池，
    不该出现在卡片里，更不能声称"已登记线索池"。"""
    return [l for l in leads if (l.get("bitable_status") or "pending") != "filtered"]


def lead_blocks(leads, limit=5, view_url=None):
    leads = _active(leads)
    shown = leads[:limit]
    blocks = []
    for ld in shown:
        pri_label, pri_color = PRIORITY_LABEL.get(ld.get("priority") or "P1", PRIORITY_LABEL["P1"])
        if lead_exists(ld):
            # 已在线索池：中性标注置前、violet 块——不是新线索，是重复提示
            head = ("<text_tag color='neutral'>已存在·已在线索池</text_tag> "
                    "<text_tag color='%s'>%s</text_tag> <text_tag color='neutral'>%s</text_tag> "
                    "**%s** · 匹配度 %d" % (
                        pri_color, pri_label, "/".join(ld.get("industries") or []) or "未分类",
                        ld.get("entity_name") or "未命名", ld.get("score", 0)))
        else:
            head = "<text_tag color='%s'>%s</text_tag> <text_tag color='neutral'>%s</text_tag> **%s** · 匹配度 %d" % (
                pri_color, pri_label, "/".join(ld.get("industries") or []) or "未分类",
                ld.get("entity_name") or "未命名", ld.get("score", 0))
        rules_line = "命中规则：" + "；".join(ld.get("matched_group_names") or ld.get("matched_groups") or [])
        ev_lines = [e.get("detail") for e in (ld.get("evidence") or []) if e.get("detail")][:3]
        body = rules_line + ("：" + "；".join(ev_lines) if ev_lines else "")
        els = [head, body]
        if ld.get("profile"):
            els.append("业务描述：%s" % ld["profile"])
        if ld.get("opportunity"):
            els.append("**最大机会：%s**" % ld["opportunity"])
        els.append(_sources_line(ld.get("sources") or []))
        pool_url = lead_pool_url(ld, view_url)
        if pool_url:
            els.append(_button("已在线索池·查看既有记录" if lead_exists(ld) else "🔍 查看线索",
                               pool_url, size="small"))
        blocks.append(_block(els, color="violet" if lead_exists(ld)
                             else ("red" if ld.get("priority") == "P0" else "green")))
    if len(leads) > limit:
        blocks.append({"tag": "markdown",
                       "content": "<font color='grey'>另有 %d 条线索</font>" % (len(leads) - limit),
                       "text_size": "notation"})
    if blocks:
        blocks[-1]["margin"] = "0px"
    return blocks


def save_leads_latest(ws, new_leads, now, touched):
    """落盘待处理线索视图（out/leads-latest.json）。

    防覆盖丢失：上一轮视图里尚未入池（status=new 且无 record_id）的线索并入本轮，
    否则连续两轮判断时前一轮待办会被静默覆盖（流水虽可查，但待办视图丢失会漏闭环）。
    结转前必须回查 leads.jsonl 的最新写入状态：视图是落盘时刻的快照，其间的
    --bitable --commit 可能已把线索推送入池——按快照结转会把已推送的线索再次
    当待办展示，造成重复通知（2026-10-26 文远知行实例）。
    touched：本轮触及实体集合（或其数量）。
    """
    prev_latest = core.jload(os.path.join(ws, "out", "leads-latest.json"), None) or {}
    cur_by_id = {l.get("lead_id"): l for l in
                 core.read_jsonl(os.path.join(ws, "log", "leads.jsonl"))}
    fresh_keys = {l.get("dedupe_key") for l in new_leads}
    carried = []
    for l in (prev_latest.get("leads") or []):
        if l.get("status") != "new" or l.get("record_id"):
            continue
        if l.get("dedupe_key") in fresh_keys:
            continue
        cur = cur_by_id.get(l.get("lead_id")) or {}
        if (cur.get("bitable_status") or "pending") != "pending":
            continue  # 期间已推送/已存在/已淘汰：不再是待办，不重复通知
        carried.append(l)
    n_matched = touched if isinstance(touched, int) else len(touched or [])
    core.jdump(os.path.join(ws, "out", "leads-latest.json"),
               {"run_at": now, "leads": list(new_leads) + carried,
                "summary": {"new_leads": len(new_leads), "carried_unpooled": len(carried),
                            "matched_entities": n_matched}})
    return carried


def write_card(ws, name, card):
    path = os.path.join(ws, "out", name)
    core.jdump(path, card)
    return path


# ---------- 内参卡（需求文档版式：活动日历 / 行业重点新闻 / 榜单监控 / 潜在客户与业务情报） ----------

# 头图与图标 img_key 复用需求文档卡片 JSON 里的既有素材（飞书 image key 租户级通用）
BRIEFING_IMGS = {
    "banner": "img_v3_0212n_1afbec4c-f128-436a-8e46-3f4a29fcd41g",
    "icon": "img_v3_027m_91fb8fc2-e6a3-406c-aa9d-04569e29bccg",
}
LEAD_POOL_VIEW_URL = "https://webeye.feishu.cn/base/K1GrbusPBadZzws1DhjcPTionsf?table=tblIJG9zaVi1I7iM&view=vewHOTnMQa"


def _button(content, url, btype="primary", size="small"):
    return {"tag": "button", "text": {"tag": "plain_text", "content": content},
            "type": btype, "width": "default", "size": size,
            "behaviors": [{"type": "open_url", "default_url": url}]}


def _section_heading(text):
    return {"tag": "markdown", "content": "**%s**" % text, "text_size": "heading",
            "margin": "16px 0px 4px 0px"}


def _activity_elements(records, limit=3):
    """活动日历段：judged 里 type=活动情报 且带 activity 明细的记录。

    行内粗体闭合符后必须跟空格（`**时间：** 值`）：全角标点+紧贴正文会让
    markdown 粗体失效、星号原样显示（实测陷阱）。
    """
    els = []
    # significant 排序后再截断：high 活动不能被原序靠前的 normal 挤出日历
    # （2026-10-26 实测：high 物流展排在第 2 位侥幸入选，列表顺序一变就会漏）
    acts = [x for x in records if (x.get("activity") and not x.get("discard"))]
    acts.sort(key=lambda x: 0 if x.get("significance") == "high" else 1)
    for r in acts[:limit]:
        a = r["activity"]
        lines = ["**%s**" % (a.get("name") or r.get("title") or "未命名活动")]
        if a.get("time"):
            lines.append("**时间：** %s" % a["time"])
        if a.get("location"):
            lines.append("**地点：** %s" % a["location"])
        if a.get("highlight"):
            lines.append("**看点：** %s" % a["highlight"])
        if r.get("sources"):
            lines.append(_sources_line(r["sources"]))
        els.append({"tag": "markdown", "content": "\n".join(lines), "text_size": "notation"})
        if a.get("signup_url"):
            els.append(_button("👉 点我查看详情并报名", a["signup_url"]))
    return els


def _news_elements(records, limit=3):
    """行业重点新闻段：编号红字标题 + 摘要 + 原文按钮。"""
    els = []
    shown = sorted([r for r in records if not r.get("discard")],
                   key=lambda r: SIG_ORDER.get(r.get("significance"), 3))[:limit]
    for i, r in enumerate(shown, 1):
        els.append({"tag": "markdown",
                    "content": "<font color='red'>**%d. %s**</font>" % (i, r.get("title") or "")})
        if r.get("summary"):
            els.append({"tag": "markdown", "content": r["summary"], "text_size": "notation"})
        if r.get("sources"):
            els.append({"tag": "markdown", "content": _sources_line(r["sources"]),
                        "text_size": "notation"})
        if r.get("url"):
            els.append(_button("让我看看👀", r["url"]))
    return els


def _rank_elements(records, limit=4):
    """榜单监控段（需求文档「榜单监控」板块）：榜单新晋/黑马信号 + 背后公司画像结论。

    数据来源：judged 里 type 含「榜单」的情报记录（判断准则约定榜单新晋统一标
    type=「榜单新晋」）。「排除长久霸榜、只看新上榜」的语义在源侧差分（links 模式
    基线有本轮新出现 = added）与 AI 判断（rank_new_entry kind）两处已保证，这里只做展示。
    """
    els = []
    shown = [r for r in records if not r.get("discard") and "榜单" in (r.get("type") or "")]
    shown.sort(key=lambda r: SIG_ORDER.get(r.get("significance"), 3))
    for r in shown[:limit]:
        els.append({"tag": "markdown",
                    "content": "<font color='red'>**🏆 %s**</font>" % (r.get("title") or "")})
        if r.get("summary"):
            els.append({"tag": "markdown", "content": r["summary"], "text_size": "notation"})
        if r.get("sources"):
            els.append({"tag": "markdown", "content": _sources_line(r["sources"]),
                        "text_size": "notation"})
        if r.get("url"):
            els.append(_button("查看榜单原文", r["url"]))
    return els


def _lead_elements(leads, limit=3, view_url=None):
    """潜在客户段：公司名按钮 + 文档字段式描述 + 猎隼线索池登记按钮（record 级定位）。

    P0 优先、同优先级按时间倒序——新线索不许被旧线索占掉展示位。
    """
    els = []
    ranked = sorted(_active(leads), key=lambda l: (l.get("priority") != "P0",
                                                   core.parse_iso(l.get("logged_at")) or 0), reverse=True)
    for ld in ranked[:limit]:
        pri_label, _ = PRIORITY_LABEL.get(ld.get("priority") or "P1", PRIORITY_LABEL["P1"])
        lines = ["**%s ｜ %s**" % (ld.get("entity_name") or "未命名", pri_label)]
        if lead_exists(ld):
            lines.append("<font color='grey'>状态：此前已登记【猎隼线索池】，本次未重复写入</font>")
        lines.append("所属赛道：%s" % ("/".join(ld.get("industries") or []) or "待补充"))
        groups = ld.get("matched_group_names") or ld.get("matched_groups") or []
        if groups:
            lines.append("命中规则：%s" % "；".join(groups))
        if ld.get("summary"):
            lines.append(ld["summary"])
        if ld.get("profile"):
            lines.append("业务描述：%s" % ld["profile"])
        if ld.get("opportunity"):
            lines.append("**最大机会：%s**" % ld["opportunity"])
        if ld.get("sources"):
            lines.append(_sources_line(ld["sources"]))
        els.append(_button(ld.get("entity_name") or "查看线索", ld.get("url") or LEAD_POOL_VIEW_URL,
                           btype="danger_text", size="medium"))
        els.append({"tag": "markdown", "content": "\n\n".join(lines), "text_size": "notation"})
        pool_url = lead_pool_url(ld, view_url or LEAD_POOL_VIEW_URL)
        if ld.get("bitable_record_id"):
            btn = ("已在线索池·点此查看既有记录" if lead_exists(ld)
                   else "该信息已登记【猎隼线索池】，点此查看完整线索")
            els.append(_button(btn, pool_url, btype="danger_filled"))
        else:
            els.append(_button("查看【猎隼线索池】", pool_url, btype="danger_filled"))
    return els


def briefing_card(records, leads, view_url=None):
    """需求文档版式内参卡：活动日历 / 行业重点新闻 / 榜单监控（有数据才出现）/ 潜在客户与业务情报。
    全空返回 None（当天无内容不产空卡）。"""
    body = [{"tag": "img", "img_key": BRIEFING_IMGS["banner"], "preview": True,
             "transparent": False, "scale_type": "fit_horizontal",
             "alt": {"tag": "plain_text", "content": "市场团队内参"},
             "corner_radius": "10px"}]
    act_els = _activity_elements(records)
    if act_els:
        body.append(_section_heading("📅 WebEye活动日历"))
        body += act_els
    news_els = _news_elements([r for r in records if r.get("type") != "活动情报"
                               and "榜单" not in (r.get("type") or "")])
    if news_els:
        body.append({"tag": "hr"})
        body.append(_section_heading("🔥 行业重点新闻"))
        body += news_els
    rank_els = _rank_elements(records)
    if rank_els:
        body.append({"tag": "hr"})
        body.append(_section_heading("🏆 榜单监控｜海外软件榜单·新晋黑马"))
        body.append({"tag": "markdown",
                     "content": "监控海外软件榜单（AI产品榜、AppMagic 等）新上榜/登顶的黑马产品"
                                "——排除长久霸榜，只看新晋；并调研背后公司是否符合 WebEye 潜在客户画像。",
                     "text_size": "notation"})
        body += rank_els
    lead_els = _lead_elements(leads, view_url=view_url)
    if lead_els:
        body.append({"tag": "hr"})
        body.append(_section_heading("🤝 潜在客户与业务情报"))
        body += lead_els
    if len(body) <= 1:
        return None
    return {**CARD_CONFIG,
            "header": {"title": {"tag": "plain_text", "content": "⚡️ 市场团队内参：近期热点与活动简报"},
                       "subtitle": {"tag": "plain_text", "content": core.now_iso().replace("T", " ")[:16]},
                       "template": "indigo",
                       "icon": {"tag": "custom_icon", "img_key": BRIEFING_IMGS["icon"]}},
            "body": {"direction": "vertical", "padding": "12px 12px 20px 12px", "elements": body}}


# ---------- 主流程：judged.json 落盘 ----------

def absorb_discards(judged):
    """把顶层 `discarded` 键自动并入 intel 列表并返回 (intel, 是否并入)。

    构建脚本常把弃权条目单独放 judged["discarded"] 而忘并入 intel（实战踩坑两次），
    在代码层兜住：有这个键就自动吸收，不再依赖构建者记得手工合并。
    """
    discards = judged.get("discarded")
    if not isinstance(discards, list):
        return judged.get("intel") or [], False
    intel = list(judged.get("intel") or [])
    intel.extend(r for r in discards if isinstance(r, dict))
    return intel, True


def strip_writeback(intel):
    """复用登记的结构归一：剥离落盘回写字段 + 弃权记录转 judged 契约。

    洁净重置复用场景（陷阱 19）：从旧 log/intel.jsonl 原样拷回的记录会带
    落盘回写字段（logged_at/grounding/grounding_notes），会被字段白名单
    拦截退出码 1——复用是标准流程，每次必踩。已知回写字段自动剥离并提示；
    其余未知字段仍由 check_keys 点名拦截（防拼写错误静默失效）。

    另：旧 log/discarded.jsonl 的弃权记录是 {change_id, reason, logged_at}
    结构，与 judged 契约 {discard: true, discard_reason} 不一致——检测到
    含 reason 且未带 discard 键的记录自动归一（dry-run 实测拦截后代码化）。
    返回剥离到的回写字段集合。
    """
    writeback = {"logged_at", "grounding", "grounding_notes"}
    # suppressed_by_guard 不剥离：它是"已通报"的正式契约字段（INTEL_KEYS 成员），
    # 从旧登记复用时要原样继承给 digest 过滤。
    stripped = set()
    for r in intel:
        extra = writeback & set(r)
        if extra:
            stripped |= extra
            for k in extra:
                del r[k]
        if "reason" in r and not r.get("discard") and "discard_reason" not in r:
            r["discard"] = True
            r["discard_reason"] = r.pop("reason")
    return stripped


def demote_already_sent(intel, sent_ids):
    """对已发登记中存在的情报强制 notify=False，返回 (处理后的 intel, 降级条数)。

    洁净重置场景（清空 log/intel.jsonl 验证"新环境"）下，历史已发情报会被当新条目
    重新通知轰炸（陷阱 19 靠人工标注 notify=false，实战负担重且易漏）。
    传入旧 intel.jsonl 的 change_id 集合，代码层强制降级，杜绝重复轰炸。
    """
    if not sent_ids:
        return intel, 0
    demoted = 0
    for r in intel:
        if r.get("discard"):
            continue
        if r.get("change_id") in sent_ids:
            # 命中即打标（无论 notify 当前值——上一轮已压成 false 的记录同样
            # 是"已通报"，不打标会让 digest 在洁净重置全量窗旧闻重推）；
            # notify 仍为 true 的同时压成 false，防实时通知重复。
            r["suppressed_by_guard"] = True
            if r.get("notify"):
                r["notify"] = False
                demoted += 1
    return intel, demoted


def _dry_run_input(ws, input_path, already_sent=None):
    """沙箱预检：把工作区完整复制到临时目录后执行真实 --input 流程，真实工作区零写入。

    洁净验证每轮都是新场景，历史上多次出现「落盘时才暴露」的问题（字段白名单、
    覆盖度缺口、grounding 拦截）。dry-run 把这些问题提前到落盘前暴露：
    预检通过再去掉 --dry-run 正式执行，失败则不改任何真实文件（兜底纪律）。
    """
    import shutil
    import tempfile
    tmp = tempfile.mkdtemp(prefix="market-intel-dryrun-")
    try:
        for name in os.listdir(ws):
            if name.startswith("."):
                continue
            src = os.path.join(ws, name)
            dst = os.path.join(tmp, name)
            if os.path.isdir(src):
                shutil.copytree(src, dst)
            else:
                shutil.copy2(src, dst)
        rc = cmd_input(tmp, input_path, already_sent=already_sent)
        sys.stderr.write("[dry-run] 沙箱预检通过（真实工作区零写入）——去掉 --dry-run 正式执行\n")
        return rc
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def cmd_input(ws, input_path, already_sent=None, dry_run=False):
    if dry_run:
        return _dry_run_input(ws, input_path, already_sent=already_sent)
    now = core.now_iso()
    latest = core.jload(os.path.join(ws, "out", "latest.json"), None)
    judged = core.jload(input_path, None)
    if not judged:
        raise SystemExit("[error] 读不到判断文件: %s" % input_path)
    intel, absorbed = absorb_discards(judged)
    if absorbed:
        sys.stderr.write("[absorb] 检测到顶层 discarded 键（%d 条弃权），已自动并入 intel——"
                         "下次构建可直接只填 intel 列表\n" % len(judged.get("discarded") or []))
    if already_sent:
        sent_path = os.path.expanduser(already_sent)
        sent_ids = set()
        for r in core.read_jsonl(sent_path) if os.path.exists(sent_path) else []:
            if r.get("discard"):
                continue
            sent_ids.add(r.get("change_id"))
            # 被合并条目也属已发：合并报道换源重现时（如 Anthropic IPO 的 36氪/
            # 钛媒体版本在主条滚窗后首次差分入集），不加进来会重复通知轰炸
            for mid in r.get("merged_change_ids") or []:
                sent_ids.add(mid)
        intel, demoted = demote_already_sent(intel, sent_ids)
        if demoted:
            sys.stderr.write("[sent-guard] %d 条情报已在旧登记中（洁净重置防重发），"
                             "强制 notify=false\n" % demoted)
    fact_records = judged.get("facts") or []
    stripped = strip_writeback(intel)
    if stripped:
        sys.stderr.write("[reuse] 已从 intel 记录剥离落盘回写字段: %s（复用旧登记时属预期）\n"
                         % ", ".join(sorted(stripped)))
    for r in intel:
        core.check_keys(r, INTEL_KEYS, "intel record")

    # 1) 覆盖度校验：漏答一条 = 判断不完整（latest.json 缺失时显式告警，不做静默全过）
    if latest is None:
        sys.stderr.write("[warn] out/latest.json 缺失，本轮不做覆盖度校验（手工补录场景）\n")
        missing = []
    else:
        missing = coverage_check(latest, intel, fact_records)
    if missing:
        sys.stderr.write("[coverage] 以下 change_id 未判（补全后重跑，pending 已保留）:\n  %s\n"
                         % "\n  ".join(missing))
        raise SystemExit(2)
    changes_by_id = {c["change_id"]: c for c in ((latest or {}).get("changes") or [])}

    entities = facts_mod.load_entities(ws)
    rules = match_mod.load_rules(ws)
    notify_cfg = load_notify(ws)

    # 2) 情报流水（intel + discard 分流落盘；按 change_id 幂等——修复后重跑不产生重复记录）
    seen_intel = {r.get("change_id") for r in
                  core.read_jsonl(os.path.join(ws, "log", "intel.jsonl"))}
    seen_discard = {r.get("change_id") for r in
                    core.read_jsonl(os.path.join(ws, "log", "discarded.jsonl"))}
    notified_intel = []
    for r in intel:
        if r.get("discard"):
            if r.get("change_id") in seen_discard:
                continue
            seen_discard.add(r.get("change_id"))
            core.append_jsonl(os.path.join(ws, "log", "discarded.jsonl"),
                              {"change_id": r.get("change_id"), "reason": r.get("discard_reason") or "未说明",
                               "logged_at": now})
            continue
        if r.get("change_id") in seen_intel:
            continue
        seen_intel.add(r.get("change_id"))
        rec = {k: r.get(k) for k in INTEL_KEYS if k in r and not k.startswith("_")}
        rec["logged_at"] = now
        ch = changes_by_id.get(r.get("change_id"))
        nums, grounded_notes = grounding_check(r, ch)
        if grounded_notes:
            rec["grounding_notes"] = grounded_notes  # 换算备注留档，供人工审计
        if nums:
            rec["grounding"] = "unverified"
            sys.stderr.write("[grounding] change %s 的说明含原文没有的数字: %s（请回原文核实，"
                             "若为单位/汇率换算可在 number_notes 申报）\n"
                             % (r.get("change_id"), ",".join(nums)))
        rec.setdefault("significance", "unknown")
        if rec["significance"] == "unknown":
            rec["notify"] = False  # 没判断过的东西不许发（脚本强制）
        core.append_jsonl(os.path.join(ws, "log", "intel.jsonl"), rec)
        if rec.get("notify"):
            notified_intel.append(rec)

    # 3) 事实抽取 -> 企业档案（含 web_page 招聘信号的确定性直录）
    touched = set()
    for f in fact_records:
        eid = facts_mod.apply_fact(ws, f, entities, now)
        touched.add(eid)
    for c in (latest or {}).get("changes") or []:
        if (c.get("source_kind") == "web_page" and c.get("change_kind") == "added"
                and c.get("owner_entity") and str(c.get("key", "")).startswith("kw:")):
            kw = str(c["key"])[3:]
            apply_hiring_signal(ws, c["owner_entity"], entities, kw, c.get("url"), now)
            touched.add(c["owner_entity"])

    # 4) 规则匹配 -> 线索（对被触及的企业全量重算；确定性、可回测）
    #    分级口径（需求文档）：任一 tier=P0 组命中即 P0（市场优先建联，实时通知）；
    #    仅 P1 组命中为 P1（入池+日报，不实时打扰，notify_p1 可放开）。
    new_leads = []
    existing_keys = {l.get("dedupe_key") for l in core.read_jsonl(os.path.join(ws, "log", "leads.jsonl"))}
    threshold = match_mod.threshold(rules)
    notify_p1 = bool((rules.get("scoring") or {}).get("notify_p1", False))
    for eid in sorted(touched):
        fact = facts_mod.load_fact(ws, eid)
        if not fact:
            continue
        result = match_mod.evaluate_entity(fact, rules)
        if not result["matched_groups"]:
            continue
        dedupe = core.sha1(eid + "|" + ",".join(sorted(result["matched_groups"])))
        priority = result.get("priority") or "P1"
        lead = {
            "lead_id": core.sha1(eid + "|" + now)[:12],
            "dedupe_key": dedupe,
            "entity_id": eid,
            "entity_name": fact.get("name") or eid,
            "industries": fact.get("industries") or [],
            "priority": priority,
            "matched_groups": result["matched_groups"],
            "matched_group_names": result.get("matched_group_names") or result["matched_groups"],
            "score": result["score"],
            "evidence": result["evidence"],
            "summary": lead_summary(fact),
            "profile": fact.get("profile"),
            "opportunity": fact.get("opportunity"),
            "url": lead_source_url(fact),
            "sources": lead_sources(fact),
            "notify": result["score"] >= threshold and (priority == "P0" or notify_p1),
            "status": "new",
            "logged_at": now,
        }
        recent_cut = time.time() - 7 * 86400
        dup = dedupe in existing_keys and any(
            (core.parse_iso(l.get("logged_at")) or 0) > recent_cut
            for l in core.read_jsonl(os.path.join(ws, "log", "leads.jsonl"))
            if l.get("dedupe_key") == dedupe)
        if dup:
            continue  # 7 天内已报过同组合线索：不重复打扰
        core.append_jsonl(os.path.join(ws, "log", "leads.jsonl"), lead)
        existing_keys.add(dedupe)
        if lead["notify"]:
            new_leads.append(lead)

    save_leads_latest(ws, new_leads, now, touched)

    # 5) 产卡（脚本产卡、直接发送；群卡仅高分/高优时出现）
    view_url = (notify_cfg.get("bitable") or {}).get("view_url")
    cards = []
    if notified_intel:
        blocks, total = intel_blocks(notified_intel)
        tags = [("%d 条动态" % total, "violet")]
        if any(r.get("significance") == "high" for r in notified_intel):
            tags.append(("含重要动态", "red"))
        card = build_card("市场情报简报", core.now_iso().replace("T", " ")[:16] + " · 本轮判定",
                          blocks + [{"tag": "markdown", "text_size": "notation",
                                     "content": "<font color='grey'>明细已归档，可按时间线 / 类型 / 对象检索。</font>"}],
                          template="violet", tags=tags, icon={**INTEL_ICON, "color": "violet"})
        cards.append(write_card(ws, "card-intel-%s.json" % stamp(), card))
        if any(r.get("significance") == "high" for r in notified_intel):
            # 群卡：重要动态 @ 提醒（notify.mention 驱动，默认 @所有人）
            gels = ([e] if (e := _mention_lead("⚠️ 含重要动态，请关注", notify_cfg.get("notify"))) else []) + blocks
            gcard = build_card("市场情报简报 · 重要", core.now_iso().replace("T", " ")[:16],
                               gels, template="red", tags=tags, icon={**INTEL_ICON, "color": "red"})
            cards.append(write_card(ws, "card-intel-%s-group.json" % stamp(), gcard))
    if new_leads:
        p0_leads = [l for l in new_leads if l.get("priority") == "P0"]
        blocks = lead_blocks(new_leads, view_url=view_url)
        card = build_card("潜在客户线索", core.now_iso().replace("T", " ")[:16] + " · 规则匹配命中",
                          blocks + [{"tag": "markdown",
                                     "content": "<font color='grey'>P0=优先建联；P1=储备线索。"
                                                "命中线索将自动登记猎隼线索池（人工事后核查）；"
                                                "发送以 --lead-card 重产版为准。</font>",
                                     "text_size": "notation"}],
                          template="red" if p0_leads else "green",
                          tags=[("P0×%d" % len(p0_leads), "red")] if p0_leads else [("%d 条线索" % len(new_leads), "green")])
        cards.append(write_card(ws, "card-leads-%s.json" % stamp(), card))
        if p0_leads:
            # 群卡：P0 线索 @ 提醒（notify.mention 驱动，默认 @所有人）
            gblocks = lead_blocks(p0_leads, view_url=view_url)
            gels = ([e] if (e := _mention_lead("⚠️ P0 线索请尽快建联", notify_cfg.get("notify"))) else []) + gblocks
            gcard = build_card("潜在客户线索 · P0 优先建联", core.now_iso().replace("T", " ")[:16],
                               gels, template="red",
                               tags=[("%d 条 P0 线索，请尽快跟进" % len(p0_leads), "red")])
            cards.append(write_card(ws, "card-leads-%s-group.json" % stamp(), gcard))

    # 6) 核销待判队列
    judged_ids = set()
    for r in intel:
        if r.get("change_id"):
            judged_ids.add(r["change_id"])
        for mid in r.get("merged_change_ids") or []:
            judged_ids.add(mid)
    for f in fact_records:
        for cid in f.get("change_ids") or []:
            judged_ids.add(cid)
    kept = diffcore.save_pending(ws, (latest or {}).get("changes") or [], judged_ids, now)

    print("判定落盘完成：情报 %d 条（通知 %d）、事实 %d 条、新线索 %d 条、待判遗留 %d"
          % (len([r for r in intel if not r.get("discard")]), len(notified_intel),
             len(fact_records), len(new_leads), kept))
    for p in cards:
        print("卡片: %s" % p)
    return 0


def apply_hiring_signal(ws, owner_entity, entities, keyword, page_url, now):
    """web_page 招聘关键词的确定性直录：不经模型，直接入档案（owner_entity 指明归属）。"""
    eid, _ = facts_mod.normalize_entity(owner_entity, entities)
    fact = facts_mod.load_fact(ws, eid) or {
        "entity_id": eid, "name": entities.get(owner_entity, {}).get("name", owner_entity),
        "aliases": [], "industries": [], "funding_events": [], "hiring_signals": [], "other_events": []}
    for hs in fact.setdefault("hiring_signals", []):
        if core.norm_url(hs.get("page_url") or "") == core.norm_url(page_url or ""):
            if keyword not in (hs.get("keywords") or []):
                hs["keywords"] = sorted((hs.get("keywords") or []) + [keyword])
            hs["last_seen"] = now
            core.jdump(facts_mod.fact_path(ws, eid), fact)
            return
    fact["hiring_signals"].append({"keywords": [keyword], "page_url": page_url,
                                   "first_seen": now, "last_seen": now})
    fact["updated_at"] = now
    core.jdump(facts_mod.fact_path(ws, eid), fact)


def lead_summary(fact):
    fe = (fact.get("funding_events") or [{}])[-1]
    hs = fact.get("hiring_signals") or []
    parts = []
    if fe:
        parts.append("近期融资：%s %s" % (fe.get("round") or "", fe.get("amount_text") or "金额未披露"))
    if hs:
        kws = sorted({k for h in hs for k in (h.get("keywords") or [])})
        if kws:
            parts.append("在招岗位关键词：%s" % "/".join(kws[:6]))
    return "；".join(parts) or "命中线索规则"


def lead_sources(fact):
    out = []
    for fe in fact.get("funding_events") or []:
        if fe.get("source_url"):
            out.append({"name": fe.get("source_name") or "融资报道", "url": fe["source_url"]})
    for oe in fact.get("other_events") or []:
        if oe.get("source_url"):
            out.append({"name": oe.get("source_name") or "动态报道", "url": oe["source_url"]})
    for hs in fact.get("hiring_signals") or []:
        if hs.get("page_url"):
            out.append({"name": "招聘页", "url": hs["page_url"]})
    return out[:3]


def lead_source_url(fact):
    src = lead_sources(fact)
    return src[0]["url"] if src else None


def stamp():
    return time.strftime("%m%d-%H%M")


def cmd_lead_card(ws):
    """猎隼池写入后重产最近一轮线索卡（私信卡 + 群卡）。

    运行序：judge.py --input → --bitable --commit → --lead-card（发送这版）。
    必须在 commit 之后跑：此时 bitable_status 已回写，exists（此前已入池）的线索
    在卡上中性标注「已存在」、绝不进群 @——群卡只催全新 P0 建联，重复轰炸=骚扰。
    群卡也由本命令产出（--input 阶段的群卡是写入前快照，以本命令的最新版为准）。
    """
    latest = core.jload(os.path.join(ws, "out", "leads-latest.json"), None)
    run_ids = [l.get("lead_id") for l in (latest or {}).get("leads") or []]
    if not run_ids:
        raise SystemExit("[error] 最近一轮没有新线索（out/leads-latest.json 为空），无需重发")
    by_id = {l.get("lead_id"): l for l in core.read_jsonl(os.path.join(ws, "log", "leads.jsonl"))
             if l.get("lead_id") in set(run_ids)}
    leads = [by_id[i] for i in run_ids if i in by_id]
    if not leads:
        raise SystemExit("[error] leads.jsonl 里找不到最近一轮的线索，疑被手工清理")
    # 字段实时兜底：旧版落盘线索可能缺 sources/profile/opportunity（如 other_event
    # 命中的、或调研补全晚于落盘的），从企业档案与 enrichments 契约补齐，
    # 保证卡片信息面完整、来源可溯源。
    import bitable as bitable_mod
    enrichments = bitable_mod.load_enrichments(ws)
    for ld in leads:
        if not ld.get("sources"):
            fact = facts_mod.load_fact(ws, ld.get("entity_id")) or {}
            fresh = lead_sources(fact)
            if fresh:
                ld["sources"] = fresh
                ld.setdefault("url", fresh[0]["url"])
        enr = enrichments.get(ld.get("entity_id")) or {}
        for k in ("profile", "opportunity"):
            if not ld.get(k) and enr.get(k):
                ld[k] = enr[k]
    notify_cfg = load_notify(ws)
    view_url = (notify_cfg.get("bitable") or {}).get("view_url")
    fresh, exists_leads = lead_card_split(leads)
    p0_leads = [l for l in fresh if l.get("priority") == "P0"]
    blocks = lead_blocks(leads, view_url=view_url)
    note = "已登记猎隼线索池，点「查看线索」直达对应记录。"
    if exists_leads:
        note += ("标注「已存在」的 %d 条为此前已登记（本次未重复写入），"
                 "不再群@建联。" % len(exists_leads))
    blocks.append({"tag": "markdown",
                   "content": "<font color='grey'>%s</font>" % note,
                   "text_size": "notation"})
    card = build_card("潜在客户线索", core.now_iso().replace("T", " ")[:16] + " · 已入池",
                      blocks, template="red" if p0_leads else "green",
                      tags=[("P0×%d" % len(p0_leads), "red")] if p0_leads
                      else [("%d 条线索" % len(leads), "green")])
    path = write_card(ws, "card-leads-%s.json" % stamp(), card)
    gpath = None
    if p0_leads:
        # 群卡：仅全新 P0（exists 是重复提示，绝不 @所有人 催建联）
        gblocks = lead_blocks(p0_leads, view_url=view_url)
        gels = ([e] if (e := _mention_lead("⚠️ P0 线索请尽快建联", notify_cfg.get("notify"))) else []) + gblocks
        gcard = build_card("潜在客户线索 · P0 优先建联", core.now_iso().replace("T", " ")[:16],
                           gels, template="red",
                           tags=[("%d 条 P0 线索，请尽快跟进" % len(p0_leads), "red")])
        gpath = write_card(ws, "card-leads-%s-group.json" % stamp(), gcard)
    located = sum(1 for l in leads if l.get("bitable_record_id"))
    print("线索卡已重产: %s（%d 条中 %d 条带记录级定位；全新 %d 条、已存在标注 %d 条%s）"
          % (path, len(leads), located, len(fresh), len(exists_leads),
             ("；群卡: %s" % gpath) if gpath else "；无全新 P0，本轮不产群卡"))
    return 0


# ---------- 每日摘要（delta 锚点式，沿用 change-watch 的宁重勿漏） ----------

def cmd_digest(ws, commit=False):
    now = core.now_iso()
    anchor_rec = core.jload(os.path.join(ws, "state", "digest_anchor.json"), {"anchor": None})
    anchor = anchor_rec.get("anchor")
    records = [r for r in core.read_jsonl(os.path.join(ws, "log", "intel.jsonl"))
               if not anchor or (core.parse_iso(r.get("logged_at")) or 0) > (core.parse_iso(anchor) or 0)]
    # 已通报压制（sent-guard 标记）的内容不进内参：洁净重置全量窗复判时，
    # 旧闻会被重新打包重复推送（2026-10-27 实测拦截后代码化）；AI 判定
    # notify=false 的记录不在此列——内参保留其归档展示语义。
    records = [r for r in records if not r.get("suppressed_by_guard")]
    leads = [l for l in core.read_jsonl(os.path.join(ws, "log", "leads.jsonl"))
             if not anchor or (core.parse_iso(l.get("logged_at")) or 0) > (core.parse_iso(anchor) or 0)]
    if commit:
        # 锚点必须同时覆盖情报与线索的 logged_at：只看 intel 会把更晚落盘的线索
        # 留在窗口里，下期日报重复展示已发过的线索（宁重勿漏只对情报成立，线索有去重键）。
        stamps = [r.get("logged_at") for r in core.read_jsonl(os.path.join(ws, "log", "intel.jsonl"))] \
            + [l.get("logged_at") for l in core.read_jsonl(os.path.join(ws, "log", "leads.jsonl"))]
        new_anchor = max([a for a in stamps if a] + [anchor or ""])
        core.jdump(os.path.join(ws, "state", "digest_anchor.json"), {"anchor": new_anchor, "committed_at": now})
        print("摘要锚点已推进到: %s" % new_anchor)
        return 0
    by_type = {}
    for r in records:
        by_type[r.get("type") or "未分类"] = by_type.get(r.get("type") or "未分类", 0) + 1
    # 正式版式：需求文档三段式内参卡；三段全空或渲染异常时降级 simple 版式（宁重勿漏）
    path = None
    view_url = ((load_notify(ws).get("bitable") or {}).get("view_url")) or LEAD_POOL_VIEW_URL
    try:
        card = briefing_card(records, leads, view_url=view_url)
        if card is not None:
            path = write_card(ws, "card-daily-%s.json" % stamp(), card)
    except Exception as exc:  # noqa: BLE001
        sys.stderr.write("[fallback] 内参卡渲染失败，降级 simple 版式: %s\n" % exc)
    if path is None:
        if not records and not leads:
            # 全空不产空卡（与 SKILL.md 纪律一致）：briefing 版式三段全空返回
            # None 后，simple 降级分支也不得产出"0 条动态"占位卡
            print("窗口内无增量（情报 0、线索 0），不产空卡；如需推进锚点加 --commit")
            return 0
        blocks = [_block(["**本时段市场情报 %d 条**，新线索 %d 条" % (len(records), len(leads)),
                          "、".join("%s %d" % (k, v) for k, v in sorted(by_type.items(), key=lambda x: -x[1])) or "无"],
                         color="violet")]
        blocks += intel_blocks(sorted(records, key=lambda r: SIG_ORDER.get(r.get("significance"), 3))[:4])[0]
        blocks.append({"tag": "markdown",
                       "content": "<font color='grey'>窗口起点：%s</font>" % (anchor or "首次运行"),
                       "text_size": "notation"})
        card = build_card("市场情报日报", core.now_iso().replace("T", " ")[:16], blocks,
                          template="violet",
                          tags=[("%d 条动态" % len(records), "violet"), ("%d 条新线索" % len(leads), "green")],
                          icon={**INTEL_ICON, "color": "violet"})
        path = write_card(ws, "card-digest-%s.json" % stamp(), card)
    print("摘要卡已产出: %s（发送成功后请跑 --digest --commit 推进锚点）" % path)
    return 0


# ---------- 状态简报 ----------

def cmd_status(ws):
    latest = core.jload(os.path.join(ws, "out", "latest.json"), None)
    if not latest:
        raise SystemExit("[error] 尚无扫描结果（先跑 pipeline.py --run）")
    health = latest.get("health", {})
    down = [k for k, v in health.items() if v.get("status") == "down"]
    stale = latest.get("summary", {}).get("stale_sources") or []
    lines = ["**扫描状态**", "来源 %d 个 · 失败 %d · 停更 %d" % (
        latest.get("summary", {}).get("sources_total", 0), len(down), len(stale))]
    if down:
        lines.append("不可用：%s" % "、".join(sorted(down)))
    if stale:
        lines.append("停更：%s" % "、".join(s.get("label") or s.get("id") for s in stale if isinstance(s, dict)))
    card = build_card("市场情报 · 扫描状态", core.now_iso().replace("T", " ")[:16],
                      [_block(lines, color="yellow" if (down or stale) else "green")],
                      template="yellow" if (down or stale) else "green",
                      tags=[("正常", "green") if not (down or stale) else ("有异常", "yellow")])
    path = write_card(ws, "card-status-%s.json" % stamp(), card)
    print("状态卡已产出: %s" % path)
    return 0


def main():
    ap = argparse.ArgumentParser(description="market-intel 判断落盘与线索产出")
    ap.add_argument("--workspace")
    ap.add_argument("--input", help="judged.json 路径")
    ap.add_argument("--already-sent",
                    help="旧 intel.jsonl 路径（洁净重置防重发）：命中的已发情报强制 notify=false")
    ap.add_argument("--dry-run", action="store_true",
                    help="沙箱预检：临时副本上完整演练 --input 流程并报告问题，真实工作区零写入")
    ap.add_argument("--digest", action="store_true", help="产出每日摘要卡（delta 窗口）")
    ap.add_argument("--commit", action="store_true", help="与 --digest 连用：推进摘要锚点")
    ap.add_argument("--status-card", action="store_true", help="产出扫描状态简报卡")
    ap.add_argument("--research-check", action="store_true",
                    help="调研完整度审计：逐企业核对 14 必研字段，未达标退出码 2")
    ap.add_argument("--bitable", action="store_true",
                    help="把 status=new 的线索构建为猎隼线索池待写记录（预览）；加 --commit 才真正写入")
    ap.add_argument("--lead-card", action="store_true",
                    help="猎隼池写入后重产最近一轮线索卡（「查看线索」直达记录）")
    args = ap.parse_args()
    ws = core.workspace(args.workspace)
    if args.lead_card:
        raise SystemExit(cmd_lead_card(ws))
    if args.research_check:
        import bitable
        raise SystemExit(2 if bitable.audit_enrichments(ws) else 0)
    if args.bitable:
        import bitable
        cfg = (core.jload(os.path.join(ws, "notify.json"), {}) or {}).get("bitable") or {}
        if args.commit:
            # 自动入池模式：先重建预览吸收最新调研（离线），再直接写入。
            # 不再要求「人工确认预览」断点——调研 14 必研字段硬门槛 + 去重 +
            # 选项校验 + blocked 留痕就是安全边界，人工事后在表内核查修正。
            items = bitable.build_pending(ws, cfg)
            if not items:
                print("没有待写线索（leads.jsonl 中 status=new 的已全部处理）。")
                raise SystemExit(0)
            pushed, exists, failed, blocked = bitable.commit(ws, cfg)
            print("猎隼线索池写入完成：新增 %d、已存在跳过 %d、失败 %d、调研未齐全拦截 %d"
                  % (pushed, exists, failed, blocked))
            print("表格视图: %s" % (cfg.get("view_url") or "(notify.json bitable.view_url 未配置)"))
            if blocked:
                sys.stderr.write("[bitable] 有线索因调研未齐全被拦截（先补调研再重跑本命令，"
                                 "审计命令：--research-check）\n")
            raise SystemExit(0 if (failed == 0 and blocked == 0) else (4 if blocked else 3))
        items = bitable.build_pending(ws, cfg)
        if not items:
            print("没有待写线索（leads.jsonl 中 status=new 的已全部处理）。")
            raise SystemExit(0)
        for it in items:
            print("- %s | %s" % (it["company"], json.dumps(it["record"], ensure_ascii=False)[:260]))
        print("预览已写入 %s（共 %d 条）。确认无误后加 --commit 真正写入。" % (bitable.pending_path(ws), len(items)))
        raise SystemExit(0)
    if args.digest:
        raise SystemExit(cmd_digest(ws, commit=args.commit))
    if args.status_card:
        raise SystemExit(cmd_status(ws))
    if args.input:
        raise SystemExit(cmd_input(ws, args.input, already_sent=args.already_sent,
                                   dry_run=args.dry_run))
    ap.print_help()


if __name__ == "__main__":
    main()
