# -*- coding: utf-8 -*-
"""change-watch 解析层专项夹具（v20261014.1 新增，可随时重跑）。

覆盖 2026-10-14 实测三类真实问题：
  X  纯媒体帖（正文只剩指向自身媒体的 t.co 短链）→ 按约定忽略；
  X  长文 note_tweet（legacy.full_text 是截断版）→ 以 note 全文为准；
  X  Article（正文只剩 x.com/i/article 短链）→ 取 title + preview_text；
  feed 多条条目共用同一链接（GCP 发布说明）→ 键必须是稳定 id 而非位置序号，
       轮换时不得产生 phantom changed。
另含回归：正常推文 / 带图说的视频帖 / RT / 空正文 / 0 推文报错 /
          链接唯一的 feed 键不变 / 无链接条目。
"""
import json
import sys

sys.path.insert(0, "/Users/apple/.workbuddy/skills/change-watch/scripts")
import watch  # noqa: E402

PASS = FAIL = 0


def check(name, ok, detail=""):
    global PASS, FAIL
    if ok:
        PASS += 1
        print("  [ok] %s" % name)
    else:
        FAIL += 1
        print("  [!!] %s  %s" % (name, detail))


def x_tweet(tid, text, media=None, urls=None, note=None, article=None,
            visibility=False):
    """构造一条推文（legacy + 可选的 note_tweet / article）。"""
    legacy = {
        "id_str": tid,
        "full_text": text,
        "created_at": "Wed Oct 14 02:50:00 +0000 2026",
        "entities": {"media": [{"type": m} for m in (media or [])],
                     "urls": [{"expanded_url": u} for u in (urls or [])]},
    }
    res = {"__typename": "Tweet", "legacy": legacy}
    if note:
        res["note_tweet"] = {"note_tweet_results": {"result": {"text": note}}}
    if article:
        res["article"] = {"article_results": {"result": article}}
    if visibility:
        res = {"__typename": "TweetWithVisibilityResults", "tweet": res}
    return {"content": {"itemContent": {"tweet_results": {"result": res}}}}


def x_response(entries):
    return {"data": {"user": {"result": {"timeline": {"timeline": {
        "instructions": [{"entries": entries}]}}}}}}


def parse_x(entries, skip_rt=True):
    return dict(watch.parse_x_timeline(x_response(entries), "Test", skip_rt))


print("== X 时间线解析 ==")

# X1 长文：full_text 被截断，note 里有全文 → 必须用 note 全文
r = parse_x([x_tweet("1001", "Argon is available now for Ultra and Pro "
                            "subscribers in @GeminiApp and AI Mode in Sear",
                     note="Argon is available now for Ultra and Pro subscribers "
                          "in @GeminiApp and AI Mode in Search.\n\n"
                          "Argon will launch at an introductory price of $2 per "
                          "million input tokens and $10 per million output tokens.")])
t = r.get("https://x.com/Test/status/1001", {}).get("text", "")
check("X1 note_tweet 全文替代截断的 full_text",
      "$10 per million output tokens" in t and "Sear" != t[-4:],
      "取到: %r" % t[-60:])

# X2 纯视频帖：正文只剩指向自身媒体的短链 → 忽略
r = parse_x([x_tweet("1002", "https://t.co/aaa", media=["video"])])
check("X2 纯视频帖被忽略", not r, "仍产出: %s" % list(r))

# X3 纯图片帖：同样无可读正文 → 忽略
r = parse_x([x_tweet("1003", "https://t.co/bbb", media=["photo"])])
check("X3 纯图片帖被忽略", not r, "仍产出: %s" % list(r))

# X4 X 文章：正文只剩 article 短链 → 取 title + preview_text + 文章链接
r = parse_x([x_tweet("1004", "https://t.co/ccc",
                     urls=["https://x.com/i/article/999"],
                     article={"title": "Optimizing a 200k-line codebase",
                              "preview_text": "When a new model launches…"})])
t = r.get("https://x.com/Test/status/1004", {}).get("text", "")
check("X4 X 文章取到标题与预览",
      "Optimizing a 200k-line codebase" in t and "When a new model launches" in t
      and "x.com/i/article/999" in t, "取到: %r" % t[:80])

# X5 正常推文：行为不变（回归）
r = parse_x([x_tweet("1005", "Gemini 4 Argon is rolling out today.")])
check("X5 正常推文原样保留",
      r.get("https://x.com/Test/status/1005", {}).get("text")
      == "Gemini 4 Argon is rolling out today.")

# X6 视频+图说：有正文 → 必须保留（不能误杀）
r = parse_x([x_tweet("1006", "Watch the demo ↓ https://t.co/ddd",
                     media=["video"])])
check("X6 视频帖带正文仍保留",
      "Watch the demo" in r.get("https://x.com/Test/status/1006", {}).get("text", ""))

# X7 长文+媒体（真实组合）：note 全文优先，不因媒体被跳过
r = parse_x([x_tweet("1007", "truncated… https://t.co/eee", media=["video"],
                     note="Full long-form body with all the details.")])
check("X7 长文带媒体取全文",
      r.get("https://x.com/Test/status/1007", {}).get("text")
      == "Full long-form body with all the details.")

# X8 RT 仍按 skip_retweets 跳过（回归）
r = parse_x([x_tweet("1008", "RT @someone: hello world")])
check("X8 RT 仍被跳过", not r)

