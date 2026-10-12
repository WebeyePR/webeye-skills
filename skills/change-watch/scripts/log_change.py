#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
change-watch :: 记录判断结果

上层（AI）读完变更集、按 references/criteria.md 逐条判断后，把结论交给本脚本落盘。
本脚本只做确定性的事：字段校验、按去重键跳过重复、追加写入、生成待发送通知草稿
与**可直接发送的飞书卡片 JSON**（中文标签在代码里定死，不靠模型每次记得翻译）。

用法：
  log_change.py --input judged.json            # judged.json 见下
  log_change.py --input judged.json --no-notify
  log_change.py --show                         # 打印最近 20 条

judged.json 形如：
  { "changes": [
      { "target": "google", "change_kind": "added", "type": "模型发布",
        "significance": "high", "object": "gemini-3.5-flash",
        "title": "…", "summary": "…", "url": "https://…",
        "sources": [{"name": "Google 官方博客", "url": "https://…"}],
        "notify": true, "notify_reason": "…" }
  ] }

必填：target / change_kind / type / significance / title / summary / url
sources 可以给字符串（自动按配置映射中文名）或 {"name","url"}（推荐，名字最准）。

退出码：0 正常；1 输入格式/致命错误；2 本轮判断不完整或含非法记录（覆盖度缺失
        / 判断记录格式非法，pending 已保留、下轮 watch 会再带回问一次）——自动化统一
        按"非 0 但不中止"处理。
