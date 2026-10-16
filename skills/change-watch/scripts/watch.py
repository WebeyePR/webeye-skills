#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
change-watch :: 抓取 + 差分

职责（全部是确定性的，不调用任何模型、不产生任何费用）：
  1. 按配置拉取每个目标的所有信源（RSS 观测流 + 网页表格对象登记册）
  2. 与上一轮状态比对，算出变更集：新增 / 消失 / 变化
  3. 覆盖状态文件，并把本轮变更集写成 JSON 供上层（AI）判断
  4. 维护源健康记录（state/health.json）：连续失败计数、失联分级（flapping/down）、
     恢复识别；并按源判定停更（源可覆盖全局阈值，写 null 表示不检查）

用法：
  watch.py --baseline          # 首次：只建基线，不产出变更（避免把历史当变更推给你）
  watch.py                     # 常规一轮
  watch.py --dry-run           # 只比对，不写状态
  watch.py --target google     # 只跑某个目标
  watch.py --workspace ~/x     # 指定工作区（默认 ~/.change-watch）

退出码：0 正常；2 有源抓取失败（变更集仍会产出）；1 参数/配置错误；
        3 另一轮 watch 正在运行（单实例锁未拿到，本轮跳过 —— 不是故障，勿重试覆盖）
"""
import argparse
import datetime
import gzip
import hashlib
import html as htmlmod
import http.cookiejar
import json
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

try:                      # 单实例锁用 flock；无 fcntl 的平台（Windows）退化为不锁定
    import fcntl
except ImportError:       # pragma: no cover - 平台差异
    fcntl = None

SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# 仓库里只放脱敏模板 targets.json.example；config/targets.json 已被 .gitignore 永久排除
# （live 配置含真实群 ID 与个人 openId，永不入库）。首跑时把示例复制进工作区。
DEFAULT_CONFIG = os.path.join(SKILL_DIR, "config", "targets.json.example")
DEFAULT_WS = os.path.join(os.path.expanduser("~"), ".change-watch")

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

# 整条正文只剩一个 t.co 短链时的识别（X 的纯媒体帖 / X 文章都是这个形态）。
_TCO_ONLY_RE = re.compile(r"^https?://t\.co/\S+$")

# CookieJar **不是线程安全的**，并发采集时多线程共享一个 jar 会互相踩（cookie 覆盖 /
# 迭代中修改）。所以每个线程各持一份 opener：cookie 只在自己这条请求链里流转，既能
# 解决 ai.google.dev 的 302 循环，又天然无竞争。并发度见 COLLECT_* 常量。
_TLS = threading.local()


def opener():
    """浏览器头 + CookieJar：ai.google.dev 对普通 UA 会 302 循环，必须带 Cookie。"""
    op = getattr(_TLS, "opener", None)
    if op is None:
        cj = http.cookiejar.CookieJar()
        op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
        _TLS.opener = op
    return op


# 所有对外时间戳统一为北京时间（东八区），与 log_change.py 保持一致。
# 不依赖本机时区：换机器/换时区也不能让 run_at / first_seen 漂成本地时间。
BEIJING = datetime.timezone(datetime.timedelta(hours=8))


def now_utc():
    return datetime.datetime.now(datetime.timezone.utc)


def now_iso():
    return datetime.datetime.now(BEIJING).isoformat(timespec="seconds")


def parse_iso(s):
    """解析 now_iso() 产出的时间戳；脏数据/缺字段时回退当前时刻，避免崩溃或时钟漂移。"""
    try:
        return datetime.datetime.fromisoformat(s)
    except (TypeError, ValueError):
        return datetime.datetime.now(BEIJING)


def browser_headers():
    """完整 Chrome 浏览器头集合（2026-10-13 实测取证后默认启用）。

    背景：LinkedIn company 页（/company/googledeepmind、/company/google）对"裸 urllib"
    请求返回 **HTTP 999**（LinkedIn 专用反爬状态码）。两轮交错 A/B 实验（同一 IP、
    交替请求、2s 间隔，共 12 次/组）：

    - 旧头（5 个字段：UA/Accept/Accept-Language/Accept-Encoding/Cache-Control）
      → company 页 12 次请求失败 10 次（83%），showcase 页不受影响；
    - 本函数完整头 → company 页 **12/12 全成功**，且对 blog.google / developers.googleblog /
      gcp-release-notes 三个 feed 与 ai.google.dev、cloud.google 四个登记册逐次比对
      状态一致、条目数一致、字节数差 <0.1%（仅页面内动态 token），**零副作用**。

    结论：999 是"请求头不像浏览器"触发，不是 IP 级硬性封禁；补齐 sec-ch-ua /
    Sec-Fetch-* / Referer / Upgrade-Insecure-Requests 即可解，**无需引入 curl_cffi
    等第三方依赖**（保住本技能零依赖、可移植的一等需求）。curl_cffi 的 TLS 指纹
    伪装实测同样 6/6 成功，作为将来 LinkedIn 升级到 TLS 指纹检测时的备选方案记录。
    """
    return {
        "User-Agent": UA,
        "Accept": ("text/html,application/xhtml+xml,application/xml;q=0.9,"
                   "image/avif,image/webp,image/apng,*/*;q=0.8,"
                   "application/signed-exchange;v=b3;q=0.7"),
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip",
        "Cache-Control": "max-age=0",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "Referer": "https://www.google.com/",
        "DNT": "1",
        "sec-ch-ua": '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"macOS"',
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "cross-site",
        "Sec-Fetch-User": "?1",
    }


def fetch(url, timeout=30, retries=1):
    last = None
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers=browser_headers())
            with opener().open(req, timeout=timeout) as r:
                raw = r.read()
                if r.headers.get("Content-Encoding") == "gzip":
                    raw = gzip.decompress(raw)
                return r.status, raw.decode("utf-8", "replace"), r.geturl()
        except Exception as exc:  # noqa: BLE001 - 抓取失败要能继续跑其他源
            last = exc
            if attempt < retries:
                time.sleep(1.0)
    raise last


# ---------- 文本工具 ----------

_TAG = re.compile(r"<[^>]+>")
# CDATA 必须先把内容取出来再剥标签：否则 <![CDATA[...]]> 整体会被当成"标签"删掉，
# 于是所有用 CDATA 包裹的 feed（实测 developer.chrome.com 就是）标题与正文全变空字符串。
_CDATA = re.compile(r"<!\[CDATA\[(.*?)\]\]>", re.S)


def unwrap_cdata(s):
    return _CDATA.sub(r"\1", s or "")


def strip_tags(s):
    return htmlmod.unescape(_TAG.sub(" ", unwrap_cdata(s)))


def norm(s):
    """规范化：折叠空白 + 折叠连续替换字符。

    连续多个 U+FFFD 折叠成一个是必需的：某些 feed 每次加载会随机乱码几个字符，
    不折叠的话同一条目每轮指纹都不同 —— 于是每轮都报同一批"变化"，是最典型的假告警。
    """
    s = re.sub(r"�+", "�", htmlmod.unescape(s or ""))
    return re.sub(r"\s+", " ", s).strip()


def sha1(text):
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def norm_url(u):
    u = (u or "").strip()
    u = re.sub(r"#.*$", "", u)
    return u


# ---------- 解析：RSS / Atom ----------

def _tag_text(block, *names):
    for n in names:
        m = re.search(r"<%s\b[^>]*>(.*?)</%s>" % (n, n), block, re.S | re.I)
        if m:
            return unwrap_cdata(m.group(1))
    return ""


def _link_of(block):
    m = re.search(r"<link\b[^>]*href=[\"']([^\"']+)[\"']", block, re.I)
    if m:
        return m.group(1)
    return strip_tags(_tag_text(block, "link")).strip()


def parse_feed(body):
    """返回 [(key, record)]，key = 归一化链接；链接被多条共用时改用条目稳定 id。

    为什么不能只用链接：有的流多条条目共用同一个链接——GCP 发布说明流
    （cloud.google.com/feeds/gcp-release-notes.xml）30 条 entry 全部指向
    docs.cloud.google.com/release-notes。链接冲突时若按出现顺序加 #2 #3… 后缀，
    键实际退化成"位置序号"：流每日轮换（新一天插入顶部、最旧一天被挤出）时
    所有条目位置整体偏移，内容没变却整批报 changed——实测单轮 30 条 phantom
    changed + 10 条 phantom added，全是要 AI 空判的噪音。改用 Atom <id> /
    RSS <guid> 后键随条目本身稳定：轮换只体现为新条目 added 与旧条目滚出
    （windowed 源由 split_rolled_out 接住）。**链接唯一的流行为完全不变**，
    既有基线不受影响（12 个博客流逐个验证过键不变）。
    """
    blocks = re.findall(r"<(item|entry)\b[^>]*>(.*?)</\1>", body, re.S | re.I)
    link_use = {}
    for _tag, block in blocks:
        link = norm_url(_link_of(block))
        if link:
            link_use[link] = link_use.get(link, 0) + 1
    out = []
    seen = {}
    for _tag, block in blocks:
        link = norm_url(_link_of(block))
        title = norm(strip_tags(_tag_text(block, "title")))
        date = norm(_tag_text(block, "pubDate", "published", "updated", "dc:date"))
        summary = norm(strip_tags(_tag_text(block, "description", "summary",
                                                  "content:encoded", "content")))[:1200]
        if not link and not title:
            continue
        if link and link_use.get(link, 0) > 1:
            # 链接被多条共用：必须换一个随条目走的稳定标识，否则键=位置序号
            stable = norm(_tag_text(block, "id", "guid"))
            key = stable or ("title:" + sha1(title + "\n" + date))
        elif link:
            key = link
        else:
            key = "title:" + sha1(title)
        n = seen.get(key, 0)
        seen[key] = n + 1
        if n:
            key = "%s#%d" % (key, n + 1)
        out.append((key, {
            "title": title,
            "link": link,
            "published": date,
            "summary": summary,
            "text": "\n".join([title, summary, date]),
            "hash": sha1("\n".join([title, summary, date])),
        }))
    if out and all(not r.get("title") for _k, r in out):
        # 与 LinkedIn / X 一致：拿不到内容就显式报错，不要静默产出一批空条目。
        # 空条目最危险的地方是"看起来抓到了 10 条"，但差分只认 URL —— 正文改了也发现不了。
        raise ValueError("解析出 %d 个条目标题全为空（feed 改版或解析失败），拒绝当真" % len(out))
    return out


# ---------- 解析：网页表格 ----------

# 表头识别用词（有些页面用 <td> 而不是 <th> 渲染表头，必须靠文本特征兜住）
HEADER_WORDS = {
    "model", "model id", "models", "name", "description", "id", "date", "status", "feature",
    "details", "release date", "retirement date", "shutdown date", "deprecated date",
    "recommended replacement", "replacement", "replacement model id", "deprecation date",
    "模型", "模型 id", "名称", "说明", "状态", "上线日期", "下线日期", "弃用日期", "版本",
}


def looks_like_header(cells):
    """整行都是短标签、且多数命中表头词 → 判为表头。"""
    if len(cells) < 2:
        return False
    if any(len(c) > 40 for c in cells):
        return False
    hits = 0
    for c in cells:
        low = c.strip().lower()
        if low in HEADER_WORDS or low.endswith("date") or low.endswith("日期"):
            hits += 1
    return hits >= 2


def parse_tables(body, key_column=0, drop_header_like=True):
    """把页面里每张表的每一行抽成 (key, record)。key = 第 key_column 列的文本。"""
    out = []
    seen = {}
    for tbl_i, tbl in enumerate(re.findall(r"<table\b.*?</table>", body, re.S | re.I)):
        # 先摸清这张表"正常数据行有几列"（取列数的众数），用来识别**分组/小节标题行**。
        # 实测：ai.google.dev 的弃用清单里，每组模型之间夹了一行 colspan 的
        # "Preview models"（只有 1 个单元格）。它不是任何模型的记录，却被当成数据行收进来；
        # 更糟的是 13 个分组的第一列同名，解析器只能给它们加 #2 #3 … 后缀 ——
        # 页面只要新增/删除一个分组，后面所有后缀就整体错位，凭空制造一批"变化/消失"。
        # 判据：列数明显少于本表常态 + 文本很短 → colspan 小节标题，不是数据。
        # 单列名单表（typical=1）不受影响——没有行会少于 typical。
        widths, parsed = {}, []
        for row in re.findall(r"<tr\b.*?</tr>", tbl, re.S | re.I):
            if re.search(r"<th\b", row, re.I):      # 显式表头行跳过
                parsed.append(None)
                continue
            cells = [norm(_TAG.sub(" ", c)) for c in re.findall(r"<td\b.*?</td>", row, re.S | re.I)]
            parsed.append(cells)
            if cells:
                widths[len(cells)] = widths.get(len(cells), 0) + 1
        typical = max(widths.items(), key=lambda kv: (kv[1], kv[0]))[0] if widths else 0
        first_row = True
        for cells in parsed:
            if cells is None:
                first_row = False
                continue
            if drop_header_like and first_row and looks_like_header(cells):
                first_row = False
                continue                             # 隐式表头行（用 <td> 渲染的）
            first_row = False
            if not cells or key_column >= len(cells):
                continue
            # 判据收紧到"只有 1 列"：实测 gemini-api-models 里 20 条真实记录是 2 列
            # （名称 + 模型 ID，而该表典型是 3 列），用"少于典型列数"会把它们整批误删。
            # 单列名单表 typical=1，本条永不触发，也不受影响。
            if typical >= 2 and len(cells) == 1 and len(cells[0]) <= 60:
                continue                             # colspan 小节标题（如 "Preview models"）
            key = cells[key_column]
            if not key:
                continue
            n = seen.get(key, 0)
            seen[key] = n + 1
            if n:
                key = "%s#%d" % (key, n + 1)
            out.append((key, {
                "cells": cells,
                "table": tbl_i,
                "text": " | ".join(cells),
                "hash": sha1(" | ".join(cells)),
            }))
    return out


# ---------- 解析：JSON-LD 帖子（schema.org SocialMediaPosting） ----------

def _walk_json_ld(node, out):
    """递归找 SocialMediaPosting 节点（可能裸着，也可能挂在 @graph 里）。"""
    if isinstance(node, dict):
        if node.get("@type") == "SocialMediaPosting":
            out.append(node)
        for v in node.values():
            if isinstance(v, (dict, list)):
                _walk_json_ld(v, out)
    elif isinstance(node, list):
        for v in node:
            if isinstance(v, (dict, list)):
                _walk_json_ld(v, out)


def parse_json_ld_posts(body):
    """解析页面 <script type="application/ld+json"> 里的 SocialMediaPosting。

    LinkedIn 的 showcase 页免登录就内嵌这个块（全文 + ISO 时间 + 帖子 URL），
    是目前 LinkedIn 唯一稳定的免登录结构化出口。返回 [(key, record)]，key = 帖子 URL。
    """
    posts = []
    for m in re.finditer(
            r'<script\b[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
            body, re.S | re.I):
        try:
            _walk_json_ld(json.loads(m.group(1).strip()), posts)
        except ValueError:
            continue
    if not posts:
        # 关键保护：页面被登录墙拦截或改版时必须报错，
        # 否则差分会把全部帖子误判成"消失"。
        if re.search(r"authwall", body, re.I):
            raise ValueError("页面被 LinkedIn 登录墙（authwall）拦截，拿不到帖子")
        raise ValueError("页面里没有 SocialMediaPosting，可能改版或被拦截")
    out = []
    seen = {}
    for p in posts:
        link = norm_url(p.get("mainEntityOfPage") or p.get("url") or "")
        text = norm(p.get("text") or "")[:1200]
        date = norm(p.get("datePublished") or "")
        if not link and not text:
            continue
        key = link or ("text:" + sha1(text))
        n = seen.get(key, 0)
        seen[key] = n + 1
        if n:
            key = "%s#%d" % (key, n + 1)
        title = text[:80] + ("…" if len(text) > 80 else "")
        out.append((key, {
            "title": title,
            "link": link,
            "published": date,
            "summary": text,
            "text": "\n".join([text, date]),
            "hash": sha1("\n".join([text, date])),
        }))
    return out


# ---------- 解析：X（推特）时间线（guest 免登录通道） ----------

# X 网页端的公开 Bearer（浏览器里人尽皆知的常量，不是任何人的密钥）。
X_BEARER = ("AAAAAAAAAAAAAAAAAAAAANRILgAAAAAAnNwIzUejRCOuH5E6I8xnZz4puTs%3D"
            "1Zv7ttfk8LF81IUq16cHjhLTvJu4FA33AGWWjCpTnA")

# GraphQL 端点模板（含 queryId 与 features，X 轮换 queryId 时改这里或用源配置覆盖）。
# 取自开源库 @the-convocation/twitter-scraper（MIT）0.22.3，2026-10 实测可用。
X_USER_BY_SCREEN_NAME_URL = "https://api.x.com/graphql/AWbeRIdkLtqTRN7yL_H8yw/UserByScreenName?variables=%7B%22screen_name%22%3A%22elonmusk%22%2C%22withGrokTranslatedBio%22%3Atrue%7D&features=%7B%22hidden_profile_subscriptions_enabled%22%3Atrue%2C%22profile_label_improvements_pcf_label_in_post_enabled%22%3Atrue%2C%22responsive_web_profile_redirect_enabled%22%3Afalse%2C%22rweb_tipjar_consumption_enabled%22%3Afalse%2C%22verified_phone_label_enabled%22%3Afalse%2C%22subscriptions_verification_info_is_identity_verified_enabled%22%3Atrue%2C%22subscriptions_verification_info_verified_since_enabled%22%3Atrue%2C%22highlights_tweets_tab_ui_enabled%22%3Atrue%2C%22responsive_web_twitter_article_notes_tab_enabled%22%3Atrue%2C%22subscriptions_feature_can_gift_premium%22%3Atrue%2C%22creator_subscriptions_tweet_preview_api_enabled%22%3Atrue%2C%22responsive_web_graphql_skip_user_profile_image_extensions_enabled%22%3Afalse%2C%22responsive_web_graphql_timeline_navigation_enabled%22%3Atrue%7D&fieldToggles=%7B%22withPayments%22%3Afalse%2C%22withAuxiliaryUserLabels%22%3Atrue%7D"
X_USER_TWEETS_URL = "https://api.x.com/graphql/N2tFDY-MlrLxXJ9F_ZxJGA/UserTweets?variables=%7B%22userId%22%3A%2244196397%22%2C%22count%22%3A20%2C%22includePromotedContent%22%3Atrue%2C%22withQuickPromoteEligibilityTweetFields%22%3Atrue%2C%22withVoice%22%3Atrue%7D&features=%7B%22rweb_video_screen_enabled%22%3Afalse%2C%22profile_label_improvements_pcf_label_in_post_enabled%22%3Atrue%2C%22responsive_web_profile_redirect_enabled%22%3Afalse%2C%22rweb_tipjar_consumption_enabled%22%3Afalse%2C%22verified_phone_label_enabled%22%3Afalse%2C%22creator_subscriptions_tweet_preview_api_enabled%22%3Atrue%2C%22responsive_web_graphql_timeline_navigation_enabled%22%3Atrue%2C%22responsive_web_graphql_skip_user_profile_image_extensions_enabled%22%3Afalse%2C%22premium_content_api_read_enabled%22%3Afalse%2C%22communities_web_enable_tweet_community_results_fetch%22%3Atrue%2C%22c9s_tweet_anatomy_moderator_badge_enabled%22%3Atrue%2C%22responsive_web_grok_analyze_button_fetch_trends_enabled%22%3Afalse%2C%22responsive_web_grok_analyze_post_followups_enabled%22%3Atrue%2C%22responsive_web_jetfuel_frame%22%3Atrue%2C%22responsive_web_grok_share_attachment_enabled%22%3Atrue%2C%22responsive_web_grok_annotations_enabled%22%3Atrue%2C%22articles_preview_enabled%22%3Atrue%2C%22responsive_web_edit_tweet_api_enabled%22%3Atrue%2C%22graphql_is_translatable_rweb_tweet_is_translatable_enabled%22%3Atrue%2C%22view_counts_everywhere_api_enabled%22%3Atrue%2C%22longform_notetweets_consumption_enabled%22%3Atrue%2C%22responsive_web_twitter_article_tweet_consumption_enabled%22%3Atrue%2C%22tweet_awards_web_tipping_enabled%22%3Afalse%2C%22responsive_web_grok_show_grok_translated_post%22%3Atrue%2C%22responsive_web_grok_analysis_button_from_backend%22%3Atrue%2C%22post_ctas_fetch_enabled%22%3Atrue%2C%22freedom_of_speech_not_reach_fetch_enabled%22%3Atrue%2C%22standardized_nudges_misinfo%22%3Atrue%2C%22tweet_with_visibility_results_prefer_gql_limited_actions_policy_enabled%22%3Atrue%2C%22longform_notetweets_rich_text_read_enabled%22%3Atrue%2C%22longform_notetweets_inline_media_enabled%22%3Atrue%2C%22responsive_web_grok_image_annotation_enabled%22%3Atrue%2C%22responsive_web_grok_imagine_annotation_enabled%22%3Atrue%2C%22responsive_web_grok_community_note_auto_translation_is_enabled%22%3Afalse%2C%22responsive_web_enhance_cards_enabled%22%3Afalse%7D&fieldToggles=%7B%22withArticlePlainText%22%3Afalse%7D"

_X_GUEST = {"token": None}
# 并发下多个线程会同时发现 token 为空 → 各激活一个（浪费额度、加剧游客限流）。
# 加这把锁保证一次运行只激活一个并复用。
_X_TOKEN_LOCK = threading.Lock()
# X guest 通道共用一个 token、限流最严，默认**完全串行**（与并发改造前的时序等价，
# 行为差异最小）。要提速就把 X_CONCURRENCY 调大，代价是更容易撞 429。
X_CONCURRENCY = 1
_X_GQL_SEM = threading.Semaphore(X_CONCURRENCY)

# ---------- 并发采集参数（settings 可覆盖，见 ALLOWED_SETTINGS） ----------
# 全局并发度：26 个源同时打出去既没必要也容易被当成抓取行为，默认 6 个线程。
DEFAULT_COLLECT_CONCURRENCY = 6
# 单 host 并发度：同一域名最多同时几个请求。blog.google 有 4 个源、cloud.google 3 个，
# 不限流的话一轮里对同一域名打出 3~4 个并发请求，反爬概率显著上升。默认 2。
DEFAULT_HOST_CONCURRENCY = 2
# 整轮采集的壁钟上限（秒）。超过就把还没回来的源判失败并继续出结果，
# 防止某个源卡住（慢 TLS/半开连接）把整条定时任务拖死。
DEFAULT_WALL_CLOCK_SECONDS = 300.0

# guest 通道是免登录的公共接口，抖动是常态，不是我方错误。实测证据：
# 冷启动首连握手要 15~16 秒（原写死 20s 超时，余量只剩 4 秒），已真实出现过一次
# 「SSL 握手超时」把整个源判成失败、进而触发一次源健康告警。
# 所以这里单独放宽超时 + 退避重试，让偶发抖动自愈，而不是骚扰人。
X_GUEST_TIMEOUT = 30
X_RETRIES = 2          # 除首次外再试 2 次
X_RETRY_BASE_SLEEP = 1.5

# 只有这些算"值得重试的抖动"：限流、服务端错误、连接/读超时。
# 其余（比如 404 = queryId 轮换失效）重试也没用，直接抛出去让人看到。
def _x_retryable(exc):
    if isinstance(exc, urllib.error.HTTPError):
        return exc.code == 429 or 500 <= exc.code < 600
    return isinstance(exc, (urllib.error.URLError, TimeoutError, ConnectionError, OSError))


def x_guest_token(timeout=X_GUEST_TIMEOUT):
    """激活一个 guest token（一次运行内复用）。免登录，但受 X 的游客限流约束。

    并发安全：整个函数持 _X_TOKEN_LOCK，未拿到 token 的线程会等第一个线程激活完，
    而不是各自激活一个（多激活 = 白白消耗游客额度，且更容易被限流）。
    """
    with _X_TOKEN_LOCK:
        if _X_GUEST["token"] is not None:
            return _X_GUEST["token"]
        last = None
        for attempt in range(X_RETRIES + 1):
            try:
                req = urllib.request.Request(
                    "https://api.x.com/1.1/guest/activate.json",
                    data=b"", method="POST",
                    headers={"Authorization": "Bearer " + X_BEARER, "User-Agent": UA})
                with urllib.request.urlopen(req, timeout=timeout) as r:
                    _X_GUEST["token"] = json.loads(r.read().decode("utf-8"))["guest_token"]
                return _X_GUEST["token"]
            except Exception as exc:  # noqa: BLE001 - 逐个记录，最后一次抛给调用方
                last = exc
                if attempt < X_RETRIES and _x_retryable(exc):
                    time.sleep(X_RETRY_BASE_SLEEP * (attempt + 1))
                    continue
                break
        raise last


def x_gql(endpoint, timeout, **var_over):
    """按模板调 X 的 GraphQL，覆盖 variables 里的指定键，返回解析后的 JSON。

    并发安全：X 的 guest 通道共用 token 且游客限流最严，这里用信号量把 X 请求串行化
    （X_CONCURRENCY，默认 1）。其它源的并发不受它影响。
    """
    with _X_GQL_SEM:
        return _x_gql_impl(endpoint, timeout, **var_over)


def _x_gql_impl(endpoint, timeout, **var_over):
    """x_gql 的实现体（调用方已持有 X 并发信号量）。"""
    u = urllib.parse.urlsplit(endpoint)
    q = dict(urllib.parse.parse_qsl(u.query, keep_blank_values=True))
    variables = json.loads(q.get("variables", "{}"))
    variables.update(var_over)
    url = urllib.parse.urlunsplit((
        u.scheme, u.netloc, u.path,
        urllib.parse.urlencode({"variables": json.dumps(variables, separators=(",", ":")),
                                "features": q.get("features", "{}")}), ""))
    last = None
    for attempt in range(X_RETRIES + 1):
        try:
            req = urllib.request.Request(url, headers={
                "Authorization": "Bearer " + X_BEARER,
                "User-Agent": UA,
                "x-guest-token": x_guest_token(),
                "x-twitter-active-user": "yes",
                "x-twitter-client-language": "en",
                "Accept": "*/*",
            })
            with urllib.request.urlopen(req, timeout=timeout) as r:
                data = json.loads(r.read().decode("utf-8"))
            if isinstance(data, dict) and data.get("errors"):
                raise ValueError("X GraphQL 返回错误: %s" % data["errors"][0].get("message", data["errors"]))
            return data
        except urllib.error.HTTPError as exc:
            last = exc
            # 401/403 多半是 guest token 被回收：作废重取一个再试，而不是整源判死。
            if exc.code in (401, 403) and attempt < X_RETRIES:
                _X_GUEST["token"] = None
                time.sleep(X_RETRY_BASE_SLEEP * (attempt + 1))
                continue
            if _x_retryable(exc) and attempt < X_RETRIES:
                time.sleep(X_RETRY_BASE_SLEEP * (attempt + 1))
                continue
            raise
        except Exception as exc:  # noqa: BLE001
            last = exc
            if _x_retryable(exc) and attempt < X_RETRIES:
                time.sleep(X_RETRY_BASE_SLEEP * (attempt + 1))
                continue
            raise
    raise last


def parse_x_timeline(data, screen_name, skip_retweets=True):
    """解析 UserTweets 响应为 [(key, record)]。

    条目同时出现在两种容器里：TimelineTimelineItem（单推）与
    TimelineTimelineModule（会话线程，推文嵌在 content.items[] 里）。
    拿不到任何推文时显式报错（防把接口故障误判成"全部推文消失"）。

    三类特殊推文（均为 2026-10-14 实测过的真实案例）：
    1. **长文（note tweet）**：`legacy.full_text` 只是截断的展示版（约 280 字），
       完整正文在 `note_tweet.note_tweet_results.result.text` 里。不读它会静默丢
       内容——实测 Gemini 4 Argon 定价公告的后半段（$2/$10 每百万 token）
       就只在 note 里，full_text 到 "Argon will launch" 就被砍断。
    2. **X 文章（article）**：正文只剩一条指向 `x.com/i/article/…` 的短链，
       全文不在本接口内，但推文自带 `article` 对象，有 `title` + `preview_text`，
       足够判断（实测 @GoogleCloudTech 的长文仅凭这两项就能定性）。
    3. **纯媒体帖（视频/图片）**：正文也只剩一条短链，但解析后指向本条推文
       自己的媒体附件，没有任何可读文本。按用户约定**直接忽略**：不进基线、
       不产生变更、不占判断配额（曾每天制造 3 条"待确认"噪音）。
    """
    tweets = []
    try:
        instructions = data["data"]["user"]["result"]["timeline"]["timeline"]["instructions"]
    except (KeyError, TypeError):
        raise ValueError("UserTweets 响应结构异常（guest 通道被拦或接口改版）")
    for ins in instructions:
        for entry in ins.get("entries", []):
            content = entry.get("content", {})
            slots = []
            if content.get("itemContent"):
                slots.append(content["itemContent"])
            for item in content.get("items", []):
                ic = item.get("item", {}).get("itemContent")
                if ic:
                    slots.append(ic)
            for ic in slots:
                res = ic.get("tweet_results", {}).get("result", {})
                if res.get("__typename") == "TweetWithVisibilityResults":
                    res = res.get("tweet", {})
                legacy = res.get("legacy")
                if legacy:
                    tweets.append((legacy, res))
    if not tweets:
        raise ValueError("时间线 0 条推文（guest 通道被拦或账号异常），拒绝当真")
    import email.utils
    out = []
    for lg, res in tweets:
        text = norm(lg.get("full_text") or "")
        # 1) 长文：完整正文以 note_tweet 为准（legacy.full_text 是截断版）
        note = (res.get("note_tweet", {}).get("note_tweet_results", {})
                    .get("result", {}) or {})
        note_text = norm(note.get("text") or "")
        if note_text:
            text = note_text
        # 2) 正文只剩一条短链：X 文章 / 纯媒体帖
        if _TCO_ONLY_RE.match(text.strip()):
            ents = lg.get("entities", {}) or {}
            media = ents.get("media", []) or []
            urls = ents.get("urls", []) or []
            article_url = next(
                (u.get("expanded_url") for u in urls
                 if "x.com/i/article/" in (u.get("expanded_url") or "")), "")
            if media and not article_url:
                continue                    # 纯媒体帖：无可读正文，按约定忽略
            art = (res.get("article", {}).get("article_results", {})
                       .get("result", {}) or {})
            art_title = norm(art.get("title") or "")
            if art_title:
                art_prev = norm(art.get("preview_text") or "")
                text = art_title + (("\n\n" + art_prev) if art_prev else "")
                if article_url:
                    text += "\n" + article_url
        if not text:
            continue
        if skip_retweets and text.startswith("RT @"):
            continue
        tid = str(lg.get("id_str") or lg.get("id") or "")
        if not tid:
            continue
        link = "https://x.com/%s/status/%s" % (screen_name, tid)
        try:
            pub = email.utils.parsedate_to_datetime(lg.get("created_at", ""))
            published = pub.astimezone(datetime.timezone.utc).isoformat()
        except Exception:  # noqa: BLE001
            published = ""
        out.append((link, {
            "title": text[:80] + ("…" if len(text) > 80 else ""),
            "link": link,
            "published": published,
            "summary": text[:1200],
            "text": text,
            "hash": sha1(text),
        }))
    return out


# ---------- 状态读写 ----------

def jload(path, default):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return default


def jdump(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2, sort_keys=True)
    os.replace(tmp, path)


# ---------- 源健康（连续失败计数：区分"偶发抖动"与"真失联"） ----------

# 连续失败达到这个次数，判定为"持续失联"（疑似真故障），需要点名提醒用户。
# 刻意**不做时间退避**：本技能按小时/天级别运行，源侧分钟级退避没有意义
# （下次运行本来就隔了数小时）。真正有价值的是"计数 + 分级 + 只在状态跃迁时提醒"，
# 这样既不重复刷同一条失败噪音，又能在恢复时告诉你一声。
DOWN_THRESHOLD = 3


def load_health(ws):
    return jload(os.path.join(ws, "state", "health.json"), {})


def save_health(ws, health):
    jdump(os.path.join(ws, "state", "health.json"), health)


def update_health(health, key, ok, error, now):
    """更新一个源的健康记录，返回 (记录, 跃迁描述或 None)。"""
    rec = health.setdefault(key, {
        "consecutive_failures": 0, "last_ok_at": None,
        "last_error": None, "last_error_at": None, "state": "ok",
        "first_failed_at": None,
    })
    old_state = rec.get("state", "ok")
    if ok:
        rec["consecutive_failures"] = 0
        rec["last_ok_at"] = now
        rec["state"] = "ok"
        rec["first_failed_at"] = None
    else:
        if old_state == "ok":
            # 只在这里记第一次失败的时间：重复告警时沿用，消息里才能说"已经卡了多久"
            rec["first_failed_at"] = now
        rec["consecutive_failures"] = int(rec.get("consecutive_failures", 0)) + 1
        rec["last_error"] = error
        rec["last_error_at"] = now
        rec["state"] = "down" if rec["consecutive_failures"] >= DOWN_THRESHOLD else "flapping"
    transition = None
    if rec["state"] != old_state:
        transition = "%s → %s" % (old_state, rec["state"])
    return rec, transition


def stale_threshold(spec, settings):
    """按源取停更阈值（小时）；返回 None 表示该源不检查停更。

    源配置里的 `stale_after_hours` 优先于全局同名字段；
    显式写 `null` 表示这个源本来就不常更新，不要告警（如弃用清单、低频账号）。
    """
    v = spec.get("stale_after_hours", settings.get("stale_after_hours", 168))
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return float(settings.get("stale_after_hours", 168))


# 声称时间比现在晚超过这么多小时，就当它没给时间（服务器时间漂移 / 故意的未来戳）。
# 不这样做会让"最新条目"永远是一个未来时间戳的条目，停更告警也就永远不会触发。
FUTURE_TOLERANCE_HOURS = 1.0


def hours_since(iso_text):
    if not iso_text:
        return None
    txt = iso_text.strip()
    try:
        if re.match(r"^\d{4}-\d{2}-\d{2}$", txt):
            dt = datetime.datetime.fromisoformat(txt).replace(tzinfo=datetime.timezone.utc)
        else:
            import email.utils
            if re.match(r"^\d{4}-\d{2}-\d{2}[T ]", txt):
                v = txt.replace(" ", "T")
                if re.search(r"[+-]\d{4}$", v):
                    dt = datetime.datetime.strptime(v[:19] + v[-5:], "%Y-%m-%dT%H:%M:%S%z")
                else:
                    dt = datetime.datetime.fromisoformat(v.replace("Z", "+00:00"))
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=datetime.timezone.utc)
            else:
                dt = email.utils.parsedate_to_datetime(txt)
        h = (now_utc() - dt.astimezone(datetime.timezone.utc)).total_seconds() / 3600.0
        return None if h < -FUTURE_TOLERANCE_HOURS else h
    except Exception:  # noqa: BLE001 - 时间不可解析时不做判断
        return None


# ---------- 配置校验（未知键直接失败，不静默忽略） ----------

# 不认识的键一律报错。理由：静默忽略会让配置漂移变成"看起来在跑、其实没生效"——
# 例如把 stale_after_hours 拼错，源会一直用默认阈值告警，而日志上完全看不出来。
# heartbeat: 巡检心跳节流配置（log_change.py --heartbeat 读取），与 README/config 模板一致。
ALLOWED_TOP = {"settings", "targets", "version", "notify", "digest", "heartbeat"}
ALLOWED_SETTINGS = {"stale_after_hours", "request_timeout_seconds",
                    "request_retries", "min_changes_to_notify",
                    # 并发采集：全局并发度 / 单域名并发度 / 整轮壁钟上限（秒）。
                    # collect_concurrency=1 即退回串行（排查问题时用它做对照）。
                    "collect_concurrency", "host_concurrency",
                    "collect_wall_clock_seconds"}
ALLOWED_TARGET = {"id", "name", "short_name", "notes", "streams", "registries"}
ALLOWED_SOURCE_COMMON = {"id", "label", "kind", "url", "notes",
                         "timeout_seconds", "stale_after_hours",
                         # windowed: 该源只暴露"最近 N 条"窗口（X 时间线、LinkedIn 展示页等）。
                         # 开了它，从窗口底部掉出去的旧条目会被识别为滚动而非"内容消失"。
                         "windowed"}
ALLOWED_SOURCE_BY_KIND = {
    "rss": set(),
    "html_tables": {"key_column", "drop_header_like"},
    "json_ld_posts": set(),
    "x_timeline": {"account", "count", "skip_retweets",
                   "user_tweets_url", "user_by_screen_name_url"},
}


def validate_config(cfg):
    """返回问题列表；非空就应当中止运行（配置错了，跑出来的结果不可信）。"""
    problems = []

    def check(node, allowed, where):
        if not isinstance(node, dict):
            problems.append("%s 应该是对象" % where)
            return
        for k in node:
            if k.startswith("_"):       # 注释字段，随便写
                continue
            if k not in allowed:
                problems.append("%s 出现未知字段 %r（允许：%s）"
                                % (where, k, "/".join(sorted(allowed))))
        return

    check(cfg, ALLOWED_TOP, "配置顶层")
    check(cfg.get("settings", {}), ALLOWED_SETTINGS, "settings")

    targets = cfg.get("targets")
    if not isinstance(targets, list) or not targets:
        problems.append("targets 必须是非空数组")
        return problems
    for t in targets:
        if not isinstance(t, dict):
            problems.append("targets 里有非对象元素")
            continue
        tid = t.get("id", "?")
        check(t, ALLOWED_TARGET, "目标 %s" % tid)
        for field in ("streams", "registries"):
            for spec in t.get(field, []) or []:
                if not isinstance(spec, dict):
                    problems.append("目标 %s 的 %s 里有非对象元素" % (tid, field))
                    continue
                sid = spec.get("id", "?")
                kind = spec.get("kind", "rss")
                if kind not in ALLOWED_SOURCE_BY_KIND:
                    problems.append("源 %s 的 kind=%r 不支持（允许：%s）"
                                    % (sid, kind, "/".join(sorted(ALLOWED_SOURCE_BY_KIND))))
                    continue
                allowed = ALLOWED_SOURCE_COMMON | ALLOWED_SOURCE_BY_KIND[kind]
                check(spec, allowed, "源 %s/%s" % (tid, sid))
                if not spec.get("url"):
                    problems.append("源 %s 缺少 url" % sid)
                if not spec.get("id"):
                    problems.append("目标 %s 的某个源缺少 id" % tid)
    return problems


# ---------- 变更身份与待判队列 ----------

# 每条变更一个稳定 id，模型必须原样带回。有了它才能机械地校验"判断覆盖完整"。
def change_id(target, layer, source_id, kind, key):
    return sha1("|".join([str(target), layer, str(source_id), kind, str(key)]))[:12]


# 未判变更按"年龄"过期，而不是按 watch 运行次数 —— 这样采集频率（cron 可高频）
# 与判断频率（AI 可低频）彻底解耦：采集每 15min 也不会把还没判的变更提前吃掉。
# 设为 24h：即便判断任务偶发滞后也留足重判窗口。超时仍单独留痕，不允许悄悄消失。
MAX_PENDING_AGE_H = 24


# ---------- 主流程 ----------

def newest_published(pairs):
    """返回 (最新条目距今小时数, 最新条目的 published 值)。"""
    best_h, best_v = None, None
    for _k, rec in pairs:
        h = hours_since(rec.get("published"))
        if h is not None and (best_h is None or h < best_h):
            best_h, best_v = h, rec.get("published")
    return (round(best_h, 1) if best_h is not None else None), best_v


def new_status(target_id, spec, layer):
    """一个源的空白状态骨架（正常采集、异常兜底、超时兜底共用）。"""
    return {
        "target": target_id,
        "layer": layer,
        "source_id": spec["id"],
        "label": spec.get("label", spec["id"]),
        "kind": spec.get("kind", "rss"),
        "url": spec["url"],
        "ok": False,
        "http": None,
        "error": None,
        "items": 0,
        "newest_item": None,
        "stale_hours": None,
        "stale": False,
    }


def fail_status(target_id, spec, layer, error):
    """构造一个"这个源没抓到"的状态（error 已是人类可读的一句话）。"""
    st = new_status(target_id, spec, layer)
    st["error"] = error
    return st


_HOST_SEMS = {}
_HOST_SEMS_LOCK = threading.Lock()


def host_semaphore(host, limit):
    """同一域名的并发闸（limit<=0 表示不限流）。

    一轮里对同一域名打出 3~4 个并发请求，被当成抓取行为的概率明显上升
    （blog.google 4 个源、cloud.google.com 3 个源、linkedin.com 3 个源）。
    默认每域名最多 2 个并发：既有提速，又不至于像爬虫。
    """
    if not limit or limit <= 0:
        return None
    with _HOST_SEMS_LOCK:
        s = _HOST_SEMS.get(host)
        if s is None:
            s = threading.Semaphore(int(limit))
            _HOST_SEMS[host] = s
        return s


def collect_source(cfg_settings, layer, target_id, spec, host_concurrency=0):
    """抓一个源，返回 (status_dict, items_map)。异常不抛出，记进 status。

    host_concurrency>0 时限制同一域名的并发数（只在并发采集时传入）。
    """
    st = new_status(target_id, spec, layer)
    try:
        kind = spec.get("kind", "rss")
        timeout = int(spec.get("timeout_seconds",
                               cfg_settings.get("request_timeout_seconds", 30)))
        if kind == "x_timeline":
            # X guest 免登录通道：两步 GraphQL（查 userId → 拉时间线），不走通用 fetch。
            account = spec.get("account") or re.sub(r"^https?://(www\.)?(x|twitter)\.com/@?", "",
                                                    spec.get("url", ""), flags=re.I).strip("/ ")
            if not account:
                raise ValueError("x_timeline 源缺少 account 或有效的 url")
            prof = x_gql(spec.get("user_by_screen_name_url", X_USER_BY_SCREEN_NAME_URL),
                         timeout, screen_name=account)
            uid = prof["data"]["user"]["result"]["rest_id"]
            data = x_gql(spec.get("user_tweets_url", X_USER_TWEETS_URL),
                         timeout, userId=uid,
                         count=int(spec.get("count", 20)))
            pairs = parse_x_timeline(data, account,
                                     bool(spec.get("skip_retweets", True)))
            st["http"] = 200
            st["final_url"] = "https://x.com/%s" % account
            st["stale_hours"], st["newest_item"] = newest_published(pairs)
            st["ok"] = True
            st["items"] = len(pairs)
            return st, dict(pairs)
        # 域名闸只做**有限等待**：一个源卡住（半开连接、慢 TLS）会一直占着闸位，
        # 同域名的其它源便全被拖到超时，一个慢源变成一批源失败。等 timeout 这么久
        # 还没轮到就不再排队 —— 限流是软约束，不能反过来把可用性拖垮。
        sem = host_semaphore(urllib.parse.urlsplit(spec["url"]).netloc, host_concurrency)
        if sem is not None and not sem.acquire(timeout=max(1.0, float(timeout))):
            sem = None
        try:
            code, body, final = fetch(spec["url"],
                                      timeout=timeout,
                                      retries=int(cfg_settings.get("request_retries", 1)))
        finally:
            if sem is not None:
                sem.release()
        st["http"] = code
        if kind == "rss":
            pairs = parse_feed(body)
            st["stale_hours"], st["newest_item"] = newest_published(pairs)
        elif kind == "html_tables":
            pairs = parse_tables(body, int(spec.get("key_column", 0)),
                                 bool(spec.get("drop_header_like", True)))
        elif kind == "json_ld_posts":
            pairs = parse_json_ld_posts(body)
            st["stale_hours"], st["newest_item"] = newest_published(pairs)
        else:
            raise ValueError("未知 kind: %r" % kind)
        st["ok"] = True
        st["items"] = len(pairs)
        st["final_url"] = final
        return st, dict(pairs)
    except Exception as exc:  # noqa: BLE001
        st["error"] = "%s: %s" % (type(exc).__name__, exc)
        return st, {}


def _num_setting(settings, key, default, lo, hi):
    """读数值型 setting：写错不崩，降级为默认并在 stderr 点名（静默忽略会变成"没生效"）。"""
    raw = settings.get(key, default)
    if raw is None:
        return default
    try:
        v = type(default)(raw)
    except (TypeError, ValueError):
        print("settings.%s=%r 不是数字，按默认 %s 处理" % (key, raw, default), file=sys.stderr)
        return default
    if v < lo or v > hi:
        print("settings.%s=%s 超出 [%s, %s]，按默认 %s 处理"
              % (key, v, lo, hi, default), file=sys.stderr)
        return default
    return v


def collect_all(settings, jobs):
    """并发采集 jobs（[(tid, layer, spec), ...]），返回**与 jobs 同序**的 [(st, items)]。

    三条硬约束（都是为了"并发不改变结果"）：

    1. **结果必须按配置顺序返回**：stdout 与 latest.json 的 sources 顺序不随完成情况
       抖动，diff 出来的内容才可复现、可对照。
    2. **daemon 工作线程**：整轮超时后进程能立刻退出。刻意不用 ThreadPoolExecutor ——
       它的线程是非 daemon 的，解释器退出时会 join，卡住的源会把定时任务整体拖死，
       而这正是加壁钟上限想要避免的事。
    3. **单源异常 / 超时不外溢**：任何一个源的问题只体现为它自己 ok=False，
       其余源照常出结果（超时的记为 TimeoutError，进入健康计数，不会静默消失）。
    """
    n = len(jobs)
    results = [None] * n
    if n == 0:
        return []
    concurrency = _num_setting(settings, "collect_concurrency",
                               DEFAULT_COLLECT_CONCURRENCY, 1, 32)
    host_conc = _num_setting(settings, "host_concurrency",
                             DEFAULT_HOST_CONCURRENCY, 0, 16)
    wall = _num_setting(settings, "collect_wall_clock_seconds",
                        DEFAULT_WALL_CLOCK_SECONDS, 5.0, 3600.0)
    if n == 1 or concurrency <= 1:
        for i, (tid, layer, spec) in enumerate(jobs):
            try:
                results[i] = collect_source(settings, layer, tid, spec)
            except Exception as exc:  # noqa: BLE001 - collect_source 已兜底，这里是双保险
                results[i] = (fail_status(tid, spec, layer, "WorkerError: %s" % exc), {})
        return results

    q = queue.Queue()
    for i, job in enumerate(jobs):
        q.put((i,) + tuple(job))

    def _worker():
        while True:
            try:
                i, tid, layer, spec = q.get_nowait()
            except queue.Empty:
                return
            try:
                results[i] = collect_source(settings, layer, tid, spec, host_conc)
            except Exception as exc:  # noqa: BLE001
                results[i] = (fail_status(tid, spec, layer, "WorkerError: %s" % exc), {})

    threads = [threading.Thread(target=_worker, name="cw-collect-%d" % k, daemon=True)
               for k in range(min(concurrency, n))]
    for t in threads:
        t.start()
    # 轮询而不是无脑 join 到 deadline：绝大多数轮次里所有源都会提前跑完，
    # 这时应当立刻往下走；只有真有源卡住时才耗到壁钟上限。
    deadline = time.monotonic() + wall
    while time.monotonic() < deadline:
        if all(r is not None for r in results):
            break
        time.sleep(0.05)
    for i in range(n):
        if results[i] is None:
            tid, layer, spec = jobs[i]
            results[i] = (fail_status(tid, spec, layer,
                                      "TimeoutError: 整轮采集超过 %.0fs 仍未返回" % wall), {})
    return results


# ---------- 单实例锁（两个 watch 同时跑会互相踩状态） ----------

_LOCK_FH = None


def acquire_run_lock(ws):
    """同一工作区同时只允许一轮 watch 在跑。返回 (ok, 说明)。

    两轮并发会各自读旧基线、各自写 latest.json / pending.json，结果是谁的变更集都不完整
    （且互相覆盖），是最难排查的一类"看起来在跑、其实漏了"。所以拿不到锁就**直接退出**，
    而不是继续跑：跳过一轮的代价远小于状态错乱。
    """
    global _LOCK_FH
    if fcntl is None:
        return True, "（当前平台没有 flock，跳过单实例锁）"
    path = os.path.join(ws, "state", "watch.lock")
    try:
        fh = open(path, "a+")
    except OSError as exc:
        return True, "（无法创建锁文件 %s，跳过单实例锁：%s）" % (path, exc)
    try:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        try:
            fh.close()
        except OSError:
            pass
        return False, "另一轮 watch 正在运行（锁 %s 未释放），本轮跳过，避免两轮互相覆盖状态" % path
    _LOCK_FH = fh          # 必须保持引用：fd 被 GC 关闭 = 锁提前释放
    return True, ""


def same_barring_loss(a, b):
    """两边等长、且每位相同或其中一边是 U+FFFD → 视为相同。

    有些 feed 每次加载会随机乱码几个字符（同一个字节这次解出来、下次解不出来），
    只看哈希的话每一轮都会报同一批"变化"，是最典型的假告警。
    前提是长度不变——所以脚本里还要把连续多个 U+FFFD 折叠成一个（见 norm()）。
    """
    if not a or not b:
        return False
    if a == b:
        return True
    if len(a) != len(b):
        return False
    return all(x == y or x == "�" or y == "�" for x, y in zip(a, b))


def effectively_same(old, new):
    """哈希相同，或"只差乱码"→ 都不算变化。老快照没有 text 字段时退回比哈希。"""
    if old.get("hash") == new.get("hash"):
        return True
    return same_barring_loss(old.get("text"), new.get("text"))


def fell_off_bottom(rec, curr_items):
    """消失的条目是否早于当前窗口里最旧的条目 —— 也就是从窗口底部掉出去的。

    比不出时间（缺 published / 格式不可解析）一律返回 False：
    分不清就按"真的消失了"处理，宁可多报一条，也不能把真下架当成滚动吃掉。
    """
    gone = hours_since(rec.get("published"))
    if gone is None:
        return False
    ages = [a for a in (hours_since(v.get("published")) for v in curr_items.values())
            if a is not None]
    return bool(ages) and gone > max(ages)


def split_rolled_out(spec, added, removed, curr_items, prev_items=None):
    """从 removed 里分出「窗口滚动」的部分，返回 (真消失, 滚动)。

    判为滚动的两类情形（都要求源显式声明 windowed，且都要求消失条目
    早于当前窗口最旧条目 —— 即"从底部掉出去"，不是被删）：

      A) 本轮有新条目进来 —— 窗口被新内容往前推，最旧几条被挤出（原逻辑）。
      B) 窗口自身收窄（2026-10-13 实测新增）—— 有限窗口流每次返回的条数并不固定：
         实测 X guest 通道抓 @Google，上一轮 15 条、下一轮 12 条，本轮**没有任何
         新条目**，最旧 3 条仍会"消失"。证据：那条推文发布于 2026-10-07T16:46，
         早于本轮窗口最旧项 10-08T14:35，且推文 URL 仍可访问 —— 是窗口抖动而非删帖。
         此类只在"本轮条数确实少于上轮"（窗口真的变窄）时才认，否则仍当真消失。

    保守边界不变：任何一条不满足，仍按真消失处理（宁可多报，漏报代价更大）。
    """
    if not spec.get("windowed") or not removed:
        return removed, []
    rolled, kept = [], []
    for item in removed:
        (rolled if fell_off_bottom(item[1], curr_items) else kept).append(item)
    if added:
        return kept, rolled                       # 情形 A
    if prev_items is not None and len(curr_items) < len(prev_items):
        return kept, rolled                       # 情形 B：窗口收窄
    return removed, []                            # 窗口没收窄却少了条目 → 真消失


def diff_map(prev, curr):
    added, removed, changed = [], [], []
    for key, rec in curr.items():
        if key not in prev:
            added.append((key, rec, None))
        elif not effectively_same(prev[key], rec):
            changed.append((key, rec, prev[key]))
    for key, rec in prev.items():
        if key not in curr:
            removed.append((key, rec, None))
    return added, removed, changed


# out/ 里每轮落一份快照、每次判断落一份卡片，无人值守跑几个月会堆出上千个文件。
# 按前缀各留最近这么多份；latest.json 与 notify-unsent-* 不动（那些要人工看）。
KEEP_OUT_FILES = 40


def _out_family(name):
    """返回要按族保留最近 N 份的文件族名；不匹配轮转规则返回 None。

    注意：按目标发的卡命名是 card-<target>-<stamp>.json、群聊强提醒卡是
    card-<target>-<stamp>-group.json，二者都带 target 前缀，不能套用
    ^card-\\d{8} 这种只认数字的旧正则（否则这些最常用的卡永远不会被清理，
    长期运行 out/ 会堆出成千上万个文件）。统一归到 'card' 族一起轮转。
    """
    if re.match(r"^(baseline|changes|card-status|notify)-\d{8}-\d{6}\.(json|md)$", name):
        return name.split("-")[0]
    if re.match(r"^card-[A-Za-z0-9_-]+-\d{8}-\d{6}(?:-group)?\.json$", name):
        return "card"
    return None


def prune_out(ws):
    d = os.path.join(ws, "out")
    try:
        names = os.listdir(d)
    except OSError:
        return
    groups = {}
    for n in names:
        fam = _out_family(n)
        if fam:
            groups.setdefault(fam, []).append(n)
    for items in groups.values():
        for n in sorted(items)[:-KEEP_OUT_FILES]:
            try:
                os.remove(os.path.join(d, n))
            except OSError:
                pass


NOTIFY_PLACEHOLDER = "REPLACE_WITH_YOUR_GROUP_CHAT_ID"


def _check_python():
    v = sys.version_info
    return (v.major, v.minor) >= (3, 8), "%d.%d.%d" % (v.major, v.minor, v.micro)


def _probe(url, timeout=8):
    import urllib.request
    try:
        req = urllib.request.Request(
            url, method="GET",
            headers={"User-Agent": "Mozilla/5.0 (change-watch init probe)"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            n = len(r.read(1024))
        return True, n, None
    except Exception as e:  # noqa: BLE001
        return False, 0, str(e)[:140]


def _detect_sender():
    return shutil.which("lark-cli")


# ---------------------------------------------------------------------------
# 推送目标自动解析（init 用：不用用户手动粘贴/查找飞书 ID）
# ---------------------------------------------------------------------------

def _run_lark(args, fmt=True):
    """运行 lark-cli 并返回解析后的 JSON dict；任何失败返回 None。

    fmt=True 时追加 `--format json`；`auth status` 等不接受该参数的子命令传 fmt=False。
    """
    lc = shutil.which("lark-cli")
    if not lc:
        return None
    cmd = [lc] + list(args)
    if fmt:
        cmd += ["--format", "json"]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    except Exception:  # noqa: BLE001
        return None
    if out.returncode != 0:
        return None
    try:
        return json.loads(out.stdout)
    except ValueError:
        return None


def _resolve_self():
    """从 lark-cli auth status 取当前用户身份（openId + 昵称）。取不到返回 (None, None)。"""
    d = _run_lark(["auth", "status"], fmt=False)
    if not d:
        return None, None
    user = (d.get("identities") or {}).get("user") or {}
    oid = user.get("openId")
    name = user.get("userName") or user.get("open_id")
    return (oid or None, name or None)


def _resolve_groups(query):
    """按群名关键词搜索，返回 [(chat_id, name), ...]。"""
    d = _run_lark(["im", "+chat-search", "--query", query])
    if not d:
        return []
    chats = ((d.get("data") or {}).get("chats") or [])
    res = []
    for c in chats:
        cid = c.get("chat_id")
        if cid:
            res.append((cid, c.get("name") or cid))
    return res


def _resolve_users(query):
    """按关键词搜用户，返回 [(open_id, name), ...]。"""
    d = _run_lark(["contact", "+search-user", "--as", "user", "--query", query])
    if not d:
        return []
    users = ((d.get("data") or {}).get("users") or [])
    res = []
    for u in users:
        oid = u.get("open_id")
        if oid:
            res.append((oid, u.get("localized_name") or u.get("name") or oid))
    return res


def _read_cfg(cfg_path):
    try:
        with open(cfg_path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def _update_notify(cfg_path, patch):
    """把 patch 合并进 targets.json 的 notify 块并写回；保留其它字段。返回是否成功。"""
    cfg = _read_cfg(cfg_path)
    if cfg is None:
        return False
    n = cfg.get("notify")
    if not isinstance(n, dict):
        n = {}
    n.update(patch)
    cfg["notify"] = n
    try:
        with open(cfg_path, "w", encoding="utf-8") as fh:
            json.dump(cfg, fh, ensure_ascii=False, indent=2)
        return True
    except OSError:
        return False


def _emit_notify_setup_needed(ws, needs):
    """脚本无法自助解析推送目标 ID 时，产出结构化待办，供 AI / 宿主工具接手查询。

    needs: list of {key, what, how} —— 缺哪些 ID、各自怎么查。
    不抛错、不阻塞：把「需要什么 + 怎么查 + 怎么写回」落盘并提示。
    """
    if not needs:
        return
    d = os.path.join(ws, "out")
    try:
        os.makedirs(d, exist_ok=True)
    except OSError:
        return
    payload = {
        "generated_at": now_iso(),
        "needs": needs,
        "how_to_apply": (
            "用你环境可用的飞书通道（连接器 / lark-cli / 其他）查到对应 ID 后，运行 "
            "`watch.py --set-user-id=<openId> --set-chat-id=<chat_id> --workspace <ws>` 写回，"
            "或直接编辑 <ws>/targets.json 的 notify 块（user_id=私信 openId，chat_id=群）。"
        ),
    }
    path = os.path.join(d, "notify-setup-needed.json")
    try:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
        print("   已生成待办文件（供 AI / 你接手查询）：%s" % path)
    except OSError:
        pass


def _ask(prompt, default):
    try:
        v = input(prompt).strip()
    except (EOFError, KeyboardInterrupt):
        v = ""
    if not v and default is not None:
        return default
    return v


def _confirm(prompt):
    try:
        v = input(prompt).strip().lower()
    except (EOFError, KeyboardInterrupt):
        return False
    return v in ("y", "yes", "是")


def _interactive_notify_setup(cfg_path, ws):
    """交互式确认推送目标，并尽量用本机可用通道解析飞书 ID。

    设计边界（关键）：实际查询 ID 的通道由 AI / 宿主环境决定（飞书连接器 /
    lark-cli / 其他）。本函数只负责「确认意图 + 在可用时自助尝试解析 + 查不到时
    产出待办与指引」，任何情况下都不强制绑定 lark-cli，查不到也不报错/阻塞。
    """
    print()
    print("=== 推送目标配置 ===")
    print("说明：实际查询飞书 ID 的通道由你（AI）依当前环境决定——可用飞书连接器、")
    print("  本机 lark-cli 或其他方式。脚本只会在「你允许且可用时」尝试用 lark-cli 解析；")
    print("  查不到则请你/AI 用别的方式查到后写回（见末尾指引）。")

    lc = _detect_sender()
    oid, name = (None, None)
    if lc:
        oid, name = _resolve_self()  # 便利层：本机 lark-cli 能读就先读
    if oid:
        print("本机 lark-cli 可读到你的飞书账号：%s（openId %s）" % (name, oid))
    elif lc:
        print("[INFO] 本机有 lark-cli 但读不到你的飞书身份（未登录？）。")
    else:
        print("[INFO] 当前环境未检测到 lark-cli；将用「外部查询」方式配置（由你/AI 用可用通道查）。")

    print("你希望把监控通知推送到哪里？")
    print("  1) 私信给我自己")
    print("  2) 发到指定群（按群名搜索）")
    print("  3) 两者都要")
    print("  4) 暂不配置（之后手动改 targets.json 的 notify 块）")
    choice = _ask("请选择 [1-4，默认 4]：", "4")
    patch = {}
    needs = []  # 需要外部查询的 ID

    if choice in ("1", "3"):
        if oid and lc:
            if _confirm("确认把私信发给自己（%s）？[y/N]：" % name):
                patch["user_id"] = oid
            else:
                print("  跳过私信配置。")
        else:
            needs.append({"key": "user_id", "what": "私信目标 openId（你自己）",
                          "how": "用飞书连接器或 lark-cli auth status 取自己的 open_id"})
            print("  ⟳ 需要你的 openId：请用环境可用的飞书通道查到后写回（末尾有方法）。")

    if choice in ("2", "3"):
        q = _ask("请输入要推送的群名称（关键词）：", None)
        if q:
            if lc:
                groups = _resolve_groups(q)
                if not groups:
                    needs.append({"key": "chat_id", "what": "群「%s」的 chat_id" % q,
                                  "how": "用飞书连接器或 lark-cli im +chat-search --query <群名> 查"})
                    print("  ⟳ 未搜到名称含「%s」的群，请用飞书通道查到 chat_id 后写回。" % q)
                elif len(groups) == 1:
                    cid, gname = groups[0]
                    if _confirm("确认发到群「%s」(%s)？[y/N]：" % (gname, cid)):
                        patch["chat_id"] = cid
                        patch["mention"] = "all"
                else:
                    print("  搜到多个群：")
                    for i, (cid, gname) in enumerate(groups, 1):
                        print("   %d) %s (%s)" % (i, gname, cid))
                    sel = _ask("请选择序号（或回车跳过）：", None)
                    if sel and sel.isdigit() and 1 <= int(sel) <= len(groups):
                        cid, gname = groups[int(sel) - 1]
                        if _confirm("确认发到群「%s」？[y/N]：" % gname):
                            patch["chat_id"] = cid
                            patch["mention"] = "all"
            else:
                needs.append({"key": "chat_id", "what": "群「%s」的 chat_id" % q,
                              "how": "用飞书连接器或 lark-cli im +chat-search --query <群名> 查"})
                print("  ⟳ 需要群 chat_id：请用飞书通道查到后写回（末尾有方法）。")
        else:
            print("  未输入群名，跳过群配置。")

    if not patch and not needs:
        print("  未做任何推送目标配置（notify 块保持不变）。")
        return

    if patch:
        shown = {k: (v[:12] + "…" if isinstance(v, str) and len(v) > 16 else v)
                 for k, v in patch.items()}
        if _confirm("即将写入 notify 块：%s\n确认写入？[y/N]：" % json.dumps(shown, ensure_ascii=False)):
            if _update_notify(cfg_path, patch):
                print("[OK]   推送目标已写入：%s" % json.dumps(patch, ensure_ascii=False))
            else:
                print("[FAIL] 写入失败，请手动编辑 targets.json 的 notify 块。")
        else:
            print("  已取消，未写入。")
            patch = {}

    if needs and not patch:
        # 有需要外部查的 ID，但本次没写进任何东西：产出待办 + 指引
        _emit_notify_setup_needed(ws, needs)
        print("  你可以用环境可用的飞书通道查到 ID 后运行：")
        print("    python3 scripts/watch.py --set-user-id=<openId> --set-chat-id=<chat_id>")
        print("  或直接编辑 targets.json 的 notify 块（user_id=私信 openId，chat_id=群）。")
    elif needs and patch:
        # 部分自助成功、部分需外部查：仍给待办
        _emit_notify_setup_needed(ws, needs)


def _check_notify(cfg):
    n = (cfg or {}).get("notify") or {}
    cid = n.get("chat_id")
    if cid == NOTIFY_PLACEHOLDER:
        return "placeholder", cid
    if not cid:
        return "missing", cid
    return "ok", cid


def do_init(ws, config_path, non_interactive):
    """首次使用检查：环境 / 网络 / 发送通道 / 配置引导。返回进程退出码。"""
    print("=== change-watch 初始化检查 ===")
    has_error = False

    ok, ver = _check_python()
    if ok:
        print("[OK]   Python %s（>=3.8 满足）" % ver)
    else:
        print("[FAIL] Python %s 过低，需要 >=3.8" % ver)
        has_error = True

    try:
        for sub in ("state/streams", "state/registries", "out", "log"):
            os.makedirs(os.path.join(ws, *sub.split("/")), exist_ok=True)
    except OSError as e:
        print("[FAIL] 无法创建工作区 %s：%s" % (ws, e))
        return 1
    cfg_path = os.path.join(ws, "targets.json")
    if not os.path.exists(cfg_path):
        shutil.copyfile(config_path, cfg_path)
        print("[OK]   已生成工作区配置：%s" % cfg_path)
    else:
        print("[OK]   工作区配置已存在：%s" % cfg_path)
    print("       改这个文件即可增删目标/信源（无需改代码）")

    cfg = jload(cfg_path, None)
    if cfg:
        problems = validate_config(cfg)
        if problems:
            print("[FAIL] 配置校验未通过：%s" % "；".join(problems))
            has_error = True
        state, cid = _check_notify(cfg)
        has_user = bool((cfg.get("notify") or {}).get("user_id"))
        if state == "placeholder":
            print("[WARN] notify.chat_id 仍是占位符 %s —— 若要用群提醒请改，否则删掉该字段" % cid)
            print("       交互运行 `python3 scripts/watch.py --init` 可自动按群名搜索并写入。")
        elif state == "missing" and not has_user:
            print("[INFO] 未配置任何通知目标（notify 无 user_id 也无 chat_id）。")
            print("       交互运行 --init 可自动配置，或手动编辑 notify 块。")
        if has_user:
            print("[OK]   私信目标已配置（notify.user_id）")
    else:
        print("[FAIL] 配置读取失败：%s" % cfg_path)
        has_error = True

    ok_n, _, err = _probe("https://blog.google/rss/")
    if ok_n:
        print("[OK]   网络：可访问 Google 源（blog.google）")
    else:
        print("[WARN] 网络：无法访问 https://blog.google/rss/")
        print("       原因：%s" % err)
        print("       可能是离线 / 防火墙 / 地区限制（国内环境常无法直连 Google）。")
        print("       仍可继续，但源会全部 FAIL；请确认网络或代理后再跑。")

    lc = _detect_sender()
    if lc:
        print("[OK]   发送通道：检测到 lark-cli（%s），可用它发飞书卡片" % lc)
    else:
        print("[INFO] 发送通道：未检测到 lark-cli。若用本工具飞书连接器请确保已绑定 bot 身份；")
        print("       两者皆无时，通知将落盘 out/notify-unsent-<时间>.md 并明确提醒，不会静默丢失。")

    # —— 推送目标配置：通道由 AI / 环境决定。脚本只「报告需求 + 列出可用通道」，
    #    解析与写回完全交给 AI（或交互模式的人类），脚本绝不自行猜测/写回飞书 ID。——
    if sys.stdin.isatty() and not non_interactive:
        _interactive_notify_setup(cfg_path, ws)
    else:
        # 非交互（CI / 自动化 / AI 驱动）：不自动解析或写回任何飞书 ID。
        # 只依据当前配置缺口给出「待办」，并提示 AI 用环境可用通道接手。
        needs = []
        cur_notify = (_read_cfg(cfg_path) or {}).get("notify") or {}
        if not cur_notify.get("user_id"):
            needs.append({"key": "user_id", "what": "私信目标 openId（你自己）",
                          "how": "用环境可用的飞书通道（连接器 / lark-cli auth status）取自己的 open_id"})
        cid = cur_notify.get("chat_id")
        if (not cid) or cid == NOTIFY_PLACEHOLDER:
            needs.append({"key": "chat_id", "what": "群 chat_id（如需群提醒）",
                          "how": "用环境可用的飞书通道（连接器 / lark-cli im +chat-search --query <群名>）查"})
        if needs:
            chan = []
            if lc:
                chan.append("本机 lark-cli")
            chan.append("飞书连接器（若已绑定 bot）")
            print("[INFO] 推送目标需配置（脚本不自动解析，交由 AI / 你接手）。")
            print("       可用通道：%s。" % "、".join(chan))
            print("       AI 接手流程：依环境选通道 → 与用户确认目标 → 查到 ID → "
                  "`python3 scripts/watch.py --set-user-id=<openId> --set-chat-id=<chat_id>` 写回。")
            print("       也可直接编辑 targets.json 的 notify 块。脚本已生成 out/notify-setup-needed.json。")
            _emit_notify_setup_needed(ws, needs)
        else:
            print("[OK]   推送目标已配置（notify.user_id / chat_id 均已存在），跳过。")

    print()
    print("=== 下一步 ===")
    print("1. 推送目标（飞书 ID）由你（AI）依环境决定通道后配置，脚本不自动解析：")
    print("   - AI 驱动：运行 --init --non-interactive 读取上方「待办」→ 用可用通道查 ID →")
    print("     `python3 scripts/watch.py --set-user-id=<openId> --set-chat-id=<chat_id>` 写回。")
    print("   - 人类交互：运行 --init（终端），脚本会按你选择用本机 lark-cli 辅助查并确认写回。")
    print("   - 两者都可用飞书连接器或手动编辑 targets.json 的 notify 块。")
    print("2. 运行：python3 %s --baseline   建基线（不推送，避免把历史当新消息）" %
          os.path.join(SKILL_DIR, "scripts", "watch.py"))
    print("3. 设置定时任务：见 README「定时任务（装完即用）」")

    if sys.stdin.isatty() and not non_interactive:
        try:
            input("按回车结束初始化检查…")
        except Exception:
            pass
    return 1 if has_error else 0


def main():
    ap = argparse.ArgumentParser(description="change-watch 抓取与差分")
    ap.add_argument("--workspace", default=DEFAULT_WS, help="工作区目录（默认 ~/.change-watch）")
    ap.add_argument("--config", default=DEFAULT_CONFIG, help="配置模板路径（仅首次复制用）")
    ap.add_argument("--baseline", action="store_true", help="首次运行：只建基线，不产出变更")
    ap.add_argument("--dry-run", action="store_true", help="只比对，不写状态")
    ap.add_argument("--target", default=None, help="只跑指定 target id")
    ap.add_argument("--init", action="store_true", help="首次使用检查：环境/网络/发送通道/配置引导")
    ap.add_argument("--non-interactive", action="store_true", help="init 时不等待交互输入（CI 用）")
    ap.add_argument("--set-user-id", default=None,
                    help="把查到的私信目标 openId 写回 notify.user_id（由 AI/宿主工具按环境查到后传入）")
    ap.add_argument("--set-chat-id", default=None,
                    help="把查到的群 chat_id 写回 notify.chat_id（由 AI/宿主工具按环境查到后传入）")
    args = ap.parse_args()

    ws = os.path.abspath(os.path.expanduser(args.workspace))
    if args.init:
        return do_init(ws, args.config, args.non_interactive)

    for sub in ("state/streams", "state/registries", "out", "log"):
        os.makedirs(os.path.join(ws, *sub.split("/")), exist_ok=True)

    cfg_path = os.path.join(ws, "targets.json")
    if not os.path.exists(cfg_path):
        shutil.copyfile(args.config, cfg_path)
        print("已初始化配置：%s（改这个文件即可增删目标/信源）" % cfg_path)

    # 直接写回模式：AI / 宿主工具查到推送目标 ID 后传入，独立于 init 流程。
    # 查 ID 的通道由调用方按环境决定，脚本只负责把结果写回配置。
    if args.set_user_id or args.set_chat_id:
        patch = {}
        if args.set_user_id:
            patch["user_id"] = args.set_user_id.strip()
        if args.set_chat_id:
            patch["chat_id"] = args.set_chat_id.strip()
        if _update_notify(cfg_path, patch):
            print("[OK]   已写回 notify 块：%s" % json.dumps(patch, ensure_ascii=False))
            return 0
        else:
            print("[FAIL] 写回 notify 失败：%s" % cfg_path, file=sys.stderr)
            return 1

    cfg = jload(cfg_path, None)
    if not cfg:
        print("配置读取失败：%s" % cfg_path, file=sys.stderr)
        return 1
    problems = validate_config(cfg)
    if problems:
        print("配置有问题（%d 处），已中止：%s" % (len(problems), cfg_path), file=sys.stderr)
        for p in problems:
            print("  - %s" % p, file=sys.stderr)
        return 1
    settings = cfg.get("settings", {})

    # 单实例锁：两轮 watch 并发会各自基于同一份旧基线算 diff，再互相覆盖 latest.json /
    # pending.json —— 结果是谁的变更集都不完整，而且极难排查。拿不到锁就跳过本轮。
    ok, lock_msg = acquire_run_lock(ws)
    if not ok:
        print(lock_msg, file=sys.stderr)
        return 3
    if lock_msg:
        print("[INFO] %s" % lock_msg, file=sys.stderr)

    health = load_health(ws)
    changes = []
    # 有限窗口流（X 时间线、LinkedIn 展示页）里从窗口底部滚出去的条目。
    # 它们不是变更，不进 changes、不占 AI 判断配额，但会单独落账备查。
    rollouts = []
    # 各源基线「延迟提交」：先把变更落盘（latest.json / pending.json），最后才推进基线。
    # 原来是在循环里立刻写状态，而 latest.json 要到最后才写——中途被杀（休眠、重启、
    # 超时）会把已检出的变更静默吞掉：它们进了基线却没进变更集，下轮不会再出现。
    # 延迟提交后失败模式变为「下轮重检」，重检由 log_change.py 的 dedupe_key 去重，无害。
    pending_state_writes = []
    statuses = []
    failed = 0

    # 先把所有源并发抓完（collect_all 保证结果与 jobs 同序），
    # 再按配置顺序逐个走差分 —— 这样 stdout 与 latest.json 的顺序不受完成先后影响。
    jobs = []
    for target in cfg.get("targets", []):
        tid = target["id"]
        if args.target and tid != args.target:
            continue
        for layer, field in (("stream", "streams"), ("registry", "registries")):
            for spec in target.get(field, []):
                jobs.append((tid, layer, spec))
    _results = iter(collect_all(settings, jobs))

    for target in cfg.get("targets", []):
        tid = target["id"]
        if args.target and tid != args.target:
            continue
        for layer, field in (("stream", "streams"), ("registry", "registries")):
            for spec in target.get(field, []):
                st, items = next(_results)
                hrec, transition = update_health(
                    health, "%s/%s" % (tid, st["source_id"]),
                    st["ok"], st.get("error"), now_iso())
                st["consecutive_failures"] = hrec["consecutive_failures"]
                st["health_state"] = hrec["state"]
                if hrec["state"] != "ok" and hrec.get("first_failed_at"):
                    fh_h = hours_since(hrec["first_failed_at"])
                    if fh_h is not None:
                        st["failing_hours"] = round(fh_h, 1)
                if transition:
                    st["health_transition"] = transition
                statuses.append(st)
                if not st["ok"]:
                    failed += 1
                    print("  [FAIL x%d] %-17s %s" % (
                        hrec["consecutive_failures"], st["source_id"], st["error"]),
                        file=sys.stderr)
                    continue
                thr = stale_threshold(spec, settings)
                if thr is not None and st["stale_hours"] is not None and st["stale_hours"] > thr:
                    st["stale"] = True
                    st["stale_after_hours"] = thr

                state_path = os.path.join(ws, "state", "streams" if layer == "stream" else "registries",
                                          "%s.json" % st["source_id"])
                prev = jload(state_path, {}).get("items", {})
                added, removed, changed = diff_map(prev, items)
                removed, rolled_out = split_rolled_out(spec, added, removed, items, prev)

                if not args.baseline:
                    # 滚出窗口的不是"变更"，但要留痕 —— 沉默必须有据，不能悄悄丢。
                    roll_reason = ("窗口滚动：有新条目进入，旧条目已移出该源的最近条目窗口"
                                   if added else
                                   "窗口收窄：本轮该源返回条目数变少，最旧几条掉出窗口（非删除）")
                    for key, rec, old in rolled_out:
                        rollouts.append({
                            "target": tid, "layer": layer,
                            "source_id": st["source_id"], "source_label": st["label"],
                            "key": key, "url": rec.get("link") or st["url"],
                            "title": rec.get("title") or rec.get("cells", [""])[0],
                            "published": rec.get("published"),
                            "reason": roll_reason,
                            "logged_at": now_iso(),
                        })
                    for kind, group in (("added", added), ("removed", removed), ("changed", changed)):
                        for key, rec, old in group:
                            changes.append({
                                "change_id": change_id(tid, layer, st["source_id"], kind, key),
                                "change_kind": kind,
                                "target": tid,
                                "layer": layer,
                                "source_id": st["source_id"],
                                "source_label": st["label"],
                                "key": key,
                                "url": rec.get("link") or st["url"],
                                "title": rec.get("title") or rec.get("cells", [""])[0],
                                "published": rec.get("published"),
                                "curr": rec,
                                "prev": old,
                                "first_seen": now_iso(),
                                "reopen_count": 0,
                            })

                if not args.dry_run:
                    # 不立刻落盘：先攒着，等变更安全写入 latest.json / pending.json 后再统一提交。
                    pending_state_writes.append((state_path, {
                        "source_id": st["source_id"],
                        "label": st["label"],
                        "kind": st["kind"],
                        "url": st["url"],
                        "updated_at": now_iso(),
                        "items": items,
                    }))

                print("  [%s] %-18s 条目=%-4d 新增=%-3d 消失=%-3d 变化=%-3d%s%s" % (
                    "ok" if st["ok"] else "!!", st["source_id"], len(items),
                    len(added), len(removed), len(changed),
                    "  <STALE>" if st["stale"] else "",
                    "  滚出=%-3d" % len(rolled_out) if rolled_out else ""))

    # ---- 待判队列：上一轮没判断完的变更不会被丢掉 ----
    # watch 一跑完状态就推进了，未判的变更下轮不会再出现在 diff 里 —— 等于永久丢失。
    # 所以未判的进 pending，下轮重新带回来再问一次（上限见 MAX_PENDING_AGE_H）。
    pending_path = os.path.join(ws, "state", "pending.json")
    replayed, expired = [], []
    if not args.baseline:
        curr_ids = {c["change_id"] for c in changes}
        for item in jload(pending_path, []):
            if not isinstance(item, dict) or not item.get("change_id"):
                continue
            if item["change_id"] in curr_ids:
                continue                      # 本轮又出现了，用新的那份
            # 按"未判年龄"过期：只看 first_seen 距今多久，与采集跑了多少轮无关。
            fs = item.get("first_seen")
            if not fs:                        # 脏记录缺时间戳：从现在起算，既不死循环也不立刻丢
                fs = now_iso()
                item = dict(item, first_seen=fs)
            n = int(item.get("reopen_count", 0)) + 1
            item = dict(item, reopen_count=n, reopened=True)
            age_h = (datetime.datetime.now(BEIJING) - parse_iso(fs)).total_seconds() / 3600.0
            (expired if age_h > MAX_PENDING_AGE_H else replayed).append(item)
        changes.extend(replayed)

    run_at = now_iso()
    stem = "baseline" if args.baseline else "changes"
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    down = [s["source_id"] for s in statuses if s.get("health_state") == "down"]
    flapping = [s["source_id"] for s in statuses if s.get("health_state") == "flapping"]
    # 只有从「持续失联 down」恢复才算真恢复；flapping → ok 是单次抖动自愈，
    # 与告警卡（log_change.py --health）同一口径，避免 stdout 与卡片说法不一致。
    def _real_recovery(s):
        p = str(s.get("health_transition", "")).split()
        return len(p) >= 3 and p[0] == "down" and p[-1] == "ok"
    recovered = [s["source_id"] for s in statuses if _real_recovery(s)]
    # 防御性守卫：停更只可能发生在「成功抓取」的源上。抓取失败的源即便残留 stale
    # 标志也绝不算停更（否则会把"我们抓不到"误报成"对方停更"）。watch.py:1215 的
    # continue 已保证失败源不带 stale，这里再加一层显式过滤，防止日后重构误判。
    stale_sources = [{"source_id": s["source_id"], "label": s.get("label", s["source_id"]),
                      "stale_hours": s.get("stale_hours"),
                      "threshold_hours": s.get("stale_after_hours")}
                     for s in statuses if s.get("stale") and s.get("ok")]
    payload = {
        "run_at": run_at,
        "workspace": ws,
        "baseline": bool(args.baseline),
        "dry_run": bool(args.dry_run),
        "summary": {
            "added": sum(1 for c in changes if c["change_kind"] == "added"),
            "removed": sum(1 for c in changes if c["change_kind"] == "removed"),
            "changed": sum(1 for c in changes if c["change_kind"] == "changed"),
            "total": len(changes),
            "sources": len(statuses),
            "sources_scanned": len(statuses),
            "sources_failed": failed,
            "sources_ok": len(statuses) - failed,
            "sources_down": len(down),
            "sources_stale": len(stale_sources),
            "replayed": len(replayed),
            "unjudged_expired": len(expired),
        },
        "replayed": replayed,
        "health": {
            "down": down,
            "flapping": flapping,
            "recovered": recovered,
        },
        "stale_sources": stale_sources,
        "sources": statuses,
        "changes": changes,
    }
    if not args.dry_run:
        save_health(ws, health)
        jdump(os.path.join(ws, "out", "%s-%s.json" % (stem, stamp)), payload)
        jdump(os.path.join(ws, "out", "latest.json"), payload)
        prune_out(ws)
        if not args.baseline:
            # 本轮全部待判项（含重新带回的）。log_change.py 判断后会从中移除。
            jdump(pending_path, changes)
            if expired:
                with open(os.path.join(ws, "log", "unjudged.jsonl"), "a", encoding="utf-8") as fh:
                    for item in expired:
                        row = dict(item)
                        row["logged_at"] = run_at
                        row["reason"] = "判断超时：未判超过 %d 小时" % MAX_PENDING_AGE_H
                        fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        if rollouts:
            # 滚出窗口的条目不进变更集，但必须留痕：将来有人问"这条是不是被我们弄丢了"，
            # 这里要能查到它是怎么走的，而不是无声无息。
            with open(os.path.join(ws, "log", "rolled_out.jsonl"), "a", encoding="utf-8") as fh:
                for row in rollouts:
                    fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        # 变更已安全落盘，现在才推进各源基线（基线运行同样需要，故放在 baseline 判断之外）。
        for spath, sobj in pending_state_writes:
            jdump(spath, sobj)
        if not args.baseline:
            with open(os.path.join(ws, "log", "runs.jsonl"), "a", encoding="utf-8") as fh:
                fh.write(json.dumps({"run_at": run_at, "summary": payload["summary"]},
                                    ensure_ascii=False) + "\n")

    s = payload["summary"]
    print("\n完成：源 %d 个（失败 %d / 其中持续失联 %d，停更告警 %d）；变更 %d 条（新增 %d / 消失 %d / 变化 %d）" % (
        s["sources"], s["sources_failed"], s["sources_down"], s["sources_stale"],
        s["total"], s["added"], s["removed"], s["changed"]))
    if down:
        print("持续失联（连续失败 ≥%d 次，需人工看一眼）：%s" % (
            DOWN_THRESHOLD,
            ", ".join(("%s（已持续 %sh）" % (x["source_id"], int(x["failing_hours"]))
                       if x.get("failing_hours") else x["source_id"])
                      for x in statuses if x.get("health_state") == "down")))
    if recovered:
        print("已恢复（此前失败，本轮成功）：%s" % ", ".join(recovered))
    if replayed:
        print("重新带回未判变更 %d 条（上一轮没给判断，本轮再问一次）：%s"
              % (len(replayed), ", ".join(x["change_id"] for x in replayed)))
    if expired:
        print("判断超时 %d 条（未判超过 %d 小时，已记入 log/unjudged.jsonl，不再重问）：%s"
              % (len(expired), MAX_PENDING_AGE_H,
                 ", ".join(x["change_id"] for x in expired)))
    if rollouts:
        print("窗口滚动 %d 条（旧条目移出该源的可见窗口，非内容消失，已记入 log/rolled_out.jsonl）：%s"
              % (len(rollouts), ", ".join(r["source_id"] for r in rollouts)))
    if args.baseline:
        print("本次是基线运行：状态已建立，未产出变更。下次运行起只报增量。")
    elif s["total"]:
        print("变更集：%s" % os.path.join(ws, "out", "latest.json"))
    else:
        print("本轮无变更。")
    return 2 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