# X9 TweetWithVisibilityResults 容器同样生效（回归）
r = parse_x([x_tweet("1009", "hidden tweet", visibility=True)])
check("X9 VisibilityResults 容器可解析",
      r.get("https://x.com/Test/status/1009", {}).get("text") == "hidden tweet")

# X10 0 推文必须显式报错（回归：防接口故障被当成全部消失）
try:
    watch.parse_x_timeline(x_response([]), "Test")
    check("X10 0 推文报错", False, "未抛异常")
except ValueError:
    check("X10 0 推文报错", True)

print("== feed 键稳定性（GCP 发布说明类：多条共用同一链接） ==")


def atom_feed(entries):
    """entries: [(id, title, date, body)]"""
    items = []
    for i, (aid, title, date, body) in enumerate(entries):
        items.append(
            "<entry><id>%s</id><title>%s</title>"
            "<link rel='alternate' href='https://docs.cloud.google.com/release-notes'/>"
            "<published>%s</published><content>%s</content></entry>"
            % (aid, title, date, body))
    return ("<?xml version='1.0'?><feed xmlns='http://www.w3.org/2005/Atom'>"
            + "".join(items) + "</feed>")


def keys_of(xml):
    return [k for k, _ in watch.parse_feed(xml)]


before = [("tag:x#October_13_2026", "October 13, 2026", "2026-10-13T00:00:00-07:00",
           "BigQuery: AI.AGG is GA."),
          ("tag:x#October_12_2026", "October 12, 2026", "2026-10-12T00:00:00-07:00",
           "Cloud SQL: MySQL 8.0.46."),
          ("tag:x#October_11_2026", "October 11, 2026", "2026-10-11T00:00:00-07:00",
           "Apigee: 1-18-0.")]
after = [("tag:x#October_14_2026", "October 14, 2026", "2026-10-14T00:00:00-07:00",
          "Gemini: new model.")] + before[:2]      # 新一天插入顶部，最旧一天挤出

kb = keys_of(atom_feed(before))
ka = keys_of(atom_feed(after))
check("F1 共用链接时键 = Atom id（非位置序号）",
      kb == ["tag:x#October_13_2026", "tag:x#October_12_2026", "tag:x#October_11_2026"],
      "实际: %s" % kb)

common_b = {k: dict(watch.parse_feed(atom_feed(before)))[k] for k in kb[:2]}
common_a = {k: dict(watch.parse_feed(atom_feed(after)))[k] for k in ka[1:3]}
check("F2 轮换后共同条目键与哈希都不变（无 phantom changed）",
      set(common_b) == set(common_a)
      and all(common_b[k]["hash"] == common_a[k]["hash"] for k in common_b),
      "before=%s after=%s" % (sorted(common_b), sorted(common_a)))
check("F3 轮换只体现为 1 个新键 + 1 个旧键脱落",
      set(ka) - set(kb) == {"tag:x#October_14_2026"}
      and set(kb) - set(ka) == {"tag:x#October_11_2026"})

# F4 共用链接但无 id/guid → 退回 标题+日期 组合键（稳定，非位置）
rss_dup = ("<?xml version='1.0'?><rss><channel>"
           + "".join("<item><title>%s</title>"
                     "<link>https://same.example/notes</link>"
                     "<pubDate>%s</pubDate><description>%s</description></item>"
                     % (t, d, b) for t, d, b in
                     [("Day 1", "Mon, 13 Oct 2026 00:00:00 -0700", "A."),
                      ("Day 2", "Sun, 12 Oct 2026 00:00:00 -0700", "B.")])
           + "</channel></rss>")
rss_dup_rot = rss_dup.replace(
    "<item><title>Day 1</title>",
    "<item><title>Day 0</title><link>https://same.example/notes</link>"
    "<pubDate>Tue, 14 Oct 2026 00:00:00 -0700</pubDate>"
    "<description>N.</description></item><item><title>Day 1</title>")
k1 = keys_of(rss_dup)
k2 = keys_of(rss_dup_rot)
check("F4 无 id 的共用链接退回标题+日期键且轮换稳定",
      len(k1) == 2 and k1[0].startswith("title:")
      and k2[1] == k1[0] and k2[2] == k1[1],
      "k1=%s k2=%s" % (k1, k2))

# F5 链接唯一的流：键仍是链接（回归：不动 12 个博客流的行为）
rss_unique = ("<?xml version='1.0'?><rss><channel>"
              + "".join("<item><title>Post %d</title>"
                        "<link>https://blog.example/p%d</link>"
                        "<pubDate>Mon, 13 Oct 2026 00:00:00 -0700</pubDate>"
                        "<description>Body %d.</description></item>" % (i, i, i)
                        for i in range(3))
              + "</channel></rss>")
check("F5 链接唯一时键 = 链接（既有行为不变）",
      keys_of(rss_unique) == ["https://blog.example/p%d" % i for i in range(3)])

# F6 无链接条目：退回标题哈希（回归）
rss_nolink = ("<?xml version='1.0'?><rss><channel>"
              "<item><title>Only title</title>"
              "<pubDate>Mon, 13 Oct 2026 00:00:00 -0700</pubDate>"
              "<description>x</description></item>"
              "</channel></rss>")
check("F6 无链接条目键 = title: 前缀",
      keys_of(rss_nolink)[0].startswith("title:"))

print()
print("结果：%d 通过 / %d 失败" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