"""
import argparse
import contextlib
import datetime
import email.utils
import hashlib
import json
import os
import re
import sys

try:
    import fcntl  # Unix 排他锁；缺失则降级为无锁（仍正确，仅无互斥）
except ImportError:  # 非 Unix 平台
    fcntl = None

SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_WS = os.path.join(os.path.expanduser("~"), ".change-watch")
REQUIRED = ("target", "change_kind", "type", "significance", "title", "summary", "url")
ALLOWED_KINDS = ("added", "removed", "changed")
# unknown：材料不足、判不出来。它**一定会落盘**（不丢），但**一定不通知**（没判断过就不许发）。

# 所有对外展示的时间统一为北京时间（东八区），不依赖本机时区设置。
BEIJING = datetime.timezone(datetime.timedelta(hours=8))


def beijing_now_str():
    """北京（东八区）当前时间字符串，格式 %Y-%m-%d %H:%M。"""
    return datetime.datetime.now(BEIJING).strftime("%Y-%m-%d %H:%M")
ALLOWED_SIG = ("high", "normal", "low", "unknown")

# 机器值 → 卡片上的人话（映射写在代码里，避免每次靠模型"记得翻译"）
SIG_STYLE = {"high": ("重要", "red"), "normal": ("常规", "blue"), "low": ("次要", "neutral"),
             "unknown": ("待确认", "neutral")}
KIND_STYLE = {"removed": ("已下架", "red"), "changed": ("有变化", "yellow")}  # added 不加标签
SIG_ORDER = {"high": 0, "normal": 1, "low": 2, "unknown": 3}


def criteria_version():
    """判断准则的内容哈希，写进每条记录 —— 事后能回答"这条是哪版口味判的"。"""
    try:
        with open(os.path.join(SKILL_DIR, "references", "criteria.md"), "rb") as fh:
            return hashlib.sha1(fh.read()).hexdigest()[:10]
    except OSError:
        return None


# ---------- 证据可查：模型写的关键记号必须能在原文里找到 ----------

# 只查三类最容易凭空出现的东西：版本号、日期、价格。
_GROUND_PATTERNS = (r"\d+\.\d+(?:\.\d+)*", r"\d{4}-\d{2}-\d{2}", r"(?<![A-Za-z])\$\s?\d[\d.,]*")


def _loose(s):
    return re.sub(r"[\s\-_/]", "", str(s)).lower()


def ungrounded_tokens(text, source_text):
    """返回 text 里出现、但 source_text 里找不到的记号。"""
    hay = _loose(source_text)
    if not hay:
        return []
    bad = []
    for pat in _GROUND_PATTERNS:
        for m in re.findall(pat, text or ""):
            if _loose(m) in hay:
                continue
            # 同一处可能被两个模式命中（$99.00 与 99.00），只留更长的那个
            bad = [b for b in bad if b not in m]
            if m not in bad:
                bad.append(m)
    return bad


def source_text_by_id(ws):
    """从最近一次抓取结果里取每条变更的原文文本，按 change_id 索引。"""
    m = {}
    try:
        with open(os.path.join(ws, "out", "latest.json"), "r", encoding="utf-8") as fh:
            latest = json.load(fh)
    except (OSError, ValueError):
        return m
    for c in latest.get("changes", []):
        parts = [c.get("title") or ""]
        for side in ("curr", "prev"):
            rec = c.get(side) or {}
            parts.append(rec.get("summary") or rec.get("text") or
                         " | ".join(rec.get("cells") or []))
        m[c.get("change_id")] = " ".join(str(x) for x in parts if x)
    return m


def target_display_name(ws, target_id):
    """取目标的中文显示名：优先 short_name（卡片标题用，短），其次 name，最后 id。"""
    try:
        with open(os.path.join(ws, "targets.json"), "r", encoding="utf-8") as fh:
            cfg = json.load(fh)
    except (OSError, ValueError):
        return target_id
    for t in cfg.get("targets", []):
        if t.get("id") == target_id:
            return t.get("short_name") or t.get("name") or target_id
    return target_id


def source_label_map(ws):
    """配置里 源URL → 中文 label，用于给卡片的来源行取中文名。"""
    m = {}
    try:
        with open(os.path.join(ws, "targets.json"), "r", encoding="utf-8") as fh:
            cfg = json.load(fh)
    except (OSError, ValueError):
        return m
    for t in cfg.get("targets", []):
        for spec in t.get("streams", []) + t.get("registries", []):
            if spec.get("url"):
                m[spec["url"]] = spec.get("label") or spec.get("id")
    return m


def notify_config(ws):
    """读取 targets.json 顶层的 notify 块（群聊强提醒配置）。读不到返回 {}。"""
    try:
        with open(os.path.join(ws, "targets.json"), "r", encoding="utf-8") as fh:
            cfg = json.load(fh)
    except (OSError, ValueError):
        return {}
    n = cfg.get("notify") or {}
    return n if isinstance(n, dict) else {}


def _mention_md(mention):
    """把 notify.mention 渲染成卡片 markdown 里的 <at> 片段。

    - "all"                       -> <at id=all></at>（需群主开 @所有人 权限）
    - ["ou_1","ou_2"]             -> <at id=ou_1></at> <at id=ou_2></at>
    - 其它 / 空                   -> ""
    """
    if not mention:
        return ""
    if mention == "all":
        return "<at id=all></at>"
    if isinstance(mention, list):
        parts = ["<at id=%s></at>" % str(x).strip() for x in mention if str(x).strip()]
        return " ".join(parts)
    return ""


def _mentionify(card, mention, condition=True, label="⚠️ 重要变更，请关注"):
    """给任意卡片字典就地加 @ 横幅并染红头（群聊强提醒）。返回同一张卡片。"""
    if not (mention and condition):
        return card
    mnt = _mention_md(mention)
    if not mnt:
        return card
    body = card.setdefault("body", {})
    body.setdefault("elements", []).insert(0, {"tag": "markdown",
                                               "content": "%s %s" % (label, mnt)})
    card.setdefault("header", {})["template"] = "red"
    return card


_FEED_TAILS = ("/rss", "/rss.xml", "/feed.xml", "/feed", "/feeds/posts/default",
               "?pagetemplate=rss")


def label_for(url, label_map):
    """给一个 URL 取中文来源名：先精确匹配配置，再按路径前缀匹配，最后退化成域名。"""
    if url in label_map:
        return label_map[url]
    best, best_len = None, -1
    for surl, lab in label_map.items():
        base = surl.split("?")[0].rstrip("/")
        for tail in _FEED_TAILS:
            if base.endswith(tail):
                base = base[: -len(tail)]
        if base and url.startswith(base) and len(base) > best_len:
            best, best_len = lab, len(base)
    if best:
        return best
    return url.split("//")[-1].split("/")[0]


def source_pairs(rec, label_map):
    """返回 [(中文名, URL)]；名称优先取判断时给的名字，其次按配置映射，最后退化成域名。

    防御：记录可能缺 sources 也缺 url（旧版/异常落盘），此时退化成空列表而非崩溃，
    保证摘要/通知卡在任意单条脏数据下不整体失败。
    """
    out = []
    srcs = rec.get("sources")
    if not srcs:
        u = rec.get("url")
        srcs = [u] if u else []
    for s in srcs:
        if isinstance(s, dict):
            u = s.get("url", "")
            if u:
                out.append((s.get("name") or label_for(u, label_map), u))
        elif s:
            out.append((label_for(str(s), label_map), str(s)))
    return out


def source_line(rec, label_map):
    pairs = source_pairs(rec, label_map)
    return "来源 %d 个 · %s" % (len(pairs), " · ".join("[%s](%s)" % p for p in pairs))


def _src_names(rec, label_map):
    """摘要紧凑行用的来源展示：只列来源名链接，不带『来源 N 个』前缀。"""
    pairs = source_pairs(rec, label_map)
    return " · ".join("[%s](%s)" % p for p in pairs) or rec.get("target", "?")


# ---------- 时间：拿到就显示，精度按「日 / 分」自适应 ----------

def _parse_time(value):
    """尽力把各种时间字符串解析成 datetime；解析不出返回 None。零依赖。"""
    if not value:
        return None
    s = str(value).strip()
    # ISO 8601（含 'Z' 与 ±偏移）
    try:
        return datetime.datetime.fromisoformat(s.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        pass
    # RFC 2822（RSS pubDate：Mon, 22 Jun 2026 10:30:00 GMT）
    try:
        dt = email.utils.parsedate_to_datetime(s)
        if isinstance(dt, datetime.datetime):
            return dt
    except (TypeError, ValueError):
        pass
    # 几种常见格式兜底
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d",
                "%a, %d %b %Y %H:%M:%S %z", "%a, %d %b %Y %H:%M:%S %Z",
                "%d %b %Y %H:%M:%S", "%d %b %Y"):
        try:
            return datetime.datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def _fmt_cn_time(value):
    """把时间渲染成中文，统一换算为北京时间，并在末尾标注『北京时间』。

    精度：有显式时分且原始时间不是午夜零点 → 『6月22日 18:30（北京时间）』；
    否则 → 『6月22日（北京时间）』。
    「是否仅日期」用原始时间判断（避免 UTC 午夜换算成东八区 08:00 后误判成有具体时刻）；
    显示的月/日/时/分统一换算到北京时间。无时区信息的字符串视为已是北京时间，不做平移。
    """
    dt = _parse_time(value)
    if dt is None:
        return None
    orig = dt
    if dt.tzinfo is not None:
        dt = dt.astimezone(BEIJING)
    has_time = bool(re.search(r"\d{1,2}:\d{2}|T\d", str(value)))
    if has_time and not (orig.hour == 0 and orig.minute == 0):
        return "北京时间 %d月%d日 %d:%02d" % (dt.month, dt.day, dt.hour, dt.minute)
    return "北京时间 %d月%d日" % (dt.month, dt.day)


def _published_for(rec, ws):
    """取一条变更的时间：优先判断时带回的 published，其次回查最近抓取结果。

    回查覆盖「判断时漏带 published」的情况；最近一次扫描里能拿到发布时间或
    首次发现时间。拿不到（如注册表项无时间）就返回 None —— 卡片不强行编造时间。
    """
    pv = rec.get("published")
    if pv:
        return pv
    cid = str(rec.get("change_id", "")).strip()
    if cid and ws:
        try:
            with open(os.path.join(ws, "out", "latest.json"), "r", encoding="utf-8") as fh:
                latest = json.load(fh)
            by_id = {c.get("change_id"): c for c in latest.get("changes", [])
                     if c.get("change_id")}
            c = by_id.get(cid)
            if c:
                return c.get("published") or c.get("first_seen")
        except (OSError, ValueError):
            pass
    # 回退到检测时间：每条落盘记录都带 logged_at，保证卡片永远有时间、不静默丢信息
    return rec.get("first_seen") or rec.get("logged_at")


def _record_blocks(ws, recs):
    """把变更记录渲染成交互容器元素列表（含末尾扫描统计行）。

    纯展示逻辑，不含标题/头部——供 build_card / build_digest_card 复用。
    recs 应为已按重要性排序的列表。
    """
    elements = []
    label_map = source_label_map(ws)
    shown = recs if len(recs) <= 5 else [r for r in recs if r.get("significance") != "low"]
    if not shown:                     # 全是低优先级的临界情况：宁可多放 5 条，也不要空卡片
        shown = recs[:5]
    for i, rec in enumerate(shown):
        tags = []
        k = KIND_STYLE.get(rec.get("change_kind"))
        if k:
            tags.append("<text_tag color='%s'>%s</text_tag>" % (k[1], k[0]))
        sig = SIG_STYLE.get(rec.get("significance"))
        if sig:
            tags.append("<text_tag color='%s'>%s</text_tag>" % (sig[1], sig[0]))
        tags.append("<text_tag color='neutral'>%s</text_tag>" % rec.get("type", ""))
        src_line = source_line(rec, label_map)
        ts = _fmt_cn_time(_published_for(rec, ws))
        if ts:
            src_line = "%s · %s" % (src_line, ts)
        block = {
            "tag": "interactive_container", "width": "fill", "has_border": True,
            "border_color": "blue-100", "corner_radius": "8px", "background_style": "blue-50",
            "padding": "12px 12px 12px 12px", "vertical_spacing": "4px",
            "elements": [
                {"tag": "markdown", "content": "%s **%s**" % (" ".join(tags), rec.get("title", ""))},
                {"tag": "markdown", "content": rec.get("summary", "")},
                {"tag": "markdown",
                 "content": "<font color='grey'>%s</font>" % src_line,
                 "text_size": "notation"},
            ],
        }
        if i != len(shown) - 1:
            block["margin"] = "0px 0px 12px 0px"
        elements.append(block)
    if len(shown) < len(recs):
        elements.append({"tag": "markdown",
                         "content": "<font color='grey'>另有 %d 条次要变更，见明细。</font>"
                                    % (len(recs) - len(shown)),
                         "text_size": "notation"})
    # 末尾扫描统计（来自最近一次抓取；与 build_status_card 同源）
    stats = ""
    try:
        with open(os.path.join(ws, "out", "latest.json"), "r", encoding="utf-8") as fh:
            latest = json.load(fh)
        s = latest.get("summary", {})
        stale = latest.get("stale_sources", [])
        names = "、".join(x.get("label", x.get("source_id", "?")) for x in stale) or "无"
        stats = "扫描 %s 个来源 · 抓取失败 %s · 停更 %d（%s）" % (
            s.get("sources", "?"), s.get("sources_failed", "?"), len(stale), names)
    except (OSError, ValueError):
        pass
    if stats:
        elements.append({"tag": "markdown", "content": "<font color='grey'>%s</font>" % stats,
                         "text_size": "notation"})
    return elements


def _digest_rows(ws, recs):
    """摘要卡专用紧凑列表：每条一行（序号 + 标签 + 标题 / 来源·类型·时间），不放长摘要。

    摘要定位是『一眼扫完本时段全貌』，信息密度优先；逐条长说明留给实时变更卡。
    recs 应为已按重要性排序的列表。

    整段列表收进一个无边框外层容器，使 build_digest_card 的 12px 块间距只作用于
    『统计块↔列表』『列表↔失败源』之间，记录之间改用 3px 紧间距，避免密度过低。
    """
    label_map = source_label_map(ws)
    rows = []
    shown = recs if len(recs) <= 20 else recs[:20]
    n = len(shown)
    for i, rec in enumerate(shown):
        seq = i + 1
        tags = []
        k = KIND_STYLE.get(rec.get("change_kind"))
        if k:
            tags.append("<text_tag color='%s'>%s</text_tag>" % (k[1], k[0]))
        sig = SIG_STYLE.get(rec.get("significance"))
        if sig:
            tags.append("<text_tag color='%s'>%s</text_tag>" % (sig[1], sig[0]))
        meta = "%s · %s" % (_src_names(rec, label_map), rec.get("type", ""))
        ts = _fmt_cn_time(_published_for(rec, ws))
        if ts:
            meta = "%s · %s" % (meta, ts)
        tag_str = (" ".join(tags) + " ") if tags else ""
        # 序号 1. 2. 3. 前置，加粗标题，标签内联
        title_md = "**%d.** %s**%s**" % (seq, tag_str, rec.get("title", ""))
        row = {
            "tag": "interactive_container", "width": "fill", "has_border": True,
            "border_color": "blue-100", "corner_radius": "6px", "background_style": "blue-50",
            "padding": "5px 10px 5px 10px", "vertical_spacing": "1px",
            "elements": [
                {"tag": "markdown", "content": title_md},
                {"tag": "markdown",
                 "content": "<font color='grey'>%s</font>" % meta,
                 "text_size": "notation"},
            ],
        }
        if i != n - 1:
            row["margin"] = "0px 0px 3px 0px"
        rows.append(row)
    if len(shown) < len(recs):
        rows.append({"tag": "markdown",
                     "content": "<font color='grey'>另有 %d 条变更，见各实时通知卡。</font>"
                                % (len(recs) - len(shown)),
                     "text_size": "notation"})
    # 外层无边框容器包裹，隔离 build_digest_card 的整块 12px 间距
    return [{
        "tag": "interactive_container", "width": "fill", "vertical_spacing": "0px",
        "elements": rows,
    }]


def build_card(ws, records, run_at, mention=None):
    """把判断结果渲染成飞书 interactive 卡片（Card 2.0）。

    标题、中文标签、来源合并、末尾统计全部在这里定死，上层拿到的是可直接发送的卡片。
    结构说明见 references/notify-card.md。

    mention：传入 notify.mention 配置（"all" 或 [open_id,...]）时，若本卡含高优变更，
    顶部加「⚠️ 重要变更，请关注 <at>」横幅，并把卡片头染红——用于发到群聊强提醒。
    """
    recs = sorted(records, key=lambda r: SIG_ORDER.get(r.get("significance"), 9))
    targets = {r.get("target") for r in recs}
    title = (target_display_name(ws, recs[0]["target"]) + " 监控简报"
             if len(targets) == 1 else "监控简报")

    elements = _record_blocks(ws, recs)

    head_tags = [{"tag": "text_tag", "text": {"tag": "plain_text",
                                              "content": "%d 条变更" % len(recs)}, "color": "blue"}]
    has_high = any(r.get("significance") == "high" for r in recs)
    if has_high:
        head_tags.append({"tag": "text_tag",
                          "text": {"tag": "plain_text", "content": "含重要变更"}, "color": "red"})

    # 群聊强提醒：高优变更在顶部加 @ 横幅，并把卡片头染红，确保群里一眼可见必看
    if mention and has_high:
        mnt = _mention_md(mention)
        if mnt:
            elements.insert(0, {"tag": "markdown",
                                "content": "**⚠️ 重要变更，请关注** %s" % mnt})

    return {
        "schema": "2.0",
        "config": {"update_multi": True, "width_mode": "default",
                   "style": {"text_size": {
                       "title": {"default": "heading-2", "pc": "heading-2", "mobile": "heading-3"},
                       "body": {"default": "normal", "pc": "normal", "mobile": "normal"},
                       "caption": {"default": "notation", "pc": "notation", "mobile": "notation"}}}},
        "header": {
            "title": {"tag": "plain_text", "content": title},
            "subtitle": {"tag": "plain_text", "content": "%s · 自动扫描" % run_at},
            "template": "red" if (mention and has_high) else "blue",
            "icon": {"tag": "standard_icon", "token": "bell_outlined", "color": "blue"},
            "text_tag_list": head_tags,
        },
        "body": {"direction": "vertical", "padding": "12px 12px 20px 12px", "elements": elements},
    }


def build_cards_by_target(ws, records, run_at, mention=None):
    """按目标分组，每个目标一张卡（notify-card.md：一张卡只讲一个目标，标题不丢焦）。

    mention：透传给 build_card，用于群聊强提醒版本。
    """
    by_target = {}
    for r in records:
        by_target.setdefault(r.get("target"), []).append(r)
    return [(t, build_card(ws, recs, run_at, mention=mention)) for t, recs in by_target.items()]


def _status_card_config():
    """与 build_card 保持一致的 config 骨架。"""
    return {
        "update_multi": True, "width_mode": "default",
        "style": {
            "text_size": {
                "title": {"default": "heading-2", "pc": "heading-2", "mobile": "heading-3"},
                "body": {"default": "normal", "pc": "normal", "mobile": "normal"},
                "caption": {"default": "notation", "pc": "notation", "mobile": "notation"},
            }
        },
    }


def _status_title(ws):
    """单目标用显示名，多目标用通用名。"""
    try:
        with open(os.path.join(ws, "targets.json"), "r", encoding="utf-8") as fh:
            cfg = json.load(fh)
    except (OSError, ValueError):
        cfg = {}
    tlist = cfg.get("targets", [])
    if len(tlist) == 1:
        return (tlist[0].get("short_name") or tlist[0].get("name") or tlist[0].get("id")) + " 监控简报"
    return "监控简报"


def _failed_source_rows(ws):
    """返回本轮抓取失败源的 markdown 行列表（"- 名称（已持续 Xh）"）；无失败返回 []。

    供 build_status_card 与 build_digest_card 复用，避免重复写失败源渲染。
    """
    try:
        with open(os.path.join(ws, "out", "latest.json"), "r", encoding="utf-8") as fh:
            latest = json.load(fh)
    except (OSError, ValueError):
        return []
    failed = [x for x in latest.get("sources", []) if not x.get("ok")]
    if not failed:
        return []
    by_id = {x.get("source_id"): x for x in latest.get("sources", []) if x.get("source_id")}
    rows = []
    for x in failed:
        sid = x.get("source_id")
        label = str(x.get("label", sid))
        fh_h = x.get("failing_hours")
        suf = "（已持续 %sh）" % int(fh_h) if isinstance(fh_h, (int, float)) else ""
        rows.append("- " + _src_link(by_id, sid, label, suf))
    return rows


def _derive_home(url):
    """从源的抓取地址推导一个适合人类点击溯源的页面地址。

    抓取用 url 往往是 feed XML 或带查询参数的列表页，直接给用户点不友好，
    这里尽量还原成人类可读的站点/栏目主页。
    """
    if not url:
        return None
    base = url.split("?", 1)[0]
    for suf in ("/feed.xml", "/feed/", "/rss.xml", "/rss/"):
        if base.endswith(suf):
            base = base[: -len(suf)]
            break
    else:
        if base.endswith("/feed"):
            base = base[: -len("/feed")]
        elif base.endswith("/rss"):
            base = base[: -len("/rss")]
    if base.endswith("/"):
        base = base[:-1]
    return base or url


def _src_link(by_id, sid, label, suffix=""):
    """生成「可点击溯源」的源字段：取到主页则 [名](主页)，否则退化为纯文本。"""
    spec = by_id.get(sid, {})
    home = _derive_home(spec.get("url")) or spec.get("url")
    if home:
        return "[%s](%s)%s" % (label, home, suffix)
    return "%s%s" % (label, suffix)


def build_status_card(ws, run_at):
    """扫描状态简报（notify-card.md §五 变体）。

    用于「无变更但需知会扫描健康」的场合：用户主动询问，或扫描有明显异常
    （失败 / 停更多起来）。数据取自最近一次抓取结果 out/latest.json。
    """
    try:
        with open(os.path.join(ws, "out", "latest.json"), "r", encoding="utf-8") as fh:
            latest = json.load(fh)
    except (OSError, ValueError):
        return None
    s = latest.get("summary", {})
    stale = latest.get("stale_sources", [])
    failed = [x for x in latest.get("sources", []) if not x.get("ok")]
    by_id = {x.get("source_id"): x for x in latest.get("sources", []) if x.get("source_id")}
    total = s.get("total", 0)

    if total == 0:
        main_md = ("**本轮无变更**\n全部 %s 个来源与上次快照一致，无新增、下架或状态变化。"
                   % s.get("sources", 0))
    else:
        main_md = "**本轮发现 %d 条变更**\n详见明细归档，可按时间线 / 类型 / 对象检索。" % total

    blocks = [{
        "tag": "interactive_container", "width": "fill", "has_border": True,
        "border_color": "blue-100", "corner_radius": "8px", "background_style": "blue-50",
        "padding": "12px 12px 12px 12px", "vertical_spacing": "4px",
        "elements": [{"tag": "markdown", "content": main_md}],
    }]

    anomaly = []
    if stale:
        rows = []
        for x in stale:
            sid = x.get("source_id")
            label = str(x.get("label", sid))
            h = x.get("stale_hours")
            suf = "（约%d天）" % round(h / 24) if isinstance(h, (int, float)) else ""
            rows.append("- " + _src_link(by_id, sid, label, suf))
        anomaly.append("**停更源（%d）**（已成功抓取 · 对方长期未更新）：\n%s"
                        % (len(stale), "\n".join(rows)))
    failed_rows = _failed_source_rows(ws)
    if failed_rows:
        anomaly.append("**抓取失败（%d）**（本次无法连接该源 · 非停更）：\n%s"
                       % (len(failed_rows), "\n".join(failed_rows)))
    if anomaly:
        # 每节各自独立成一个 markdown 元素 —— 不能把两节用单个换行拼进同一元素：
        # 飞书 markdown 会把「- 列表项」紧接着的下一行当作该列表项的续行（lazy
        # continuation），于是「抓取失败」整节会被并进「停更源」最后一条里，视觉上
        # 混成一团。容器元素本就纵向排列，拆开即可，且不依赖换行是否生效。
        blocks.append({
            "tag": "interactive_container", "width": "fill", "has_border": True,
            "border_color": "yellow-100", "corner_radius": "8px", "background_style": "yellow-50",
            "padding": "12px 12px 12px 12px", "vertical_spacing": "8px",
            "elements": [{"tag": "markdown", "content": a} for a in anomaly],
        })

    stale_links = "、".join(_src_link(by_id, x.get("source_id"),
                                       str(x.get("label", x.get("source_id", "?"))))
                             for x in stale) or "无"
    stats = "扫描 %s 个来源 · 抓取失败 %s · 停更 %d（%s）" % (
        s.get("sources", "?"), s.get("sources_failed", "?"), len(stale), stale_links)
    # 状态卡是"安静时候"唯一的发声渠道，读者必须能看出这份简报有多新鲜 ——
    # 否则采集真停了、latest.json 冻在几天前，卡片上看起来也一切正常。
    scanned = _fmt_cn_time(latest.get("run_at"))
    if scanned:
        stats = "%s · 数据采集于 %s" % (stats, scanned)
    blocks.append({"tag": "markdown",
                   "content": "<font color='grey'>%s</font>" % stats, "text_size": "notation"})

    for i, b in enumerate(blocks):
        if i != len(blocks) - 1:
            b["margin"] = "0px 0px 12px 0px"

    head_tags = []
    if total == 0:
        head_tags.append({"tag": "text_tag",
                         "text": {"tag": "plain_text", "content": "无变更"}, "color": "green"})
    else:
        head_tags.append({"tag": "text_tag",
                         "text": {"tag": "plain_text", "content": "%d 条变更" % total}, "color": "blue"})
    if failed:
        head_tags.append({"tag": "text_tag",
                         "text": {"tag": "plain_text", "content": "有失败"}, "color": "red"})

    return {
        "schema": "2.0",
        "config": _status_card_config(),
        "header": {
            "title": {"tag": "plain_text", "content": _status_title(ws)},
            "subtitle": {"tag": "plain_text", "content": "%s · 自动扫描" % run_at},
            "template": "blue",
            "icon": {"tag": "standard_icon", "token": "bell_outlined", "color": "blue"},
            "text_tag_list": head_tags,
        },
        "body": {"direction": "vertical", "padding": "12px 12px 20px 12px", "elements": blocks},
    }


def digest_config(ws):
    """读取 targets.json 顶层 digest 块（每日摘要配置）。读不到返回 {}。"""
    try:
        with open(os.path.join(ws, "targets.json"), "r", encoding="utf-8") as fh:
            cfg = json.load(fh)
    except (OSError, ValueError):
        return {}
    d = cfg.get("digest") or {}
    return d if isinstance(d, dict) else {}


def _changes_since(ws, start_dt, inclusive=True):
    """读取 log/changes.jsonl，返回 logged_at（北京时间）>= start_dt（inclusive=True）
    或 > start_dt（inclusive=False）的变更记录。digest 增量窗口用 > 以避免边界重复。"""
    out = []
    path = os.path.join(ws, "log", "changes.jsonl")
    if not os.path.exists(path):
        return out
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            la = rec.get("logged_at")
            if not la:
                continue
            dt = _parse_time(la)
            if dt is None:
                continue
            if dt.tzinfo is not None:
                dt = dt.astimezone(BEIJING)
            hit = dt >= start_dt if inclusive else dt > start_dt
            if hit:
                out.append(rec)
    return out


def build_digest_card(ws, records, run_at, window_label):
    """每日变更摘要卡：聚合窗口内全部已判定变更，按严重度/目标/类型统计，附当前不可用源。

    records：窗口内变更（dict 列表，已含 significance/target/type）。为空时仍返回一张
    「本时段无变更」摘要卡（每天定时汇总，间隔够长，有无结果都要正常通知，不作为心跳）。
    """
    recs = sorted(records, key=lambda r: SIG_ORDER.get(r.get("significance"), 9))

    by_sig, by_target, by_type = {}, {}, {}
    for r in recs:
        k = r.get("significance", "?")
        by_sig[k] = by_sig.get(k, 0) + 1
        t = r.get("target", "?")
        by_target[t] = by_target.get(t, 0) + 1
        y = r.get("type", "?")
        by_type[y] = by_type.get(y, 0) + 1

    def _sig_label(k):
        return SIG_STYLE.get(k, (str(k),))[0]
    sig_line = " · ".join("%s %d" % (_sig_label(k), by_sig[k])
                          for k in sorted(by_sig, key=lambda x: SIG_ORDER.get(x, 9)))
    target_line = "、".join("%s %d" % (target_display_name(ws, t), n)
                            for t, n in by_target.items()) or "无"
    type_line = "、".join("%s %d" % (k, n) for k, n in
                          sorted(by_type.items(), key=lambda kv: -kv[1])) or "无"
    if recs:
        summary_md = "**本时段变更 %d 条** ［%s］\n按目标：%s\n按类型：%s" % (
            len(recs), sig_line, target_line, type_line)
    else:
        summary_md = "**本时段变更 0 条**\n截至 %s 暂无已确认的变更。" % run_at

    blocks = [{
        "tag": "interactive_container", "width": "fill", "has_border": True,
        "border_color": "blue-100", "corner_radius": "8px", "background_style": "blue-50",
        "padding": "12px 12px 12px 12px", "vertical_spacing": "4px",
        "elements": [{"tag": "markdown", "content": summary_md}],
    }]
    if recs:
        blocks += _digest_rows(ws, recs)

    failed_rows = _failed_source_rows(ws)
    if failed_rows:
        blocks.append({
            "tag": "interactive_container", "width": "fill", "has_border": True,
            "border_color": "yellow-100", "corner_radius": "8px", "background_style": "yellow-50",
            "padding": "12px 12px 12px 12px", "vertical_spacing": "8px",
            "elements": [{"tag": "markdown",
                          "content": "**当前不可用源（%d）**（本次无法连接该源 · 非停更）：\n%s"
                                     % (len(failed_rows), "\n".join(failed_rows))}],
        })

    for i, b in enumerate(blocks):
        if i != len(blocks) - 1:
            b["margin"] = "0px 0px 12px 0px"

    return {
        "schema": "2.0",
        "config": _status_card_config(),
        "header": {
            "title": {"tag": "plain_text", "content": "每日变更摘要"},
            "subtitle": {"tag": "plain_text", "content": "%s · %s" % (window_label, run_at)},
            "template": "blue",
            "icon": {"tag": "standard_icon", "token": "calendar_clock_outlined", "color": "blue"},
            "text_tag_list": [{"tag": "text_tag",
                               "text": {"tag": "plain_text", "content": "%d 条变更" % len(recs)},
                               "color": "blue" if recs else "green"}],
        },
        "body": {"direction": "vertical", "padding": "12px 12px 20px 12px", "elements": blocks},
    }


def _atomic_write_json(path, data):
    """原子写 JSON：先写 .tmp 再 os.replace，避免半写文件被下一轮读到。"""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def _read_json_or(path, default):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return default


def _digest_state_path(ws):
    return os.path.join(ws, "state", "digest_checkpoint.json")


def _digest_pending_path(ws):
    return os.path.join(ws, "state", "digest_pending.json")


@contextlib.contextmanager
def _digest_lock(ws):
    """对 digest 状态加排他锁，防并发（自动化 + 手动）竞争锚点。无 fcntl 则降级为空操作。"""
    path = os.path.join(ws, "state", "digest.lock")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fh = open(path, "w")
    try:
        if fcntl is not None:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        yield
    finally:
        try:
            if fcntl is not None:
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        finally:
            fh.close()


def _fmt_cn_short(value):
    """把 ISO/时间字符串渲染成『M月D日 HH:MM』（统一北京时间）；解析不出原样返回。"""
    dt = _parse_time(value) if isinstance(value, str) else value
    if dt is None:
        return str(value)
    if dt.tzinfo is not None:
        dt = dt.astimezone(BEIJING)
    return "%d月%d日 %02d:%02d" % (dt.month, dt.day, dt.hour, dt.minute)


def _cmd_digest(ws):
    """生成每日变更摘要卡（持 digest 排他锁后转 impl，防并发竞争锚点/pending）。"""
    with _digest_lock(ws):
        return _cmd_digest_impl(ws)


def _cmd_digest_impl(ws):
    """生成每日变更摘要卡：聚合窗口内已判定的全部变更 + 当前不可用源。

    每天定时汇总，间隔够长，**有无变更都正常通知**（无变更时为「本时段变更 0 条」卡）。
    不作为心跳卡——日级间隔本身已是足够长的发声。digest.enabled=false 时跳过。
    发出目标为群（notify.chat_id），不发私信（与变更卡/健康卡区分渠道）。

    窗口模式（targets.json: digest.mode）：
    - "delta"（默认）：锚定「上次已交付摘要」，窗口 = (上次 sent_at, 本次 to]；无锚点则
      seed = now - seed_hours。**本命令只产卡 + 写 pending，不推进锚点**；须由调用方在
      发送成功后跑 `--digest --commit` 推进（宁重勿漏：发送失败不推进→下轮自动重覆盖）。
      对多日间隔免疫：from 取上次锚点、to 取本次运行时刻，窗口自动吸收间隔，首尾相接不重不漏。
    - "day"（legacy）：锚定自然日零点，窗口 = [今天 00:00, now]（旧行为，可回退）。
    """
    cfg = digest_config(ws)
    if not cfg.get("enabled", True):
        print("摘要已禁用（digest.enabled=false），跳过。")
        return 0
    include_low = bool(cfg.get("include_low", True))
    mode = str(cfg.get("mode", "delta")).strip().lower()
    if mode not in ("delta", "day"):
        print("digest.mode 取值应为 delta/day，当前 %r；按 delta 处理。" % mode, file=sys.stderr)
        mode = "delta"
    try:
        seed_h = float(cfg.get("seed_hours", 24))
    except (TypeError, ValueError):
        print("digest.seed_hours 取值应为小时数，当前 %r；按 24 处理。" % (cfg.get("seed_hours"),), file=sys.stderr)
        seed_h = 24.0
    seed_h = max(0.0, seed_h)

    now = datetime.datetime.now(BEIJING)
    run_at = beijing_now_str()
    to_iso = now.isoformat(timespec="seconds")
    anchor_to = to_iso  # 推进锚点用；delta 下会抬到「窗口内最大 logged_at」以防重复覆盖
    if mode == "day":
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        window_label = "北京时间 %d月%d日 00:00 至今" % (start.month, start.day)
        recs = _changes_since(ws, start, inclusive=True)
        from_dt = start
    else:
        ck = _read_json_or(_digest_state_path(ws), None)
        from_dt = _parse_time(ck.get("sent_at")) if isinstance(ck, dict) else None
        if from_dt is not None and from_dt.tzinfo is not None:
            from_dt = from_dt.astimezone(BEIJING)
        if from_dt is None:
            from_dt = now - datetime.timedelta(hours=seed_h)
            seeded = True
        else:
            seeded = False
        recs = _changes_since(ws, from_dt, inclusive=False)
        # 锚点必须 >= 窗口内最大 logged_at，否则该记录下轮会被重复覆盖
        # （防御时钟前移 / 未来时间戳；正常情况下 max_la <= to，锚点即 to）
        _maxla = max((r.get("logged_at") for r in recs if r.get("logged_at")), default=None)
        if _maxla:
            _ml, _td = _parse_time(_maxla), _parse_time(to_iso)
            if _ml is not None and _td is not None:
                if _ml.tzinfo is not None:
                    _ml = _ml.astimezone(BEIJING)
                if _td.tzinfo is not None:
                    _td = _td.astimezone(BEIJING)
                if _ml > _td:
                    anchor_to = _maxla
        if seeded:
            window_label = "首次简报（回溯近 %.0f 小时）%s 至 %s" % (
                seed_h, _fmt_cn_short(from_dt), _fmt_cn_short(to_iso))
        else:
            window_label = "自上次简报 %s 至 %s" % (_fmt_cn_short(from_dt), _fmt_cn_short(to_iso))

    if not include_low:
        recs = [r for r in recs if r.get("significance") != "low"]
    card = build_digest_card(ws, recs, run_at, window_label)
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    os.makedirs(os.path.join(ws, "out"), exist_ok=True)
    card_path = os.path.join(ws, "out", "card-digest-%s.json" % stamp)
    with open(card_path, "w", encoding="utf-8") as fh:
        json.dump(card, fh, ensure_ascii=False, indent=2)
    n = len(recs)
    print("每日变更摘要卡片（%s）：%s" % ("无变更" if n == 0 else "%d 条变更" % n, card_path))
    print("  窗口：%s（模式 %s）" % (window_label, mode))

    if mode == "delta":
        # 只把本次 to 记进 pending，供 --commit 推进锚点；本步绝不推进（宁重勿漏）
        try:
            _atomic_write_json(_digest_pending_path(ws), {
                "window_from": from_dt.isoformat(timespec="seconds"),
                "window_to": anchor_to,
                "count": n,
                "card": os.path.basename(card_path),
                "created_at": now_iso(),
            })
        except OSError as e:
            print("写入摘要 pending 失败：%s" % e, file=sys.stderr)

    ncfg = notify_config(ws)
    chat_id = ncfg.get("chat_id")
    if chat_id:
        print("  lark-cli im +messages-send --as bot --chat-id %s "
              "--msg-type interactive --content - < %s" % (chat_id, card_path))
        if mode == "delta":
            print("  （发送 ok 后务必运行以推进锚点：python3 %s --digest --commit --workspace %s）"
                  % (os.path.abspath(__file__), ws))
    else:
        print("  （未配置群聊目标 notify.chat_id，跳过群发；如需群发请配置）")
    return 0


def _cmd_digest_commit(ws):
    """delta 模式：把上次【已发送成功】的摘要窗口推进为锚点。

    只读 state/digest_pending.json，把 checkpoint.sent_at 设为 pending.window_to
    （= 上次 --digest 选取的上界，**绝不写"现在"**），并清掉 pending。
    单调保护：待提交上界不晚于当前锚点则不倒退。无 pending 则无需提交。
    发送失败时**不要**调用本命令 → 锚点不推进 → 下轮 --digest 自动重覆盖（宁重勿漏）。
    """
    with _digest_lock(ws):
        pend = _read_json_or(_digest_pending_path(ws), None)
        if not isinstance(pend, dict) or not pend.get("window_to"):
            print("没有待提交的摘要窗口（digest_pending.json 缺失/为空）；无需 commit。")
            return 0
        to_iso = str(pend.get("window_to"))
        to_dt = _parse_time(to_iso)
        if to_dt is not None and to_dt.tzinfo is not None:
            to_dt = to_dt.astimezone(BEIJING)
        ck = _read_json_or(_digest_state_path(ws), None)
        cur_dt = _parse_time(ck.get("sent_at")) if isinstance(ck, dict) and ck.get("sent_at") else None
        if cur_dt is not None and cur_dt.tzinfo is not None:
            cur_dt = cur_dt.astimezone(BEIJING)
        if to_dt is not None and cur_dt is not None and to_dt <= cur_dt:
            print("待提交窗口上界 %s 不晚于当前锚点，跳过（单调保护，不倒退）。" % to_iso)
            try:
                os.remove(_digest_pending_path(ws))
            except OSError:
                pass
            return 0
        data = {
            "sent_at": to_iso,
            "window_from": pend.get("window_from"),
            "window_to": to_iso,
            "count": pend.get("count"),
            "committed_at": now_iso(),
        }
        try:
            _atomic_write_json(_digest_state_path(ws), data)
        except OSError as e:
            print("提交摘要锚点失败：%s（未推进，下轮将重覆盖，宁重勿漏）" % e, file=sys.stderr)
            return 1
        try:
            os.remove(_digest_pending_path(ws))
        except OSError:
            pass
        print("摘要锚点已推进至 %s（该窗口及以前视为已交付，count=%s）。" % (to_iso, pend.get("count")))
        return 0


def build_health_card(alerts, run_at):
    """源健康跃迁告警卡：alerts 为 [(label, kind, failing_hours), ...]，kind∈{down,ok}。"""
    lines = []
    for label, kind, fh_h in alerts:
        if kind == "down":
            suf = "（已持续 %sh）" % int(fh_h) if isinstance(fh_h, (int, float)) else ""
            lines.append("- ⚠️ **%s** 不可用%s" % (label, suf))
        else:
            lines.append("- ✅ **%s** 已恢复" % label)
    block = {
        "tag": "interactive_container", "width": "fill", "has_border": True,
        "border_color": "yellow-100", "corner_radius": "8px", "background_style": "yellow-50",
        "padding": "12px 12px 12px 12px", "vertical_spacing": "8px",
        "elements": [{"tag": "markdown",
                      "content": "**监控源健康跃迁**\n" + "\n".join(lines)}],
    }
    downs = sum(1 for a in alerts if a[1] == "down")
    ups = sum(1 for a in alerts if a[1] == "ok")
    head_tags = []
    if downs:
        head_tags.append({"tag": "text_tag",
                          "text": {"tag": "plain_text", "content": "%d 不可用" % downs}, "color": "red"})
    if ups:
        head_tags.append({"tag": "text_tag",
                          "text": {"tag": "plain_text", "content": "%d 已恢复" % ups}, "color": "green"})
    return {
        "schema": "2.0",
        "config": _status_card_config(),
        "header": {
            "title": {"tag": "plain_text", "content": "监控源健康"},
            "subtitle": {"tag": "plain_text", "content": "%s · 自动巡检" % run_at},
            "template": "red" if downs else "blue",
            "icon": {"tag": "standard_icon", "token": "warning_outlined", "color": "blue"},
            "text_tag_list": head_tags,
        },
        "body": {"direction": "vertical", "padding": "12px 12px 20px 12px",
                 "elements": [block]},
    }


def _cmd_health(ws):
    """对源健康跃迁发一次性告警：ok→down / →ok 各一次；持续失联（无新跃迁）静音。

    去重依据：out/latest.json 里每个源本轮的 health_transition 字段本身就只在
    状态真正变化那一轮出现（稳定态不会有该字段），所以同一持续故障不会反复告警。
    再用 state/health_alerts.json 记已告警的跃迁串，防止对同一份 latest.json 重复
    跑 --health 时重复发。flapping 等中间态不告警。
    """
    try:
        with open(os.path.join(ws, "out", "latest.json"), "r", encoding="utf-8") as fh:
            latest = json.load(fh)
    except (OSError, ValueError):
        print("无 out/latest.json，跳过健康告警。", file=sys.stderr)
        return 0
    sources = latest.get("sources", [])
    alert_state_path = os.path.join(ws, "state", "health_alerts.json")
    try:
        with open(alert_state_path, "r", encoding="utf-8") as fh:
            alerted = json.load(fh)
    except (OSError, ValueError):
        alerted = {}
    if not isinstance(alerted, dict):
        alerted = {}

    alerts = []
    for s in sources:
        trans = s.get("health_transition")
        if not trans:
            continue
        sid = s.get("source_id")
        # 跃迁串形如 "ok → down" / "flapping → ok"（watch.py 用空格包裹箭头）。
        # 只关心「新状态」（串尾那个词）：down=不可用，ok=已恢复，其余（flapping 等）静音。
        # 不按箭头字符精确匹配——箭头写法变动也不影响判断。
        parts = trans.split()
        new_state = parts[-1] if parts else ""
        old_state = parts[0] if len(parts) >= 3 else ""
        if new_state == "down":
            if alerted.get(sid) != trans:
                alerts.append((str(s.get("label", sid)), "down", s.get("failing_hours"), trans))
                alerted[sid] = trans
        elif new_state == "ok":
            # 只有从「持续失联 down」恢复才通知。flapping → ok 是单次网络抖动自愈，
            # 不值得发卡 —— 实测各源偶发失败率约 2.5%/轮，18 个源下这会频繁刷"已恢复"。
            if old_state == "down":
                if alerted.get(sid) != trans:
                    alerts.append((str(s.get("label", sid)), "ok", None, trans))
                    alerted[sid] = trans
            else:
                alerted.pop(sid, None)
        else:
            # flapping 等中间态不告警；清掉旧记录，未来再发生能重新告警
            alerted.pop(sid, None)

    if not alerts:
        # 静音：仅写回（清掉 flapping 标记），不产卡片、不发送
        try:
            os.makedirs(os.path.dirname(alert_state_path), exist_ok=True)
            with open(alert_state_path, "w", encoding="utf-8") as fh:
                json.dump(alerted, fh, ensure_ascii=False, indent=2)
        except OSError as e:
            print("写入健康告警状态失败：%s" % e, file=sys.stderr)
        print("无健康跃迁需告警（持续失联源已在上次告警，静音）。")
        return 0

    card = build_health_card([(a[0], a[1], a[2]) for a in alerts], beijing_now_str())
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    card_path = os.path.join(ws, "out", "card-health-%s.json" % stamp)
    with open(card_path, "w", encoding="utf-8") as fh:
        json.dump(card, fh, ensure_ascii=False, indent=2)
    print("监控源健康告警卡片：%s" % card_path)
    uid = notify_config(ws).get("user_id")
    if uid:
        print("  lark-cli im +messages-send --as bot --user-id %s "
              "--msg-type interactive --content - < %s" % (uid, card_path))
    else:
        print("  （未配置私信目标 notify.user_id，跳过私信；如需私信请配置）")
    try:
        os.makedirs(os.path.dirname(alert_state_path), exist_ok=True)
        with open(alert_state_path, "w", encoding="utf-8") as fh:
            json.dump(alerted, fh, ensure_ascii=False, indent=2)
    except OSError as e:
        print("写入健康告警状态失败：%s" % e, file=sys.stderr)
    return 0


# ---------- 心跳：频繁巡检的存活确认，但必须节流，否则去噪声失去意义 ----------

HEARTBEAT_STATE = "state/last_heartbeat.json"


def heartbeat_config(ws):
    """读取 targets.json 顶层 heartbeat 块（巡检心跳节流配置）。读不到返回 {}。"""
    try:
        with open(os.path.join(ws, "targets.json"), "r", encoding="utf-8") as fh:
            cfg = json.load(fh)
    except (OSError, ValueError):
        cfg = {}
    h = cfg.get("heartbeat") or {}
    return h if isinstance(h, dict) else {}


def _heartbeat_min_h(ws):
    """心跳最小间隔（小时）。默认 12，配置 heartbeat.min_hours 可覆盖。

    设计约束（用户明确）：频繁检测需要心跳证明存活，但绝不能频繁通知——
    否则去噪声（只在真变更时打扰）就失去意义。故阈值取 12h 起步、可上调到一天。
    """
    try:
        v = float(heartbeat_config(ws).get("min_hours", 12))
    except (TypeError, ValueError):
        v = 12.0
    return max(1.0, v)


def build_heartbeat_card(ws, run_at):
    """巡检心跳卡：证明监控仍在运行且最近一次扫描正常。

    数据取自 out/latest.json（与状态卡同源）。这是『安静时候的存活确认』，
    不报变更（变更另有实时卡），只在节流窗口到期时由 --heartbeat 发出。
    """
    try:
        with open(os.path.join(ws, "out", "latest.json"), "r", encoding="utf-8") as fh:
            latest = json.load(fh)
    except (OSError, ValueError):
        return None
    s = latest.get("summary", {})
    stale = latest.get("stale_sources", [])
    by_id = {x.get("source_id"): x for x in latest.get("sources", []) if x.get("source_id")}
    scanned = _fmt_cn_time(latest.get("run_at")) or run_at
    ok = s.get("sources_ok", "?")
    total_src = s.get("sources", "?")
    failed = s.get("sources_failed", 0)
    main_md = ("**监控运行中**\n最近一次自动巡检于 %s 完成，扫描 %s 个来源：%s 正常 / %d 停更 / %s 抓取失败。"
               % (scanned, total_src, ok, len(stale), failed))

    blocks = [{
        "tag": "interactive_container", "width": "fill", "has_border": True,
        "border_color": "blue-100", "corner_radius": "8px", "background_style": "blue-50",
        "padding": "12px 12px 12px 12px", "vertical_spacing": "4px",
        "elements": [{"tag": "markdown", "content": main_md}],
    }]
    anomaly = []
    if stale:
        rows = []
        for x in stale:
            sid = x.get("source_id")
            label = str(x.get("label", sid))
            h = x.get("stale_hours")
            suf = "（约%d天）" % round(h / 24) if isinstance(h, (int, float)) else ""
            rows.append("- " + _src_link(by_id, sid, label, suf))
        anomaly.append("**停更源（%d）**（已成功抓取 · 对方长期未更新）：\n%s"
                       % (len(stale), "\n".join(rows)))
    failed_rows = _failed_source_rows(ws)
    if failed_rows:
        anomaly.append("**抓取失败（%d）**（本次无法连接该源 · 非停更）：\n%s"
                       % (len(failed_rows), "\n".join(failed_rows)))
    if anomaly:
        blocks.append({
            "tag": "interactive_container", "width": "fill", "has_border": True,
            "border_color": "yellow-100", "corner_radius": "8px", "background_style": "yellow-50",
            "padding": "12px 12px 12px 12px", "vertical_spacing": "8px",
            "elements": [{"tag": "markdown", "content": a} for a in anomaly],
        })
    for i, b in enumerate(blocks):
        if i != len(blocks) - 1:
            b["margin"] = "0px 0px 12px 0px"

    head_tags = [{"tag": "text_tag",
                  "text": {"tag": "plain_text", "content": "心跳"}, "color": "blue"}]
    if failed:
        head_tags.append({"tag": "text_tag",
                          "text": {"tag": "plain_text", "content": "有失败"}, "color": "red"})
    return {
        "schema": "2.0",
        "config": _status_card_config(),
        "header": {
            "title": {"tag": "plain_text", "content": "巡检心跳"},
            "subtitle": {"tag": "plain_text", "content": "%s · 自动巡检" % run_at},
            "template": "blue" if not failed else "red",
            "icon": {"tag": "standard_icon", "token": "check_circle_outlined", "color": "blue"},
            "text_tag_list": head_tags,
        },
        "body": {"direction": "vertical", "padding": "12px 12px 20px 12px", "elements": blocks},
    }


def _cmd_heartbeat(ws):
    """巡检心跳：节流式存活确认。仅当距上次心跳 >= 阈值（默认 12h）时才发，否则静默。

    用法（放进每小时巡检任务的『本轮无变更』分支）：
      python3 log_change.py --heartbeat --workspace <ws>
    返回 0；产卡则打印私信发送命令（心跳发给本人，不刷群）。
    """
    min_h = _heartbeat_min_h(ws)
    state_path = os.path.join(ws, HEARTBEAT_STATE)
    last_iso = None
    try:
        with open(state_path, "r", encoding="utf-8") as fh:
            last_iso = json.load(fh).get("sent_at")
    except (OSError, ValueError):
        last_iso = None
    if last_iso:
        dt = _parse_time(last_iso)
        if dt is not None:
            if dt.tzinfo is not None:
                dt = dt.astimezone(BEIJING)
            hours = (datetime.datetime.now(BEIJING) - dt).total_seconds() / 3600.0
            if hours < min_h:
                print("心跳未到期（距上次 %.1fh，阈值 %.1fh），静默。" % (hours, min_h))
                return 0
    card = build_heartbeat_card(ws, beijing_now_str())
    if not card:
        print("无法生成心跳卡：缺少 out/latest.json 或无法读取", file=sys.stderr)
        return 1
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    card_path = os.path.join(ws, "out", "card-heartbeat-%s.json" % stamp)
    with open(card_path, "w", encoding="utf-8") as fh:
        json.dump(card, fh, ensure_ascii=False, indent=2)
    # 写入上次心跳时间（先落盘再返回，失败也继续——本轮已发，下轮自然节流）
    try:
        os.makedirs(os.path.dirname(state_path), exist_ok=True)
        with open(state_path, "w", encoding="utf-8") as fh:
            json.dump({"sent_at": now_iso()}, fh, ensure_ascii=False, indent=2)
    except OSError as e:
        print("写入心跳状态失败：%s" % e, file=sys.stderr)
    print("巡检心跳卡片：%s" % card_path)
    uid = notify_config(ws).get("user_id")
    if uid:
        print("  lark-cli im +messages-send --as bot --user-id %s "
              "--msg-type interactive --content - < %s" % (uid, card_path))
    else:
        print("  （未配置私信目标 notify.user_id，跳过私信；如需私信请配置）")
    return 0


def now_iso():
    return datetime.datetime.now(BEIJING).isoformat(timespec="seconds")


def dedupe_key(rec):
    """优先用 watch.py 发的 change_id —— 它唯一且稳定，也让待判队列能精确核销。
    手工补录（没有 change_id）时退回老的字段组合。"""
    cid = str(rec.get("change_id", "")).strip()
    if cid:
        return "cid:" + cid
    raw = "|".join([str(rec.get("target", "")), str(rec.get("change_kind", "")),
                    str(rec.get("url", "")), str(rec.get("type", ""))])
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def judged_ids(rec):
    """一条判断覆盖了哪些 change_id：自己那个 + 被合并进来的那些。

    合并（同一事件多来源合成一条）时如果不列出被合并的 id，覆盖度校验会把它当成
    "漏判"——但合并不是漏判。所以合并必须显式写 merged_change_ids。
    """
    out = set()
    cid = str(rec.get("change_id", "")).strip()
    if cid:
        out.add(cid)
    for x in (rec.get("merged_change_ids") or []):
        x = str(x).strip()
        if x:
            out.add(x)
    return out


def load_existing(path):
    keys, rows = set(), []
    if not os.path.exists(path):
        return keys, rows
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            rows.append(rec)
            keys.add(rec.get("dedupe_key") or dedupe_key(rec))
    return keys, rows


def main():
    ap = argparse.ArgumentParser(description="change-watch 记录判断结果")
    ap.add_argument("--workspace", default=DEFAULT_WS)
    ap.add_argument("--input", help="判断结果 JSON 文件")
    ap.add_argument("--no-notify", action="store_true", help="不生成待发送通知草稿")
    ap.add_argument("--status-card", action="store_true",
                   help="生成扫描状态简报卡片（无变更/有异常时知会；读取最近一次 out/latest.json）")
    ap.add_argument("--show", action="store_true", help="打印最近 20 条")
    ap.add_argument("--digest", action="store_true",
                   help="生成每日变更摘要卡片（聚合窗口内变更 + 当前不可用源；有无变更都发，不作心跳）")
    ap.add_argument("--commit", action="store_true",
                   help="配合 --digest：把上次已发送成功的摘要窗口推进为锚点（delta 模式；发送 ok 后调用）")
    ap.add_argument("--health", action="store_true",
                   help="对源健康跃迁（不可用/已恢复）发一次性告警；持续失联静音")
    ap.add_argument("--heartbeat", action="store_true",
                   help="巡检心跳（节流存活确认）：距上次 >= 阈值(默认12h)才发，否则静默；发给本人私信")
    args = ap.parse_args()

    ws = os.path.abspath(os.path.expanduser(args.workspace))
    log_dir = os.path.join(ws, "log")
    os.makedirs(log_dir, exist_ok=True)
    log_path = os.path.join(log_dir, "changes.jsonl")

    seen_keys, rows = load_existing(log_path)

    if args.show:
        for rec in rows[-20:]:
            print("%s  [%s] %-10s %-6s %s" % (
                rec.get("logged_at", "?")[:16], rec.get("target", "?"),
                rec.get("type", "?"), rec.get("significance", "?"), rec.get("title", "")))
        print("\n共 %d 条。" % len(rows))
        return 0

    if args.status_card:
        card = build_status_card(ws, beijing_now_str())
        if not card:
            print("无法生成状态卡：缺少 out/latest.json 或无法读取", file=sys.stderr)
            return 1
        stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        card_path = os.path.join(ws, "out", "card-status-%s.json" % stamp)
        with open(card_path, "w", encoding="utf-8") as fh:
            json.dump(card, fh, ensure_ascii=False, indent=2)
        print("扫描状态简报卡片：%s" % card_path)
        _uid0 = notify_config(ws).get("user_id")
        if _uid0:
            print("  lark-cli im +messages-send --as bot --user-id %s "
                  "--msg-type interactive --content - < %s" % (_uid0, card_path))
        else:
            print("  lark-cli im +messages-send --as bot --user-id <openId> "
                  "--msg-type interactive --content - < %s" % card_path)
            print("  （未配置私信目标 notify.user_id：交互运行 watch.py --init 自动写入，或手动设置）")
        # 群聊强提醒：本轮有抓取失败的源（监控降级）时，额外把状态简报发群并 @
        ncfg = notify_config(ws)
        chat_id = ncfg.get("chat_id")
        mention = ncfg.get("mention")
        failed = []
        if chat_id and mention:
            try:
                with open(os.path.join(ws, "out", "latest.json"), "r", encoding="utf-8") as fh:
                    latest = json.load(fh)
                failed = [x for x in latest.get("sources", []) if not x.get("ok")]
            except (OSError, ValueError):
                failed = []
        if chat_id and mention and failed:
            gcard = _mentionify(json.loads(json.dumps(card)), mention,
                                label="⚠️ 监控异常，请关注")
            gpath = os.path.join(ws, "out", "card-status-%s-group.json" % stamp)
            with open(gpath, "w", encoding="utf-8") as fh:
                json.dump(gcard, fh, ensure_ascii=False, indent=2)
            print("扫描状态简报卡片（群聊强提醒，含 @）：")
            print("  %s" % gpath)
            print("  lark-cli im +messages-send --as bot --chat-id %s "
                  "--msg-type interactive --content - < %s" % (chat_id, gpath))
        return 0

    if args.commit:
        return _cmd_digest_commit(ws)

    if args.digest:
        return _cmd_digest(ws)

    if args.health:
        return _cmd_health(ws)

    if args.heartbeat:
        return _cmd_heartbeat(ws)

    if not args.input:
        print("需要 --input 或 --show", file=sys.stderr)
        return 1

    with open(args.input, "r", encoding="utf-8") as fh:
        payload = json.load(fh)
    incoming = payload.get("changes", payload) if isinstance(payload, dict) else payload
    if not isinstance(incoming, list):
        print("输入格式不对：应为数组，或 {\"changes\": [...]}", file=sys.stderr)
        return 1

    src_texts = source_text_by_id(ws)
    ver = criteria_version()
    appended, skipped, invalid, ungrounded, discarded = [], [], [], [], []
    handled_ids = set()
    batch_ids = set()
    for rec in incoming:
        # 同一个 change_id 在同一批里出现两次（例如一次判为无关、一次又正常判断）
        # 是自相矛盾的，必须拦住 —— 否则流水里会同时留下"无关"和"已记录"两条。
        dup = judged_ids(rec) & batch_ids
        if dup:
            invalid.append((rec.get("title", "?"),
                            ["change_id %s 在本批次里出现了两次，结论矛盾"
                             % "/".join(sorted(dup))]))
            continue
        batch_ids |= judged_ids(rec)
        # 「判为不是真变更」必须显式写出来，不能靠"我不写它"来表达：
        # 什么都不写 = 漏判，会被反复带回重问，超过 24 小时未判才记进 unjudged.jsonl，纯噪音。
        if rec.get("discard"):
            row = {"change_id": str(rec.get("change_id", "")).strip(),
                   "title": rec.get("title") or rec.get("change_id"),
                   "reason": rec.get("discard_reason") or "未说明",
                   "logged_at": now_iso(), "criteria_version": ver}
            discarded.append(row)
            handled_ids |= judged_ids(rec)
            continue
        missing = [k for k in REQUIRED if not str(rec.get(k, "")).strip()]
        if missing:
            invalid.append((rec.get("title", "?"), missing))
            continue
        if rec["change_kind"] not in ALLOWED_KINDS:
            invalid.append((rec.get("title", "?"), ["change_kind 取值应为 %s" % "/".join(ALLOWED_KINDS)]))
            continue
        if rec["significance"] not in ALLOWED_SIG:
            invalid.append((rec.get("title", "?"), ["significance 取值应为 %s" % "/".join(ALLOWED_SIG)]))
            continue
        key = dedupe_key(rec)
        cid = str(rec.get("change_id", "")).strip()
        if key in seen_keys:
            skipped.append(rec.get("title", "?"))
            handled_ids |= judged_ids(rec)
            continue
        rec = dict(rec)
        rec["dedupe_key"] = key
        rec["logged_at"] = now_iso()
        rec["criteria_version"] = ver
        rec.setdefault("sources", [rec["url"]])
        if rec["significance"] == "unknown":
            rec["notify"] = False          # 没判断过就不许发
        else:
            # high/normal/low 默认都通知（实时性：变更不拖延，摘要与变更分开、
            # 变更全级别正常发）。AI 仍可显式写 notify:false 把某条标记为"落盘但不发"。
            rec.setdefault("notify", True)
        # 证据可查：说明里的版本号/日期/价格必须能在原文里找到，找不到就留痕
        src = src_texts.get(cid, "")
        if src:
            bad = ungrounded_tokens("%s %s" % (rec.get("title", ""), rec.get("summary", "")), src)
            if bad:
                rec["grounding"] = "unverified"
                rec["grounding_tokens"] = bad
                ungrounded.append((rec.get("title", "?"), bad))
        appended.append(rec)
        seen_keys.add(key)
        handled_ids |= judged_ids(rec)

    if appended:
        with open(log_path, "a", encoding="utf-8") as fh:
            for rec in appended:
                fh.write(json.dumps(rec, ensure_ascii=False) + "\n")

    # 已判的从待判队列里核销；没判的留在里面，下轮 watch 会再带回来问一次
    pending_path = os.path.join(ws, "state", "pending.json")
    if handled_ids and os.path.exists(pending_path):
        try:
            with open(pending_path, "r", encoding="utf-8") as fh:
                items = json.load(fh)
        except (OSError, ValueError):
            items = None
        if isinstance(items, list):
            rest = [it for it in items
                    if str((it or {}).get("change_id", "")).strip() not in handled_ids]
            if len(rest) != len(items):
                tmp = pending_path + ".tmp"
                with open(tmp, "w", encoding="utf-8") as fh:
                    json.dump(rest, fh, ensure_ascii=False, indent=2, sort_keys=True)
                os.replace(tmp, pending_path)

    if discarded:
        with open(os.path.join(log_dir, "discarded.jsonl"), "a", encoding="utf-8") as fh:
            for row in discarded:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")

    print("追加 %d 条，判为无关 %d 条，跳过重复 %d 条，格式不合法 %d 条。"
          % (len(appended), len(discarded), len(skipped), len(invalid)))
    for title, miss in invalid:
        print("  [不合法] %s -> 缺/错：%s" % (title[:40], ", ".join(miss)), file=sys.stderr)
    for title, bad in ungrounded:
        print("  [证据待核] %s -> 说明里的 %s 在原文里没找到，请回看原文再改"
              % (title[:40], ", ".join(bad)), file=sys.stderr)

    # 覆盖度校验：漏答不是"判断为无关"，是失败。点名，且不静默
    missing = sorted({k for k in src_texts if k} - handled_ids)
    coverage_incomplete = bool(missing)
    if missing:
        print("\n⚠ 本轮有 %d 条变更没给判断：%s" % (len(missing), ", ".join(missing)),
              file=sys.stderr)
        print("  它们留在待判队列里，下轮 watch 会再问一次（按未判年龄过期，默认 24 小时，"
              "与采集频次无关；超时才记入 log/unjudged.jsonl）。", file=sys.stderr)

    to_notify = [r for r in appended if r.get("notify") and not args.no_notify]
    if to_notify:
        stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        out = os.path.join(ws, "out", "notify-%s.md" % stamp)
        lm = source_label_map(ws)
        lines = ["# 待通知（%d 条）\n" % len(to_notify)]
        for rec in to_notify:
            lines.append("## [%s] %s" % (rec.get("type"), rec["title"]))
            lines.append("- 目标：%s｜变更类型：%s｜重要性：%s" % (
                rec.get("target"), rec.get("type"), rec.get("significance")))
            if rec.get("object"):
                lines.append("- 对象：%s" % rec["object"])
            lines.append("- 说明：%s" % rec["summary"])
            lines.append("- 来源：%s" % " ｜ ".join(
                "%s <%s>" % p for p in source_pairs(rec, lm)))
            if rec.get("notify_reason"):
                lines.append("- 为何值得看：%s" % rec["notify_reason"])
            lines.append("")
        with open(out, "w", encoding="utf-8") as fh:
            fh.write("\n".join(lines))
        print("待发送通知草稿：%s" % out)

        # 按目标分别发卡（notify-card.md：一张卡只讲一个目标，标题不丢焦）。
        by_target = {}
        for r in to_notify:
            by_target.setdefault(r.get("target"), []).append(r)
        ncfg = notify_config(ws)
        chat_id = ncfg.get("chat_id")
        mention = ncfg.get("mention")
        uid = ncfg.get("user_id")
        for ti, (tgt, recs) in enumerate(by_target.items()):
            card = build_card(ws, recs, beijing_now_str())
            card_path = os.path.join(ws, "out", "card-%s-%s.json" % (tgt, stamp))
            with open(card_path, "w", encoding="utf-8") as fh:
                json.dump(card, fh, ensure_ascii=False, indent=2)
            # 私信卡片：仅当配置了私信目标（notify.user_id）
            if uid:
                print("待发送飞书卡片（私信，目标 %s，中文标签已写好，**直接发这份，不要自己拼标签**）："
                      % tgt)
                print("  %s" % card_path)
                print("  lark-cli im +messages-send --as bot --user-id %s "
                      "--msg-type interactive --content - < %s" % (uid, card_path))
            else:
                print("（未配置私信目标 notify.user_id，跳过私信卡片；"
                      "如需私信请交互运行 watch.py --init 自动写入）")
            # 群聊强提醒：本目标含高优变更时，额外产出带 @ 的群卡片
            if chat_id and mention and any(r.get("significance") == "high" for r in recs):
                gcard = build_card(ws, recs, beijing_now_str(), mention=mention)
                gpath = os.path.join(ws, "out", "card-%s-%s-group.json" % (tgt, stamp))
                with open(gpath, "w", encoding="utf-8") as fh:
                    json.dump(gcard, fh, ensure_ascii=False, indent=2)
                print("待发送飞书卡片（群聊强提醒，目标 %s，含 @）：" % tgt)
                print("  %s" % gpath)
                print("  lark-cli im +messages-send --as bot --chat-id %s "
                      "--msg-type interactive --content - < %s" % (chat_id, gpath))
    # 返回 2 的两种"失败"（都非中止，已合法落盘的变更照常处理）：
    # ① 覆盖度不完整（有变更没给判断，pending 已保留、下轮再问）；
    # ② 判断记录格式非法（缺必填 / change_kind·significance 取值错 / change_id 自相矛盾）。
    # 两者都让编排/自动化知道本轮判断没做完或没做对。与 watch.py 退出码 2 语义不同
    #（那边是"有源抓取失败"），但自动化统一按"非 0 但不中止"处理即可。
    incomplete = coverage_incomplete or bool(invalid)
    return 2 if incomplete else 0


if __name__ == "__main__":
    sys.exit(main())
