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

import re
import threading
import time
import xml.etree.ElementTree as ET
from urllib.parse import urlparse

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

    # 键分配（先统计链接共用情况）
    link_count = {}
    for e in entries:
        k = core.norm_url(e["link"])
        if k:
            link_count[k] = link_count.get(k, 0) + 1
    used = set()
    for e in entries:
        link_key = core.norm_url(e["link"])
        if link_key and link_count[link_key] == 1:
            key = link_key
        elif e.get("_guid"):
            key = "guid:" + e["_guid"]
        else:
            key = "t:" + core.sha1((e["title"] or "") + "|" + (e["published"] or ""))
        n, i = 2, key
        while i in used:  # 极端兜底：仍冲突则加后缀，保证键唯一
            i = "%s#%d" % (key, n)
            n += 1
        used.add(i)
        e["key"] = i
    return entries


# ---------- 页面监测 ----------

def parse_web_page(body, spec):
    """按关键词出现与否产出条目：出现 -> 一条（added 语义），消失 -> 该键缺席（removed 语义）。"""
    text = core.norm(core.strip_tags(body)).lower()
    entries = []
    for kw in (spec.get("config") or {}).get("keywords") or []:
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
