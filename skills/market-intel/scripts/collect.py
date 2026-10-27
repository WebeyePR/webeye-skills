#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""采集适配器与并发调度。

源类型（kind）——与 change-watch / AIHOT 的源类型谱系对齐：
- rss        标准 RSS / Atom（量子位、雷峰网、极客公园、TechCrunch 等已实测可用）
- mp_account 微信公众号，经自部署 RSSHub 输出 RSS——解析与 rss 相同，但更保守：
             解析到 0 条视为失败显式报错（不静默空结果），建议低频轮询 + windowed: true
- web_page   通用页面监测（官网招聘页等）：按 config.keywords 的关键词在页面出现/消失
             产出条目（岗位关键词新增 = 扩张信号，消失 = 收缩信号）

适配器注册制：新增源类型在 COLLECTORS 里注册一个函数即可，调度与差分零改动。

并发约束沿用 change-watch（v20261014 的三条纪律）：
- 结果按配置顺序返回（不随完成情况抖动）；
- 用 daemon 工作线程（不用 ThreadPoolExecutor——它的线程非 daemon，卡住的源会拖死定时任务）；
- 单域名限流 + 整轮壁钟上限，超时未返回的源判失败并继续。
"""

import json
import re
import threading
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from urllib.parse import urljoin as _urljoin, urlparse

import core

ATOM_NS = "{http://www.w3.org/2005/Atom}"
DC_NS = "{http://purl.org/dc/elements/1.1/}"

_BARE_AMP = re.compile(r"&(?!#\d+;|#x[0-9A-Fa-f]+;|[a-zA-Z]+;)")
# XML 1.0 不允许的控制字符（雷峰网实测混入 \x0c 换页符），转义救不了，只能剔除
_BAD_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


# ---------- RSS / Atom 解析 ----------

def _child_text(node, *names):
    for name in names:
        for child in node:
            if child.tag in (name, ATOM_NS + name):
                return (child.text or "").strip()
    return ""


def _link_of(item):
    for child in item:
        if child.tag == "link" and (child.text or "").strip():
            return child.text.strip()
        if child.tag == ATOM_NS + "link":
            href = child.get("href") or ""
            if href.strip():
                return href.strip()
    return ""


def parse_feed(body):
    """解析 RSS/Atom 为条目列表。

    键规则沿用 change-watch v20261014.1 的修复：链接唯一 -> 用链接；链接被多条共用
    -> 用 Atom id / RSS guid；再退化 -> 标题+日期。绝不能用位置序号当键（会制造 phantom changed）。
    """
    try:
        root = ET.fromstring(body.strip().encode("utf-8"))
    except Exception:
        # 容错一次：真实 feed 常见两类脏数据——未转义的裸 &（需转义）与 XML 非法控制字符
        # （雷峰网实测混入 \x0c，需剔除）。清洗后重试，仍失败才按源报错（不静默空结果）。
        try:
            fixed = _BAD_CTRL.sub("", _BARE_AMP.sub("&amp;", body.strip()))
            root = ET.fromstring(fixed.encode("utf-8"))
        except Exception as exc:  # noqa: BLE001
            raise ValueError("RSS 解析失败: %s" % exc)

    items = root.findall(".//item") + root.findall(".//" + ATOM_NS + "entry")
    entries = []
    for it in items:
        title = core.norm(_child_text(it, "title"))
        link = _link_of(it)
        published = (_child_text(it, "pubDate") or _child_text(it, "published")
                     or _child_text(it, "updated") or _child_text(it, DC_NS + "date"))
        summary = core.norm(core.strip_tags(_child_text(it, "description") or _child_text(it, "summary")
                                            or _child_text(it, "content")))
        guid = _child_text(it, "guid") or _child_text(it, "id")
        entries.append({
            "title": title,
            "link": link,
            "published": published or None,
            "summary": summary[:1200],
            "text": "\n".join(x for x in [title, summary, published] if x),
            "_guid": guid or None,
        })

    # 防空条目静默：全部标题为空 = 解析失败（源会标 FAIL），不是"抓到了 N 条"（change-watch 陷阱15）
    if entries and all(not e["title"] for e in entries):
        raise ValueError("RSS 条目标题全空，疑似页面改版或编码问题，按源失败处理")

    # 键分配（内容正交）：同一链接无论批内出现几次，键恒定为归一 URL。
    # 此前按「批内链接唯一才用链接、重复则 guid:/#N」分配，同一活动在列表页多板块
    # 重复展示时跨批拿到 guid:URL / URL#2 等不同键 → change_id 漂移 → 已发防重发
    # 失效（每轮已通报活动以新变更身份入窗）。同键重复条目由差分层 dict 化自然合并。
    for e in entries:
        e["key"] = entry_key_of(e)
    return entries


def entry_key_of(e):
    """条目内容正交键：归一链接 > guid > 标题哈希（纯函数，基线迁移复用同一规则）。"""
    link_key = core.norm_url(e.get("link"))
    if link_key:
        return link_key
    guid = e.get("_guid") or e.get("guid")
    if guid:
        return "guid:" + guid
    # 无链接无 guid：快照条目保留原键（guid:/t: 形态本就内容稳定——迁移场景
    # 快照不存 guid 字段，重算 t: 哈希反而会与新鲜抓取的 guid: 键错位成假新增）
    if e.get("key") and str(e["key"]).startswith(("t:", "guid:")):
        return e["key"]
    return "t:" + core.sha1((e.get("title") or "") + "|" + (e.get("published") or ""))


# ---------- 页面监测 ----------

_ANCHOR_RE = re.compile(r"<a\b[^>]*href=[\"']([^\"']+)[\"'][^>]*>(.*?)</a>", re.I | re.S)


def parse_web_page(body, spec):
    """页面监测，两种模式（spec.config.mode）：

    - keywords（默认）：关键词出现/消失（官网招聘页场景，key=kw:关键词）；
    - links：提取页面里的文章/条目链接做差分（资讯列表页/榜单页场景）。
      "基线有、本轮新出现" = added，天然实现"排除霸榜、只看新上榜"的榜单语义。
      config: link_pattern（href 过滤正则，可选）、max_items（上限，默认 60）、
      title_min_len（锚文本最短长度，过滤"更多"类噪音，默认 6）。
    """
    cfg = spec.get("config") or {}
    mode = cfg.get("mode") or "keywords"
    if mode == "links":
        return _page_links(body, spec, cfg)
    return _page_keywords(body, spec, cfg)


def _page_keywords(body, spec, cfg):
    text = core.norm(core.strip_tags(body)).lower()
    entries = []
    for kw in cfg.get("keywords") or []:
        if not kw:
            continue
        if kw.lower() in text:
            entries.append({
                "key": "kw:" + kw,
                "title": "页面出现「%s」" % kw,
                "link": spec.get("url"),
                "published": None,
                "summary": "关键词「%s」在页面中出现" % kw,
                "text": kw,
            })
    return entries


_LIST_ORDINAL = re.compile(r"^\s*\d{1,2}\s*[.、．]\s*(?=[\u4e00-\u9fffA-Za-z])")


def _strip_list_ordinal(title):
    """剥掉列表页自带的序号前缀（v0.6.5 修）。

    实测（品牌方舟列表页）：锚文本为「5. AI软硬件公司「Ambi」完成1.74亿元Pre-A轮融资」
    「4. 揽金6个亿，国产吸尘器在TikTok卖爆了！」——这个「5.」「4.」是**页面列表序号**，
    不是标题内容。不剥会出两个可见问题：
      ① 卡片里新闻段本身已有「1. 2. 3.」编号，标题再带「5.」就成了「3. 5. xxx」双重编号，
         读者会以为漏了 1-4 条；
      ② 搜索/去重时序号变化（同一内容在不同期排到不同位）会造成标题不一致。
    只剥**开头**的「数字 + 点/顿号 + 紧跟中文或字母」形态，且要求序号 ≤ 2 位，
    避免误伤「3D 打印」「5G 芯片」「100 天」这类以数字开头的真实标题
    （它们后面紧跟的是数字/单位而非点号，或不以「.」分隔）。
    """
    return _LIST_ORDINAL.sub("", title or "", count=1)


def _page_links(body, spec, cfg):
    """links 模式：锚点 -> 条目（key=规范化 URL，title=锚文本）。"""
    pattern = re.compile(cfg["link_pattern"]) if cfg.get("link_pattern") else None
    base = spec.get("url") or ""
    min_title = int(cfg.get("title_min_len", 6))
    max_items = int(cfg.get("max_items", 60))
    seen, entries = set(), []
    for href, anchor_html in _ANCHOR_RE.findall(body):
        href = (href or "").strip()
        if href.startswith(("javascript:", "#", "mailto:", "tel:")):
            continue
        link = _urljoin(base, href)
        if not link.startswith("http"):
            continue
        key = core.norm_url(link)
        if not key or key in seen:
            continue
        if pattern and not pattern.search(link):
            continue
        title = _strip_list_ordinal(core.norm(core.strip_tags(anchor_html)))
        if len(title) < min_title:
            continue
        seen.add(key)
        entries.append({"key": key, "title": title, "link": link, "published": None,
                        "summary": "", "text": title})
        if len(entries) >= max_items:
            break
    return entries


# ---------- 详情页富化（detail_enrich 源配置驱动） ----------

# 活动行详情页内嵌 var ativityJson = {...}（站点源码就少拼一个 c，实测如此）：
# 起止时间（ASP.NET \/Date(ts)\/ 写法）、城市/地址/标签/主办方齐全——RSS feed 本身
# 只有标题+链接（实测 description 为空），时间地点必须富化补全，否则卡片只能写「见报名页」。
_HDX_ACT_RE = re.compile(r"ivityJson\s*=\s*\{", re.S)
_HDX_DATE_RE = re.compile(r"\\?/Date\((\d{13})\)\\?/")


def _hdx_date(text):
    m = _HDX_DATE_RE.search(text or "")
    if not m:
        return None
    dt = datetime.fromtimestamp(int(m.group(1)) / 1000, tz=timezone(timedelta(hours=8)))
    return dt.strftime("%Y-%m-%d %H:%M")


def _balanced_json(text, start):
    """从 text[start]=='{' 起做括号配对，返回平衡的 JSON 子串（字符串内的括号不计数）。"""
    depth, in_str, esc = 0, False, False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
    return None


def enrich_huodongxing(entry, body):
    """活动行详情页富化：解析 ativityJson，把时间/地点/主办方/标签写进 entry。

    解析失败原样返回（AI 判断时仍可写「见报名页」并注明原因）——富化是增益不是门槛。
    """
    m = _HDX_ACT_RE.search(body or "")
    if not m:
        return entry
    raw = _balanced_json(body, m.end() - 1)
    if not raw:
        return entry
    try:
        data = json.loads(raw)
    except Exception:  # noqa: BLE001
        return entry
    start, end = _hdx_date(data.get("Start")), _hdx_date(data.get("End"))
    if not start:
        return entry
    time_text = start if not end else "%s ~ %s" % (start, end[5:])
    where = " · ".join(x for x in [data.get("City"), data.get("Address")] if x) or "线上/待公布"
    lines = ["时间：%s" % time_text, "地点：%s" % where]
    organizers = data.get("Organizers")
    if isinstance(organizers, list) and organizers:
        names = "、".join(str(o.get("Name")) for o in organizers[:3]
                          if isinstance(o, dict) and o.get("Name"))
        if names:
            lines.append("主办方：%s" % names)
    # 报名规模是热度信号（已报/上限），判断「重点活动」时可参考
    if data.get("MaxInstance"):
        lines.append("报名规模：%s/%s" % (data.get("InstanceNumber") or 0, data["MaxInstance"]))
    if data.get("Tag"):
        lines.append("标签：%s" % data["Tag"])
    old_summary = entry.get("summary") or ""
    entry["summary"] = "\n".join(lines + ([old_summary] if old_summary else []))
    entry["published"] = start
    entry["text"] = "\n".join(x for x in [entry.get("title"), entry["summary"], start] if x)
    return entry


_ENRICHERS = {"huodongxing.com": enrich_huodongxing}


def enrich_detail(spec, entry, settings, budget):
    """对单条 added 条目做详情页富化（spec.config.detail_enrich: true 时由管线调用）。

    budget: 剩余抓取配额列表 [n]（可变共享，单轮上限防超时）；失败静默返回原条目。
    """
    host = urlparse(entry.get("link") or "").netloc.replace("www.", "")
    fn = _ENRICHERS.get(host)
    if fn is None or budget[0] <= 0:
        return entry
    budget[0] -= 1
    try:
        timeout = int(settings.get("request_timeout_seconds", 30))
        body = core.fetch(entry["link"], timeout=timeout, retries=0)
        return fn(entry, body)
    except Exception:  # noqa: BLE001
        return entry


# ---------- 适配器注册表 ----------

def _collect_rss(body, spec):
    return parse_feed(body)


def _collect_mp(body, spec):
    entries = parse_feed(body)
    if not entries:
        # 公众号通道解析到 0 条：显式失败而非空结果（authwall / 改版会被看到，不会误读成"没更新"）
        raise ValueError("公众号通道解析到 0 条，疑似被拦或路由失效（请检查自部署 RSSHub）")
    return entries


def _collect_page(body, spec):
    return parse_web_page(body, spec)


COLLECTORS = {"rss": _collect_rss, "mp_account": _collect_mp, "web_page": _collect_page}


def collect_source(spec, settings):
    """采集单个源。返回 (ok, entries, error)。"""
    kind = spec.get("kind", "rss")
    fn = COLLECTORS.get(kind)
    if fn is None:
        return False, [], "未知源类型: %s" % kind
    if not spec.get("url"):
        return False, [], "缺少 url"
    timeout = spec.get("timeout_seconds", settings.get("request_timeout_seconds", 30))
    body = core.fetch(spec["url"], timeout=timeout, retries=settings.get("request_retries", 1))
    return True, fn(body, spec), None


# ---------- 并发调度 ----------

def run_collection(sources, settings):
    """并发采集，结果按配置顺序返回（与串行等价）。返回与 sources 等长的 (ok, entries, error) 列表。"""
    results = [None] * len(sources)
    if not sources:
        return results
    global_sem = threading.Semaphore(max(1, int(settings.get("collect_concurrency", 6))))
    host_sems = {}
    deadline = time.time() + max(30, int(settings.get("collect_wall_clock_seconds", 300)))

    def worker(i, spec):
        host = urlparse(spec.get("url", "")).netloc or "?"
        if host not in host_sems:
            host_sems[host] = threading.Semaphore(max(1, int(settings.get("host_concurrency", 2))))
        hsem = host_sems[host]

        def run():
            if time.time() > deadline:
                results[i] = (False, [], "整轮壁钟超时，本轮未执行")
                return
            with global_sem, hsem:
                if time.time() > deadline:
                    results[i] = (False, [], "整轮壁钟超时，本轮未执行")
                    return
                try:
                    results[i] = collect_source(spec, settings)
                except Exception as exc:  # noqa: BLE001 —— 单源失败不外溢
                    results[i] = (False, [], "%s: %s" % (type(exc).__name__, exc))

        t = threading.Thread(target=run, daemon=True)
        t.start()
        return t

    threads = [worker(i, spec) for i, spec in enumerate(sources)]
    for t in threads:
        t.join(max(1.0, deadline - time.time()))
    # 壁钟兜底：join 超时后仍未完成的槽位判失败
    for i, r in enumerate(results):
        if r is None:
            results[i] = (False, [], "采集超时未返回")
    return results
