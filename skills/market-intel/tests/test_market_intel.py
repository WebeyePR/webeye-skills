#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""market-intel 离线验证：解析 / 金额 / 规则求值 / 差分 / 实体归一 / 端到端判定。

运行：python3 -m unittest discover -s skills/market-intel/tests -v
（仓库根目录执行；全部用夹具离线跑，不碰网络。）
"""

import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.abspath(os.path.join(HERE, "..", "scripts"))
sys.path.insert(0, SCRIPTS)

import collect  # noqa: E402
import core  # noqa: E402
import diffcore  # noqa: E402
import facts as facts_mod  # noqa: E402
import judge  # noqa: E402
import match as match_mod  # noqa: E402
import pipeline  # noqa: E402
import bitable  # noqa: E402


class TestLoadSources(unittest.TestCase):
    """sources.json 加载：rsshub:// 地址拼装与默认值兜底。"""

    @staticmethod
    def _write_sources(ws, settings, sources):
        core.workspace(ws)
        core.jdump(os.path.join(ws, "rules.json"),
                   core.jload(os.path.join(core.SCRIPT_DIR, "..", "config", "rules.json.example"), None))
        core.jdump(os.path.join(ws, "sources.json"),
                   {"version": 1, "settings": settings, "sources": sources})

    def test_rsshub_prefix_resolved_from_settings(self):
        """rsshub:// 前缀按 settings.rsshub_base 拼装——部署迁移只改一处。"""
        with tempfile.TemporaryDirectory() as ws:
            self._write_sources(
                ws,
                {"rsshub_base": "http://10.0.0.5:1200/"},
                [{"id": "cls", "name": "财联社", "kind": "rss", "url": "rsshub://cls/telegraph",
                  "enabled": True, "serves": ["intel"], "tier": "T2", "owner_entity": None,
                  "tags": [], "windowed": True, "stale_after_hours": 72}])
            sources, settings = pipeline.load_sources(ws)
            self.assertEqual(sources[0]["url"], "http://10.0.0.5:1200/cls/telegraph")

    def test_rsshub_default_base(self):
        """未配置 rsshub_base 时用默认本地地址，行为与历史配置一致。"""
        with tempfile.TemporaryDirectory() as ws:
            self._write_sources(
                ws, {},
                [{"id": "kr", "name": "36氪快讯", "kind": "rss", "url": "rsshub://36kr/newsflashes",
                  "enabled": True, "serves": ["intel"], "tier": "T2", "owner_entity": None,
                  "tags": [], "windowed": True, "stale_after_hours": 168},
                 {"id": "web", "name": "某页面", "kind": "web_page", "url": "https://example.com/a",
                  "enabled": True, "serves": ["intel"], "tier": "T2", "owner_entity": None,
                  "tags": [], "windowed": True, "stale_after_hours": 168}])
            sources, _ = pipeline.load_sources(ws)
            by_id = {s["id"]: s["url"] for s in sources}
            self.assertEqual(by_id["kr"], "http://127.0.0.1:1200/36kr/newsflashes")
            self.assertEqual(by_id["web"], "https://example.com/a")  # 非 rsshub:// 原样保留


class TestParseIso(unittest.TestCase):
    """时间解析必须同时兼容 ISO 8601 与 RSS 原生 RFC 2822（2026-10-27 事故回归）。"""

    def test_rfc2822_gmt(self):
        ts = core.parse_iso("Mon, 26 Oct 2026 18:11:43 GMT")
        self.assertIsNotNone(ts, "RSS 原生 RFC 2822 时间必须可解析")

    def test_rfc2822_numeric_offset(self):
        a = core.parse_iso("Tue, 27 Oct 2026 10:34:24 +0800")
        b = core.parse_iso("2026-10-27T10:34:24+08:00")
        self.assertIsNotNone(a)
        self.assertEqual(a, b, "RFC 2822 与等价 ISO 必须得到同一时间戳")

    def test_iso_still_works(self):
        self.assertIsNotNone(core.parse_iso("2026-10-27T02:34:24Z"))
        self.assertIsNotNone(core.parse_iso("2026-10-27T10:34:24+08:00"))

    def test_garbage_returns_none(self):
        self.assertIsNone(core.parse_iso("garbage"))
        self.assertIsNone(core.parse_iso(""))
        self.assertIsNone(core.parse_iso(None))


class TestParseFeed(unittest.TestCase):
    FEED = """<?xml version="1.0"?><rss version="2.0"><channel>
      <item><title>A</title><link>https://x.com/post/1</link><pubDate>Mon, 20 Oct 2026 08:00:00 GMT</pubDate><description>正文A</description></item>
      <item><title>B</title><link>https://x.com/same</link><guid>g-b</guid><pubDate>Tue, 21 Oct 2026 08:00:00 GMT</pubDate></item>
      <item><title>C</title><link>https://x.com/same</link><guid>g-c</guid><pubDate>Wed, 22 Oct 2026 08:00:00 GMT</pubDate></item>
    </channel></rss>"""

    def test_key_content_orthogonal(self):
        """键内容正交：同链接无论批内出现几次键恒定（v0.3.9 修复批内重复致键漂移）。

        此前共享链接时键取 guid:/#N 形态，活动行同一活动在列表页多板块重复展示，
        跨批拿到不同键 → change_id 漂移 → 已通报内容每轮以新变更身份重复入窗。
        """
        # 活动只出现一次：键 = 归一链接
        single = collect.parse_feed(self.FEED.replace(
            '<item><title>C</title><link>https://x.com/same</link><guid>g-c</guid><pubDate>Wed, 22 Oct 2026 08:00:00 GMT</pubDate></item>', ''))
        k_single = [e["key"] for e in single if e["link"] == "https://x.com/same"][0]
        self.assertEqual(k_single, "https://x.com/same")
        # 同一活动批内重复出现（列表页多板块场景）：键不因批内重复而漂移
        dup_feed = self.FEED.replace("</channel>",
            "<item><title>C2</title><link>https://x.com/same</link><guid>g-c</guid></item>"
            "<item><title>C3</title><link>https://x.com/same</link><guid>g-c</guid></item></channel>")
        dup = collect.parse_feed(dup_feed)
        k_dup = {e["key"] for e in dup if e["link"] == "https://x.com/same"}
        self.assertEqual(k_dup, {"https://x.com/same"}, "重复条目同键，由差分层 dict 化合并")

    def test_linkless_entries_stable_keys(self):
        """无链接条目兜底：guid 优先（稳定），guid 缺失按标题+时间哈希（确定可复算）。"""
        e_guid = {"title": "T", "link": None, "published": None, "_guid": "g-1"}
        e_hash = {"title": "T", "link": None, "published": "2026-10-27", "key": None}
        self.assertEqual(collect.entry_key_of(e_guid), "guid:g-1")
        k1 = collect.entry_key_of(e_hash)
        self.assertEqual(k1, collect.entry_key_of(dict(e_hash)), "t: 键必须确定")
        self.assertTrue(k1.startswith("t:"))
        # 快照条目（无 _guid 字段）原样保留 t: 键
        self.assertEqual(collect.entry_key_of({"title": "T", "link": None, "key": "t:abc"}), "t:abc")

    def test_rekey_baseline_migrates_and_dedupes(self):
        """基线键迁移：旧形态键（guid:URL/URL#2）统一重算为归一链接并去重，幂等。"""
        ws = tempfile.mkdtemp(prefix="mi-rekey-")
        os.makedirs(os.path.join(ws, "state", "streams"))
        old = {"collected_at": "2026-10-26T00:00:00+08:00", "entries": [
            {"key": "guid:https://e.com/a", "title": "活动A", "link": "https://e.com/a"},
            {"key": "guid:https://e.com/a#2", "title": "活动A(板块2)", "link": "https://e.com/a"},
            {"key": "https://e.com/b/", "title": "活动B", "link": "https://e.com/b/"},
        ]}
        core.jdump(diffcore.stream_path(ws, "s1"), old)
        rc = pipeline.rekey_baseline(ws)
        self.assertEqual(rc, 0)
        new = core.jload(diffcore.stream_path(ws, "s1"), {})
        keys = [e["key"] for e in new["entries"]]
        self.assertEqual(keys, ["https://e.com/a", "https://e.com/b"], "改键 + 去重 + 尾斜杠归一")
        # 幂等：重跑无变化
        pipeline.rekey_baseline(ws)
        again = core.jload(diffcore.stream_path(ws, "s1"), {})
        self.assertEqual([e["key"] for e in again["entries"]], keys)


class TestParseAmount(unittest.TestCase):
    def test_cases(self):
        f = core.parse_amount_cny
        self.assertAlmostEqual(f("完成数千万融资"), 1e7)      # 模糊表述取下限
        self.assertAlmostEqual(f("1.5亿美元"), 1.5 * 7.2e8)
        self.assertAlmostEqual(f("近亿元"), 1e8)
        self.assertAlmostEqual(f("2000万元人民币"), 2e7)
        self.assertAlmostEqual(f("300万美元"), 300e4 * 7.2)
        # 英文写法（外刊源常见，实测 TechCrunch "$30B" 曾解析失败导致大额线索漏报）
        self.assertAlmostEqual(f("$30B"), 30e9 * 7.2)
        self.assertAlmostEqual(f("$200M"), 200e6 * 7.2)
        self.assertAlmostEqual(f("$1.5billion"), 1.5e9 * 7.2)
        self.assertAlmostEqual(f("raised 35million"), 35e6)  # 无美元符号不折算
        self.assertIsNone(f("金额未披露"))
        self.assertIsNone(f(""))
        self.assertIsNone(f("连续三年盈利"))


class TestGrounding(unittest.TestCase):
    """grounding 数字核对：直引通过 / 换算兜底 / AI 申报 / 虚构拦截。"""

    CHANGE = {"title": "OpenAI working on new $30B raise", "summary": "a $30B round at record valuation"}

    def test_direct_quote_passes(self):
        rec = {"title": "OpenAI 新融资", "summary": "原文 $30B 语境"}
        unverified, notes = judge.grounding_check(rec, self.CHANGE)
        self.assertEqual(unverified, [])
        self.assertEqual(notes, {})

    def test_unit_conversion_fallback(self):
        """$30B → 300亿美元（×10）、30,000 人 → 3万人（÷10000）：换算兜底通过并留档。"""
        rec = {"title": "OpenAI 新融资", "summary": "OpenAI 洽谈 300 亿美元融资"}
        unverified, notes = judge.grounding_check(rec, self.CHANGE)
        self.assertEqual(unverified, [])
        self.assertIn("300", notes)

    def test_ai_declared_note(self):
        """AI 在 number_notes 申报换算依据 → 通过且备注留档（含脚本兜底认不出的换算）。"""
        rec = {"title": "OpenAI 新融资", "summary": "约合 2100 亿人民币",
               "number_notes": {"2100": "按 7.2 汇率与四舍五入换算自 $30B"}}
        unverified, notes = judge.grounding_check(rec, self.CHANGE)
        self.assertEqual(unverified, [])
        self.assertEqual(notes["2100"], "按 7.2 汇率与四舍五入换算自 $30B")

    def test_fabricated_number_blocked(self):
        """无出处、无申报、非换算的数字 → 拦截（unverified）。"""
        rec = {"title": "OpenAI 新融资", "summary": "OpenAI 融资 777 亿美元"}
        unverified, _ = judge.grounding_check(rec, self.CHANGE)
        self.assertEqual(unverified, ["777"])

    def test_title_dynamic_counter_not_probed(self):
        """title 不参与数字核对（2026-10-27 代码化）：页面监测源 title 尾部带
        动态计数器（阅读/热度数，实测 449→463 漂移），复用拷回场景 title 里的
        旧数字不得触发误报；summary 里的虚构数字仍然拦截。"""
        change = {"title": "禁令影响扩大 10-26 07:19 463", "summary": "IDC 分析"}
        rec = {"title": "禁令影响扩大 10-26 07:19 449", "summary": "政策风险升级"}
        unverified, _ = judge.grounding_check(rec, change)
        self.assertEqual(unverified, [], "title 原样带回契约，动态计数器漂移不应误报")

    def test_change_out_of_window_skips_grounding(self):
        """变更已滚出本轮窗口（change=None，复用拷回常见）：无证据可查，
        跳过核对，不得对说明里的合法数字（如年份 2026）误报。"""
        rec = {"title": "2026物流展", "summary": "2026亚洲国际物流展于11月举办"}
        unverified, notes = judge.grounding_check(rec, None)
        self.assertEqual((unverified, notes), ([], {}))


class TestEntity(unittest.TestCase):
    ENT = {"moonshot": {"name": "月之暗面", "aliases": ["Kimi", "Moonshot"], "industries": ["AI"]}}

    def test_alias_hit(self):
        eid, rec = facts_mod.normalize_entity("Kimi", self.ENT)
        self.assertEqual(eid, "moonshot")
        self.assertEqual(rec["name"], "月之暗面")

    def test_unknown_slug(self):
        eid, rec = facts_mod.normalize_entity("某新公司X", self.ENT)
        self.assertTrue(eid.startswith("unknown:"))
        self.assertEqual(rec["name"], "某新公司X")


class TestFundingDateStrict(unittest.TestCase):
    """within_days 时效判定：日期不可解析时不得当成"近期"命中（2026-10-27 修复）。

    旧实现 `if within and ts and ...` —— ts 为 None 时整条时效判断被跳过，
    "去年10月"这类不可解析的旧融资会被当成"近 30 天内融资"命中 P0 线索。
    线索误报会让市场同事建联扑空，比漏报更伤信任，故按保守处理（不命中）。
    """

    COND = {"within_days": 30}

    def test_unparseable_date_not_hit(self):
        """不可解析日期（中文表述）→ 不满足时效，不命中。"""
        fact = {"funding_events": [{"date": "去年10月", "round": "B轮",
                                    "amount_text": "3亿元"}]}
        hit, _ = match_mod.ev_funding(self.COND, fact)
        self.assertFalse(hit, "无法解析的日期不得当成近期融资命中")

    def test_old_iso_date_not_hit(self):
        """一年前的 ISO 日期 → 超出 within_days，不命中。"""
        fact = {"funding_events": [{"date": "2025-10-01", "round": "B轮",
                                    "amount_text": "3亿元"}]}
        hit, _ = match_mod.ev_funding(self.COND, fact)
        self.assertFalse(hit)

    def test_recent_date_still_hit(self):
        """近 5 天的融资照常命中（修复不许误伤正常路径）。"""
        import datetime
        recent = (datetime.datetime.now() - datetime.timedelta(days=5)).strftime("%Y-%m-%d")
        fact = {"funding_events": [{"date": recent, "round": "B轮",
                                    "amount_text": "3亿元"}]}
        hit, detail = match_mod.ev_funding(self.COND, fact)
        self.assertTrue(hit, "近期融资必须命中")
        self.assertIn("B轮", detail)

    def test_no_within_days_ignores_date(self):
        """未设 within_days 时不做时效过滤（历史档案查询场景照常返回）。"""
        fact = {"funding_events": [{"date": "去年10月", "round": "B轮",
                                    "amount_text": "3亿元"}]}
        hit, _ = match_mod.ev_funding({}, fact)
        self.assertTrue(hit, "无时效要求时不应因日期不可解析而漏掉")


class TestDenylist(unittest.TestCase):
    """绝对负向黑名单（2026-10-27 用户明令）：命中即硬丢弃，绝不推巨头/服务商/晚期融资。

    用户原话："你总是在给我推希音（SHEIN）、钛动、声网这类人尽皆知的大巨头或
    出海成熟服务商……推这些大厂对我来说纯属噪音。"
    """

    def setUp(self):
        sys.path.insert(0, core.SCRIPT_DIR)
        import tempfile
        self.ws = tempfile.mkdtemp(prefix="mi-deny-")
        os.makedirs(self.ws, exist_ok=True)
        # 用内置模板（真实名单，不 mock）
        self.deny = match_mod.load_denylist(self.ws)

    def _hit(self, name, aliases=None, events=None, **kw):
        fact = {"name": name, "aliases": aliases or [], "funding_events": events or []}
        fact.update(kw)
        return match_mod.check_denylist(fact, self.deny)

    def test_super_giants_blocked(self):
        """超级巨头/独角兽一律命中。"""
        for name in ("希音", "字节跳动", "拼多多", "腾讯", "米哈游", "莉莉丝",
                     "月之暗面", "DeepSeek", "小米"):
            hit, why = self._hit(name)
            self.assertTrue(hit, "%s 应被黑名单拦截" % name)
            self.assertIn("超级巨头", why)

    def test_service_providers_blocked(self):
        """成熟出海服务商/友商（生态伙伴，非买云直客）一律命中。"""
        for name in ("声网", "钛动科技", "白鲸出海", "PingPong", "连连支付", "店匠"):
            hit, why = self._hit(name)
            self.assertTrue(hit, "%s 应被黑名单拦截（服务商/友商）" % name)
            self.assertIn("服务商", why)

    def test_alias_also_blocked(self):
        """别名命中同样拦截（SHEIN / Kimi / Moonshot 等）。"""
        hit, _ = self._hit("某某科技", aliases=["SHEIN"])
        self.assertTrue(hit, "别名含 SHEIN 也应拦截")

    def test_late_round_blocked(self):
        """C 轮及以上 / 战略融资 / IPO 一律命中（只要种子~B 轮）。"""
        for rnd in ("C轮", "C+轮", "D轮", "E轮", "F轮", "Pre-IPO", "战略融资"):
            hit, why = self._hit("某成长公司", events=[{"round": rnd}])
            self.assertTrue(hit, "%s 应被轮次拦截" % rnd)
            self.assertIn("轮次", why)

    def test_target_round_not_blocked(self):
        """种子~B 轮的目标客群不得被误伤（修复不许误杀）。"""
        for rnd in ("种子轮", "天使轮", "Pre-A轮", "A轮", "A+轮", "Pre-B轮", "B轮", "B+轮"):
            hit, _ = self._hit("某成长公司", events=[{"round": rnd}])
            self.assertFalse(hit, "%s 属于目标轮次，不该被拦" % rnd)

    def test_no_round_not_blocked(self):
        """轮次缺失不因轮次否决（可能只是报道没写）。"""
        hit, _ = self._hit("某成长公司", events=[])
        self.assertFalse(hit)

    def test_target_company_passes(self):
        """真实目标客群（早期轮次小公司）不得被拦。"""
        for name in ("橘猫宇宙", "湃岛科技", "Ambi", "光启之境"):
            hit, why = self._hit(name, events=[{"round": "Pre-A轮"}])
            self.assertFalse(hit, "%s 是目标客群，不该被拦（%s）" % (name, why))


class TestDenylistDefaultFallback(unittest.TestCase):
    """黑名单的「默认就有保护」四层语义（2026-10-27 用户注 2）。

    用户原话：「denylist.json 可以复制一个作为默认模板，如果用户不反对或没有则默认
    使用这个」。落点是：**用户不配也必须被过滤**，同时保留「我想清空」的出口。

    历史坑：工作区文件存在但名单为空（`{"version":1}` / `deny_entities: []`）时
    直接返回空名单 → **静默零过滤** → 巨头又被当线索推，正是用户抱怨的场景复现。
    """

    def setUp(self):
        sys.path.insert(0, core.SCRIPT_DIR)
        self.ws = tempfile.mkdtemp(prefix="mi-dldef-")
        core.workspace(self.ws)

    def _load(self):
        return match_mod.load_denylist(self.ws)

    def _write(self, content):
        p = os.path.join(self.ws, "denylist.json")
        if isinstance(content, str):
            open(p, "w", encoding="utf-8").write(content)
        else:
            core.jdump(p, content)

    def test_01_no_file_uses_builtin(self):
        """① 工作区无文件 → 开箱即用内置默认（117 巨头）。"""
        d = self._load()
        self.assertGreaterEqual(len(d.get("deny_entities") or []), 100,
                                "无文件时必须回退内置默认，不能零过滤")
        self.assertTrue(d.get("deny_by_round"), "轮次口径也要有默认")

    def test_02_empty_object_fills_builtin(self):
        """② 空对象 → 自动补内置默认（防「配了文件却零过滤」）。"""
        self._write({"version": 1})
        d = self._load()
        self.assertGreaterEqual(len(d.get("deny_entities") or []), 100)

    def test_03_empty_arrays_filled(self):
        """③ 显式空数组 → 同样补默认（这是最容易被误认为"已清空"的写法）。"""
        self._write({"version": 1, "deny_entities": [], "deny_by_round": []})
        d = self._load()
        self.assertGreaterEqual(len(d.get("deny_entities") or []), 100)

    def test_04_user_content_wins(self):
        """④ 用户给了内容 → 用用户的，不掺默认（该维度）。"""
        self._write({"version": 1, "deny_entities": ["只测这家"],
                     "deny_service_providers": ["也测这家"],
                     "deny_by_round": ["C轮"], "allow_round": ["A轮"]})
        d = self._load()
        self.assertEqual(d["deny_entities"], ["只测这家"], "用户名单必须原样保留")
        self.assertEqual(d["deny_by_round"], ["C轮"])

    def test_05_explicit_off_really_clears(self):
        """⑤ 显式 use_builtin_default=false → 真正清空（保留出口）。"""
        self._write({"version": 1, "use_builtin_default": False,
                     "deny_entities": [], "deny_by_round": []})
        d = self._load()
        self.assertEqual(d.get("deny_entities"), [], "显式关闭后必须真清空")

    def test_06_broken_json_falls_back(self):
        """⑥ 文件损坏 → 回退内置默认（不能因坏文件失去过滤）。"""
        self._write("{ 坏掉的 json")
        d = self._load()
        self.assertGreaterEqual(len(d.get("deny_entities") or []), 100)

    def test_07_user_named_companies_all_hit(self):
        """用户点名的巨头/服务商必须全部命中（含别名路径）。"""
        d = self._load()
        cases = [("希音", ["SHEIN"]), ("字节跳动", ["ByteDance"]), ("拼多多", ["Temu"]),
                 ("阿里", ["Alibaba"]), ("腾讯", ["Tencent"]), ("网易", ["NetEase"]),
                 ("米哈游", ["miHoYo"]), ("莉莉丝", ["Lilith"]), ("声网", ["Agora"]),
                 ("钛动", ["Tec-Do"]), ("白鲸出海", []), ("赤子城", []),
                 ("PingPong", []), ("连连", ["连连支付"]), ("飞书深诺", []),
                 ("店匠", ["Shoplazza"])]
        miss = []
        for name, aliases in cases:
            hit, _ = match_mod.check_denylist({"name": name, "aliases": aliases}, d)
            if not hit:
                miss.append(name)
        self.assertEqual(miss, [], "以下用户点名的公司未被拦截: %s" % miss)


class TestDenylistOnPoolEntry(unittest.TestCase):
    """入池前黑名单复检（2026-10-27 补）：build_pending 必须重算黑名单。

    事故场景：上一轮就已产出、尚未写入的 pending 线索，若公司后来被用户加入
    `denylist.json`（实测：声网、C 轮公司），旧实现只在 `judge.cmd_input` 生成
    线索时检查——`build_pending` 自称"按当前规则重算匹配"却只重算 scope，
    这些线索会被 `--bitable --commit` 直接写进线索池，违背「命中即硬丢弃」。
    """

    def setUp(self):
        sys.path.insert(0, core.SCRIPT_DIR)
        self.ws = tempfile.mkdtemp(prefix="mi-deny-pool-")
        core.workspace(self.ws)
        for f in ("rules.json", "entities.json"):
            core.jdump(os.path.join(self.ws, f), core.jload(
                os.path.join(core.SCRIPT_DIR, "..", "config", f + ".example"), None))
        core.jdump(os.path.join(self.ws, "denylist.json"), core.jload(
            os.path.join(core.SCRIPT_DIR, "..", "config", "denylist.json.example"), None))
        core.jdump(os.path.join(self.ws, "out", "latest.json"),
                   {"run_at": core.now_iso(), "changes": [], "summary": {}})
        self.cfg = {"enabled": True, "app_token": "x", "table_id": "y", "field_map": {}}

    def _seed(self, lid, eid, name, rnd):
        core.append_jsonl(os.path.join(self.ws, "log", "leads.jsonl"), {
            "lead_id": lid, "entity_id": eid, "entity_name": name,
            "priority": "P0", "score": 90, "matched_groups": ["p0-funding-recent"],
            "matched_group_names": ["近1月融资"], "industries": ["应用"],
            "bitable_status": "pending", "logged_at": core.now_iso()})
        core.jdump(os.path.join(self.ws, "facts", eid + ".json"), {
            "entity_id": eid, "name": name, "aliases": [], "industries": ["应用"],
            "origin": "CN", "going_global": True,
            "funding_events": [{"date": core.now_iso()[:10], "round": rnd,
                                "amount_text": "1亿元"}]})
        core.append_jsonl(os.path.join(self.ws, "log", "enrichments.jsonl"), {
            "entity_id": eid, "entity_name": name,
            "fields": {k: "x" for k in ["profile", "business", "funding", "investors",
                                        "financials", "tech_stack", "ceo", "size",
                                        "founded", "city", "contact", "website",
                                        "opportunity"]} | {"icebreaker": "x" * 100}})

    def test_denied_lead_excluded_from_pending(self):
        """黑名单公司（调研齐全）不得进入待写队列。"""
        import bitable
        self._seed("L1", "agora", "声网", "A轮")
        self._seed("L2", "cat", "橘猫宇宙", "Pre-A轮")
        items = bitable.build_pending(self.ws, self.cfg)
        names = [i["company"] for i in items]
        self.assertEqual(names, ["橘猫宇宙"], "声网应被入池前复检拦下")
        rows = {r["lead_id"]: r for r in core.read_jsonl(
            os.path.join(self.ws, "log", "leads.jsonl"))}
        self.assertEqual(rows["L1"]["bitable_status"], "filtered")
        self.assertIn("黑名单", rows["L1"]["bitable_note"])

    def test_late_round_lead_excluded(self):
        """C 轮公司同样在入池前复检被拦（轮次属黑名单口径）。"""
        import bitable
        self._seed("L3", "grow", "某C轮公司", "C轮")
        items = bitable.build_pending(self.ws, self.cfg)
        self.assertEqual(items, [], "C 轮公司不该进待写队列")


class TestSalesGuidance(unittest.TestCase):
    """销售指引强制字段（2026-10-27 用户明令）：技术战报必带 sales_playbook、
    战略风向必带 sales_strategy，缺则拒落盘（exit 2）。

    用户原话："行业新闻太泛，缺少真正的销售弹药"——没有销售动作的资讯 = 噪音。
    """

    def setUp(self):
        sys.path.insert(0, core.SCRIPT_DIR)
        import tempfile
        self.ws = tempfile.mkdtemp(prefix="mi-sales-")
        core.workspace(self.ws)
        for f in ("rules.json", "entities.json"):
            core.jdump(os.path.join(self.ws, f), core.jload(
                os.path.join(core.SCRIPT_DIR, "..", "config", f + ".example"), None))
        core.jdump(os.path.join(self.ws, "notify.json"),
                   {"version": 1, "notify": {"user_id": "ou_x", "chat_id": "oc_x", "mention": ""}})
        core.jdump(os.path.join(self.ws, "out", "latest.json"),
                   {"run_at": core.now_iso(), "changes": [], "summary": {}})

    def _run(self, rec):
        jp = os.path.join(self.ws, "judged.json")
        core.jdump(jp, {"intel": [rec], "facts": []})
        return judge.cmd_input(self.ws, jp)

    def test_tech_brief_without_playbook_rejected(self):
        """技术战报 notify=true 但缺 sales_playbook → exit 2 拒落盘。"""
        rec = {"change_id": "x1", "type": "技术战报", "significance": "high",
               "title": "某模型降价 40%", "summary": "API 价格下调。",
               "url": "https://e/1", "notify": True}
        with self.assertRaises(SystemExit) as ctx:
            self._run(rec)
        self.assertEqual(ctx.exception.code, 2)

    def test_tech_brief_with_short_playbook_rejected(self):
        """空话（"我们更稳定更安全"）字数不足 → 同样拒。"""
        rec = {"change_id": "x2", "type": "技术战报", "significance": "high",
               "title": "某模型降价", "summary": "s", "url": "https://e/1",
               "notify": True, "sales_playbook": "我们更稳定更安全"}
        with self.assertRaises(SystemExit) as ctx:
            self._run(rec)
        self.assertEqual(ctx.exception.code, 2)

    def test_tech_brief_with_playbook_passes(self):
        """合格 sales_playbook → 正常落盘并渲染进卡片。"""
        rec = {"change_id": "x3", "type": "技术战报", "significance": "high",
               "title": "某模型降价 40%", "summary": "API 价格下调。",
               "url": "https://e/1", "notify": True,
               "sales_playbook": ("竞对降价 40% 后客户必拿价格说事。防守：算总成本不只看 token 单价，"
                                  "骨干网直连让跨区时延低 30%+；合规杠杆用本地 Region 数据驻留。"
                                  "话术：单价贵一点，但你少两个合规风险。")}
        self.assertEqual(self._run(rec), 0)
        cards = [f for f in os.listdir(os.path.join(self.ws, "out")) if f.startswith("card-")]
        blob = "".join(open(os.path.join(self.ws, "out", c), encoding="utf-8").read()
                       for c in cards)
        self.assertIn("销售对标指引", blob, "sales_playbook 必须渲染进卡片，不能只在 JSON 里")

    def test_strategy_without_guidance_rejected(self):
        """战略风向 notify=true 但缺 sales_strategy → exit 2。"""
        rec = {"change_id": "x4", "type": "战略风向", "significance": "high",
               "title": "中东短剧增长 120%", "summary": "s", "url": "https://e/1",
               "notify": True}
        with self.assertRaises(SystemExit) as ctx:
            self._run(rec)
        self.assertEqual(ctx.exception.code, 2)

    def test_strategy_with_guidance_passes(self):
        """合格 sales_strategy → 落盘并渲染。"""
        rec = {"change_id": "x5", "type": "战略风向", "significance": "high",
               "title": "中东短剧增长 120%", "summary": "s", "url": "https://e/1",
               "notify": True,
               "sales_strategy": ("中东短剧暴涨 → 客户要同时解决多区域低延迟与当地内容合规。"
                                  "本周重点抓中东短剧出海团队（刚拿 A/B 轮正在铺量的），"
                                  "主推多区域边缘节点 + 数据驻留合规组合方案。")}
        self.assertEqual(self._run(rec), 0)
        cards = [f for f in os.listdir(os.path.join(self.ws, "out")) if f.startswith("card-")]
        blob = "".join(open(os.path.join(self.ws, "out", c), encoding="utf-8").read()
                       for c in cards)
        self.assertIn("销售战略建议", blob)

    def test_notify_false_needs_no_guidance(self):
        """notify=false 的归档记录不要求销售指引（不强迫为归档编话术）。"""
        rec = {"change_id": "x6", "type": "技术战报", "significance": "low",
               "title": "某小更新", "summary": "s", "url": "https://e/1",
               "notify": False}
        self.assertEqual(self._run(rec), 0)


class TestRules(unittest.TestCase):
    RULES = {
        "version": 1,
        "scope": {"industry": {"any_of": ["AI", "电商", "游戏"]}},
        "signal_groups": [
            {"id": "g1", "name": "近期融资且在招", "weight": 70, "all_of": [
                {"type": "funding_event", "within_days": 180},
                {"type": "hiring", "within_days": 30, "keywords_any": ["算法"]}]},
            {"id": "g2", "name": "大额融资", "weight": 65, "all_of": [
                {"type": "funding_event", "within_days": 180, "min_amount_cny": 1e7}]},
        ],
        "scoring": {"threshold": 60, "extra_group_bonus": 10},
    }
    NOW = core.now_iso()

    def _fact(self, **kw):
        base = {"industries": ["AI"],
                "funding_events": [{"date": self.NOW, "round": "B轮", "amount_text": "3亿美元",
                                    "amount_cny": 3 * 7.2e8}],
                "hiring_signals": [{"keywords": ["算法工程师"], "page_url": "u", "last_seen": self.NOW}]}
        base.update(kw)
        return base

    def test_scope_gates_everything(self):
        """行业不符：即使融资+招聘全中也不命中（耦合条件只认 scope）。"""
        r = match_mod.evaluate_entity(self._fact(industries=["教育"]), self.RULES)
        self.assertEqual(r["matched_groups"], [])

    def test_group_or(self):
        """只满足一组（大额融资，无招聘）也命中。"""
        r = match_mod.evaluate_entity(self._fact(hiring_signals=[]), self.RULES)
        self.assertEqual(r["matched_groups"], ["g2"])

    def test_both_groups_score_bonus(self):
        r = match_mod.evaluate_entity(self._fact(), self.RULES)
        self.assertEqual(set(r["matched_groups"]), {"g1", "g2"})
        self.assertEqual(r["score"], 70 + 10)

    def test_amount_gate_conservative(self):
        """金额解析不出时不过 min_amount 门槛（宁可不命中，不虚报）。"""
        fact = self._fact(funding_events=[{"date": self.NOW, "round": "天使轮", "amount_text": None}],
                          hiring_signals=[])
        r = match_mod.evaluate_entity(fact, self.RULES)
        self.assertEqual(r["matched_groups"], [])

    def test_unknown_type_rejected(self):
        bad = json.loads(json.dumps(self.RULES))
        bad["signal_groups"][0]["all_of"][0]["type"] = "magic_signal"
        with self.assertRaises(SystemExit):
            match_mod.validate_rules(bad)

    def test_bad_tier_rejected(self):
        bad = json.loads(json.dumps(self.RULES))
        bad["signal_groups"][0]["tier"] = "P5"
        with self.assertRaises(SystemExit):
            match_mod.validate_rules(bad)

    def test_unknown_scope_dim_rejected(self):
        bad = json.loads(json.dumps(self.RULES))
        bad["scope"]["weather"] = {"type": "industry_tag", "any_of": ["AI"]}
        with self.assertRaises(SystemExit):
            match_mod.validate_rules(bad)


class TestRulesV2(unittest.TestCase):
    """v2 规则口径（需求文档）：三维 scope + P0/P1 分级 + 新 evaluator。"""
    RULES = {
        "version": 2,
        "scope": {
            "industry": {"type": "industry_tag", "any_of": ["游戏", "电商", "短剧"]},
            "origin": {"type": "chinese_company"},
            "global": {"type": "going_global"},
        },
        "signal_groups": [
            {"id": "p0-funding-recent", "name": "近1月融资", "tier": "P0", "weight": 90,
             "all_of": [{"type": "funding_event", "within_days": 30}]},
            {"id": "p0-rank-new-entry", "name": "榜单新晋", "tier": "P0", "weight": 85,
             "all_of": [{"type": "other_event", "kind_any": ["rank_new_entry"], "within_days": 60}]},
            {"id": "p1-funding-180", "name": "近半年融资画像", "tier": "P1", "weight": 60,
             "all_of": [{"type": "funding_event", "within_days": 180}]},
        ],
        "scoring": {"threshold": 60, "extra_group_bonus": 10},
    }
    NOW = core.now_iso()

    def _fact(self, **kw):
        base = {"industries": ["游戏"], "origin": "CN", "going_global": True,
                "funding_events": [{"date": self.NOW, "round": "A轮", "amount_text": "5000万元"}]}
        base.update(kw)
        return base

    def test_p0_priority_and_groups(self):
        r = match_mod.evaluate_entity(self._fact(), self.RULES)
        self.assertEqual(set(r["matched_groups"]), {"p0-funding-recent", "p1-funding-180"})
        self.assertEqual(r["priority"], "P0")

    def test_p1_when_only_profile_groups(self):
        fact = self._fact(funding_events=[{"date": "2026-06-01T00:00:00+08:00",
                                           "round": "天使轮", "amount_text": "1000万元"}])
        r = match_mod.evaluate_entity(fact, self.RULES)
        self.assertEqual(r["matched_groups"], ["p1-funding-180"])
        self.assertEqual(r["priority"], "P1")

    def test_other_event_rank_entry(self):
        fact = self._fact(funding_events=[],
                          other_events=[{"kind": "rank_new_entry", "detail": "新晋 AI产品榜 Top10",
                                         "extracted_at": self.NOW}])
        r = match_mod.evaluate_entity(fact, self.RULES)
        self.assertIn("p0-rank-new-entry", r["matched_groups"])

    def test_scope_blocks_foreign_company(self):
        """非中国企业（origin 未申报且无启发式证据）：画像再好也不命中（需求文档硬筛）。"""
        fact = {"industries": ["游戏"], "funding_events": [{"date": self.NOW, "round": "B轮",
                                                           "amount_text": "$30B"}]}
        r = match_mod.evaluate_entity(fact, self.RULES)
        self.assertEqual(r["matched_groups"], [])

    def test_chinese_heuristics_cny(self):
        """无显式 origin，人民币融资兜底判中。"""
        fact = {"industries": ["游戏"], "going_global": True,
                "funding_events": [{"date": self.NOW, "round": "A轮", "amount_text": "数千万元人民币"}]}
        r = match_mod.evaluate_entity(fact, self.RULES)
        self.assertNotEqual(r["matched_groups"], [])

    def test_going_global_keyword_fallback(self):
        """无显式 going_global，事件文本含出海关键词兜底判中。"""
        fact = {"industries": ["电商"], "origin": "CN",
                "funding_events": [{"date": self.NOW, "round": "A轮", "amount_text": "1亿元"}],
                "other_events": [{"kind": "business_surge", "detail": "海外站点订单暴增3倍",
                                  "extracted_at": self.NOW}]}
        r = match_mod.evaluate_entity(fact, self.RULES)
        self.assertEqual(r["priority"], "P0")


class TestParseWebPage(unittest.TestCase):
    """web_page 两种模式：keywords（招聘页）与 links（资讯列表/榜单页，新出现=added）。"""

    SPEC = {"url": "https://www.example.com/", "config": {"mode": "links",
                                                          "link_pattern": r"example\.com/(p|t)/\w+"}}
    BODY = """<html><body>
      <a href="/p/101.html">第一篇文章标题足够长</a>
      <a href="https://www.example.com/t/abcDEF">第二篇文章的标题也很长</a>
      <a href="/p/101.html">第一篇文章标题足够长</a>
      <a href="https://other.com/x">外部链接不算</a>
      <a href="/more">更多</a>
      <a href="javascript:void(0)">按钮文字</a>
    </body></html>"""

    def test_links_mode_dedupe_and_filter(self):
        entries = collect.parse_web_page(self.BODY, self.SPEC)
        keys = [e["key"] for e in entries]
        self.assertEqual(len(keys), 2, "去重 + 域过滤后应剩 2 条")
        self.assertTrue(all(k.startswith("http") for k in keys))
        self.assertEqual(entries[0]["title"], "第一篇文章标题足够长")

    def test_keywords_mode_still_works(self):
        spec = {"url": "https://c.com/jobs", "config": {"keywords": ["大模型"]}}
        entries = collect.parse_web_page("<html>我们在招大模型工程师</html>", spec)
        self.assertEqual([e["key"] for e in entries], ["kw:大模型"])


class TestNewSourceBaseline(unittest.TestCase):
    """新源首次抓取只建基线，不产变更（2026-10-27 实测修复，用户"线索严重过时"根因）。

    未修复时：新加一个源会把它的全部历史文章（DeepSeek 19 条 + Qwen 40 条，
    最早到 2024-07）当 added 灌进判定窗，用户看到的就是"严重过时的旧闻当新动态"。
    """

    def setUp(self):
        sys.path.insert(0, core.SCRIPT_DIR)
        import tempfile
        self.ws = tempfile.mkdtemp(prefix="mi-newsrc-")
        os.makedirs(os.path.join(self.ws, "state", "streams"), exist_ok=True)
        os.makedirs(os.path.join(self.ws, "log"), exist_ok=True)
        os.makedirs(os.path.join(self.ws, "out"), exist_ok=True)

    def test_no_snapshot_means_baseline_only(self):
        """无 prev 快照 = 新源 → 落基线后跳过，不产变更。"""
        import pipeline
        entries = [{"key": "k%d" % i, "title": "历史旧文%d" % i,
                    "link": "https://e/%d" % i, "published": "2024-07-25T00:00:00+00:00",
                    "summary": "", "text": "t%d" % i} for i in range(5)]
        # 模拟：无 prev
        prev = diffcore.load_snapshot(self.ws, "brand-new-source")
        self.assertEqual(prev, {}, "新源应当没有 prev 快照")
        # 修复逻辑：prev 为空 → 只存快照，不进 diff
        if not prev:
            diffcore.save_snapshot(self.ws, "brand-new-source", entries, core.now_iso())
        after = diffcore.load_snapshot(self.ws, "brand-new-source")
        self.assertEqual(len(after), 5, "新源首次抓取必须落基线快照")
        # 第二轮：基线已在，历史条目不算新增
        added, removed, rolled = diffcore.diff_source(after, entries, windowed=True)
        self.assertEqual(added, [], "第二轮对同一批历史条目不得报 added")
        self.assertEqual(removed, [])


class TestDiff(unittest.TestCase):
    def _e(self, key, pub):
        return {"key": key, "title": key, "link": "https://a/%s" % key, "published": pub,
                "summary": "", "text": key}

    def test_windowed_roll_not_removed(self):
        prev = {"old": self._e("old", "2026-09-01T00:00:00+08:00"),
                "mid": self._e("mid", "2026-10-01T00:00:00+08:00")}
        cur = [self._e("mid", "2026-10-01T00:00:00+08:00"),
               self._e("new", "2026-10-20T00:00:00+08:00")]
        added, removed, rolled = diffcore.diff_source(prev, cur, windowed=True)
        self.assertEqual([e["key"] for e in added], ["new"])
        self.assertEqual(removed, [])
        self.assertEqual([e["key"] for e in rolled], ["old"])

    def test_real_removal_conservative(self):
        """非 windowed：消失就是消失（宁可多报，不把真消失当滚动吃掉）。"""
        prev = {"old": self._e("old", "2026-09-01T00:00:00+08:00")}
        added, removed, rolled = diffcore.diff_source(prev, [], windowed=False)
        self.assertEqual([e["key"] for e in removed], ["old"])
        self.assertEqual(rolled, [])

    def test_full_window_rotation_is_rolled(self):
        """满窗等量替换（定长窗口 10 进 10 出）→ 消失必为挤出，全判滚动。

        2026-10-27 实测事故回归：量子位 RSS 是定长 10 条窗口，每轮 10 进 10 出，
        旧实现依赖 `pub < oldest_cur`，而该源返回非严格时序（含推荐位混排），
        条件永不成立 → 每轮 10 条 removed 噪音进判定窗。
        """
        prev = {k: self._e(k, "Mon, 26 Oct 2026 09:00:00 +0000")
                for k in ("a", "b", "c", "d")}
        cur = [self._e(k, "Tue, 27 Oct 2026 09:00:00 +0000")
               for k in ("c", "d", "e", "f")]
        added, removed, rolled = diffcore.diff_source(prev, cur, windowed=True)
        self.assertEqual(sorted(e["key"] for e in added), ["e", "f"])
        self.assertEqual(removed, [], "满窗等量替换时消失条目全为挤出，不得判 removed")
        self.assertEqual(sorted(e["key"] for e in rolled), ["a", "b"])

    def test_shrink_with_stale_entry_is_rolled(self):
        """收缩但消失条目早于窗口最旧条目 → 仍判滚动（旧闻被挤出）。"""
        prev = {"old": self._e("old", "Mon, 01 Sep 2026 00:00:00 +0000"),
                "mid": self._e("mid", "Mon, 26 Oct 2026 00:00:00 +0000")}
        cur = [self._e("mid", "Mon, 26 Oct 2026 00:00:00 +0000")]
        added, removed, rolled = diffcore.diff_source(prev, cur, windowed=True)
        self.assertEqual(removed, [])
        self.assertEqual([e["key"] for e in rolled], ["old"])

    def test_shrink_recent_entry_is_removed(self):
        """收缩且消失条目时间不旧 → 保守判 removed（宁可多报，不吞真消失）。"""
        prev = {"recent": self._e("recent", "Tue, 27 Oct 2026 09:00:00 +0000"),
                "other": self._e("other", "Tue, 27 Oct 2026 09:00:00 +0000")}
        cur = [self._e("other", "Tue, 27 Oct 2026 09:00:00 +0000")]
        added, removed, rolled = diffcore.diff_source(prev, cur, windowed=True)
        self.assertEqual([e["key"] for e in removed], ["recent"])
        self.assertEqual(rolled, [])

    def test_small_jitter_treated_as_roll(self):
        """列表页条数小幅抖动（±10%）不算真收缩 —— 2026-10-27 活动行实测。

        真实事故：活动行列表页条数在 20/21 之间随机抖动（备份 20、本轮 21），
        旧实现用绝对比较 `len(cur) < len(prev)` → 1 条差异就判「源真变少」
        → 4 条消失全判 removed 噪音，违反 SOP「真增量轮 removed 应为 0」。
        容差按比例 10%：21 → 容差 2，缺口 1 ≤ 2 视为抖动 → 判 rolled。
        且该源 published 几乎全为 None，时间比较本就恒不成立。
        """
        prev = {f"k{i}": self._e(f"k{i}", None) for i in range(21)}
        cur = [self._e(f"k{i}", None) for i in range(3, 21)] + \
              [self._e("new1", None), self._e("new2", None), self._e("new3", None)]
        added, removed, rolled = diffcore.diff_source(prev, cur, windowed=True)
        self.assertEqual(len(removed), 0, "小幅抖动不应产 removed")
        self.assertEqual(len(rolled), 3, "消失条目应判窗口滚动")
        self.assertEqual(len(added), 3)

    def test_real_shrink_without_timestamp_rolls(self):
        """明显收缩但条目无时间戳 → 判 rolled（无时间证据不主张真消失）。"""
        prev = {f"k{i}": self._e(f"k{i}", None) for i in range(20)}
        cur = [self._e(f"k{i}", None) for i in range(10, 20)]   # 20 → 10，缺口超容差
        added, removed, rolled = diffcore.diff_source(prev, cur, windowed=True)
        self.assertEqual(len(removed), 0, "无时间戳时不该主张真消失")
        self.assertEqual(len(rolled), 10)

    def test_windowed_roll_with_rfc2822_published(self):
        """RSS 原生 RFC 2822 时间也必须能识别窗口滚动（2026-10-27 实测事故回归）。

        真实事故：parse_iso 只认 ISO 8601，RSS 的 `Mon, 26 Oct 2026 18:11:43 GMT`
        解析成 None → oldest_cur/pub 全为 None → 窗口滚动分支永不成立 → 每轮
        几十条滚动条目被判「真的消失」进变更窗。本条用 RFC 2822 时间复现同一场景。
        """
        prev = {"old": self._e("old", "Mon, 26 Oct 2026 18:11:43 GMT"),
                "mid": self._e("mid", "Tue, 27 Oct 2026 06:00:00 GMT")}
        cur = [self._e("mid", "Tue, 27 Oct 2026 06:00:00 GMT"),
               self._e("new", "Tue, 27 Oct 2026 09:30:00 GMT")]
        added, removed, rolled = diffcore.diff_source(prev, cur, windowed=True)
        self.assertEqual([e["key"] for e in added], ["new"])
        self.assertEqual(removed, [], "RFC 2822 时间下早于窗口的条目必须判滚动，不得进 removed")
        self.assertEqual([e["key"] for e in rolled], ["old"])


class TestCoverage(unittest.TestCase):
    def test_missing_ids_detected(self):
        latest = {"changes": [{"change_id": "a"}, {"change_id": "b"}, {"change_id": "c"}]}
        missing = judge.coverage_check(latest, [{"change_id": "a"}], [{"change_ids": ["b"]}])
        self.assertEqual(missing, ["c"])

    def test_merge_counts_as_covered(self):
        latest = {"changes": [{"change_id": "a"}, {"change_id": "b"}]}
        missing = judge.coverage_check(latest, [{"change_id": "a", "merged_change_ids": ["b"]}], [])
        self.assertEqual(missing, [])

    def test_already_judged_history_credits_coverage(self):
        """facts 补录/增量判定：历史登记已判的条目不算缺失（facts-only 输入不被误拦）。"""
        latest = {"changes": [{"change_id": "a"}, {"change_id": "b"}]}
        missing = judge.coverage_check(latest, [], [], already_judged={"a", "b"})
        self.assertEqual(missing, [])
        # 历史只覆盖一半：另一半仍须本轮回答
        missing = judge.coverage_check(latest, [], [], already_judged={"a"})
        self.assertEqual(missing, ["b"])

    def test_no_history_still_blocks(self):
        """不传历史（旧调用方式）时行为不变：未答条目照常报缺失。"""
        latest = {"changes": [{"change_id": "a"}]}
        self.assertEqual(judge.coverage_check(latest, [], []), ["a"])


class TestEndToEnd(unittest.TestCase):
    def test_judge_produces_facts_leads_card(self):
        with tempfile.TemporaryDirectory() as ws:
            core.workspace(ws)
            core.jdump(os.path.join(ws, "rules.json"),
                       core.jload(os.path.join(core.SCRIPT_DIR, "..", "config",
                                               "rules.json.example"), None))
            core.jdump(os.path.join(ws, "entities.json"),
                       core.jload(os.path.join(core.SCRIPT_DIR, "..", "config",
                                               "entities.json.example"), None))
            core.jdump(os.path.join(ws, "notify.json"),
                       {"version": 1, "notify": {"user_id": "", "chat_id": "", "mention": "all"}})
            latest = {"run_at": core.now_iso(), "health": {}, "summary": {},
                      "changes": [
                          {"change_id": "chg1", "source_id": "q", "source_name": "量子位", "tier": "T2",
                           "serves": ["lead"], "change_kind": "added", "key": "k1",
                           "url": "https://q/1", "title": "月之暗面完成新一轮融资",
                           "summary": "月之暗面完成 3亿美元 B轮 融资，持续招聘算法工程师。"},
                          {"change_id": "chg2", "source_id": "q", "source_name": "量子位", "tier": "T2",
                           "serves": ["intel"], "change_kind": "added", "key": "k2",
                           "url": "https://q/2", "title": "某地出台数据新政",
                           "summary": "某地出台数据新政。"}]}
            core.jdump(os.path.join(ws, "out", "latest.json"), latest)
            judged = {
                "intel": [
                    {"change_id": "chg1", "discard": True, "discard_reason": "仅作事实原料，不单独通知"},
                    {"change_id": "chg2", "type": "政策监管", "significance": "normal",
                     "title": "某地出台数据新政", "summary": "影响数据合规。",
                     "url": "https://q/2", "sources": [{"name": "量子位", "url": "https://q/2"}],
                     "notify": True, "notify_reason": "行业政策"}],
                "facts": [
                    # 用目标客群（早期轮次的小公司）而非月之暗面——后者已在黑名单
                    # （超级巨头/独角兽），会被 denylist 硬丢弃（2026-10-27 用户明令）
                    {"entity_name": "橘猫宇宙", "aliases": [],
                     "industries": ["AI硬件", "游戏"], "origin": "CN", "going_global": True,
                     "event_type": "funding",
                     "event": {"date": core.now_iso()[:10], "round": "Pre-A轮",
                               "amount_text": "3000万元",
                               "investors": "某基金", "source_name": "量子位",
                               "source_url": "https://q/1"},
                     "change_ids": ["chg1"], "confidence": "high"}]}
            jp = os.path.join(ws, "judged.json")
            core.jdump(jp, judged)
            rc = judge.cmd_input(ws, jp)
            self.assertEqual(rc, 0)
            # 档案与线索
            fact = facts_mod.load_fact(ws, "unknown:橘猫宇宙")
            self.assertEqual(len(fact["funding_events"]), 1)
            leads = core.read_jsonl(os.path.join(ws, "log", "leads.jsonl"))
            self.assertEqual(len(leads), 1)
            # v2 口径：橘猫宇宙（origin=CN/going_global）Pre-A 轮融资 → P0 双组命中
            self.assertIn("p0-funding-recent", leads[0]["matched_groups"])
            self.assertEqual(leads[0]["priority"], "P0")
            self.assertGreaterEqual(leads[0]["score"], 60)
            self.assertTrue(leads[0]["notify"])
            # 卡片与待判核销
            self.assertEqual(len([f for f in os.listdir(os.path.join(ws, "out"))
                                  if f.startswith("card-")]) >= 2, True)
            pending = core.jload(diffcore.pending_path(ws), {"items": []})
            self.assertEqual(pending["items"], [])


class TestAlreadySentMissingPost(unittest.TestCase):
    """重判已落盘记录时不得漏发（2026-10-27 实测漏发，v0.5.1 修复）。

    真实事故：上轮落盘 notify=true 但未发（锚点未推进），本轮带 --already-sent
    重判同一 change_id。旧实现 `if change_id in seen_intel: continue` 跳过该记录，
    内存里的 rec 不加入 notified_intel → 卡片里没有它 → **永久漏发**；
    而 sent-guard 若把 notify 压成 false，则连下一轮也不会再发。
    """

    def setUp(self):
        sys.path.insert(0, core.SCRIPT_DIR)
        self.ws = tempfile.mkdtemp(prefix="mi-resend-")
        core.workspace(self.ws)
        core.jdump(os.path.join(self.ws, "rules.json"),
                   core.jload(os.path.join(core.SCRIPT_DIR, "..", "config",
                                           "rules.json.example"), None))
        core.jdump(os.path.join(self.ws, "entities.json"),
                   core.jload(os.path.join(core.SCRIPT_DIR, "..", "config",
                                           "entities.json.example"), None))
        core.jdump(os.path.join(self.ws, "notify.json"),
                   {"version": 1, "notify": {"user_id": "ou_x", "chat_id": "oc_x", "mention": ""}})
        core.jdump(os.path.join(self.ws, "out", "latest.json"),
                   {"run_at": core.now_iso(), "changes": [], "summary": {}})
        # 已落盘但未发：notify=true，无 suppressed_by_guard
        core.append_jsonl(os.path.join(self.ws, "log", "intel.jsonl"),
                          {"change_id": "pending1", "type": "融资动态",
                           "title": "上轮落盘未发的情报", "summary": "s", "url": "https://e/1",
                           "notify": True, "significance": "high",
                           "logged_at": "2026-10-27T11:00:00+08:00"})

    def test_existing_notify_record_reaches_card(self):
        """已落盘 + 仍 notify=true 的记录必须进入 notified_intel（不得被 skip 吞掉）。"""
        jp = os.path.join(self.ws, "judged.json")
        core.jdump(jp, {"intel": [{"change_id": "pending1", "type": "融资动态",
                                   "title": "上轮落盘未发的情报", "summary": "s",
                                   "url": "https://e/1", "notify": True,
                                   "significance": "high"}], "facts": []})
        # already_sent 指向同一文件但用 sent_before 把它排开（=确实没发过）
        judge.cmd_input(self.ws, jp, already_sent=os.path.join(self.ws, "log", "intel.jsonl"),
                        sent_before="2026-10-27T10:00:00+08:00")
        cards = [f for f in os.listdir(os.path.join(self.ws, "out")) if f.startswith("card-")]
        self.assertTrue(cards, "应产出卡片")
        blob = ""
        for c in cards:
            blob += open(os.path.join(self.ws, "out", c), encoding="utf-8").read()
        self.assertIn("上轮落盘未发的情报", blob,
                      "未发过的记录必须出现在卡片里——被 seen_intel 跳掉就是漏发")


class TestBitable(unittest.TestCase):
    """猎隼线索池写入预设：轮次归一 / 标签映射 / 记录构建。"""

    OPTIONS = {
        "所属赛道": {"AI应用", "内容创作", "具身智能", "AI基础设施", "AI硬件"},
        "标签细分": {"游戏", "电商", "短剧", "硬件", "AIGC", "机器人", "制造", "北美", "东南亚", "欧洲"},
        "融资动态": {"种子轮", "天使轮", "Pre-A轮", "A轮", "A+轮", "B轮", "战略融资", "未融资"},
    }

    def setUp(self):
        sys.path.insert(0, core.SCRIPT_DIR)
        import bitable
        self.bitable = bitable
        self.cfg = {
            "app_token": "x", "table_id": "x", "enabled": True,
            "default_status": "未跟进",
            "field_map": {"company": "公司名称", "desc": "一句话业务描述", "track": "所属赛道",
                          "tags": "标签细分", "round": "融资动态", "investors": "背后资方/投资机构",
                          "media": "曝光媒体/合作渠道", "gcp": "GCP切入点", "status": "跟进状态",
                          "captured_at": "抓取时间", "website": "官网链接"},
            "maps": {
                "industry_to_track": {"游戏": "内容创作", "应用": "AI应用", "AI硬件": "AI硬件"},
                "industry_to_tags": {"游戏": "游戏", "电商": "电商", "AI硬件": "硬件", "互联网": None,
                                     "_regions": {"北美": "美国|TikTok", "东南亚": "印尼|东南亚"}},
                "source_to_media": {"品牌方舟": "品牌方舟", "白鲸出海": "白鲸出海", "TechCrunch": None},
            },
        }

    def test_norm_round(self):
        f = self.bitable.norm_round
        self.assertEqual(f("A+"), "A+轮")
        self.assertEqual(f("pre-A"), "Pre-A轮")
        self.assertEqual(f("B轮"), "B轮")
        self.assertEqual(f("战略"), "战略融资")
        self.assertEqual(f("天使"), "天使轮")
        self.assertIsNone(f(""))
        self.assertIsNone(f("C3轮不准的", {"B轮"}))  # 不在选项则拒绝

    def test_map_tags_regions(self):
        tags = self.bitable.map_tags(
            ["游戏", "互联网"], "TikTok 在美国的下载量暴涨", self.cfg["maps"]["industry_to_tags"],
            self.OPTIONS["标签细分"])
        self.assertIn("游戏", tags)
        self.assertIn("北美", tags)
        self.assertNotIn("互联网", tags)

    def test_build_record(self):
        import datetime as dt
        lead = {
            "entity_name": "点点互动", "industries": ["游戏"], "score": 80,
            "summary": "二合新游上线", "url": "http://x/a",
            "sources": [{"name": "白鲸出海", "url": "http://x/a"}],
            "evidence": [{"detail": "发布新游", "group": "p0"}],
            "logged_at": "2026-10-23T18:00:00+08:00",
        }
        fact = {"profile": "出海游戏厂商", "opportunity": "买量与云成本",
                "funding_events": [{"round": "A+", "investors": "腾讯", "date": "2026-09-01"}]}
        rec = self.bitable.build_record(lead, fact, self.cfg, options=self.OPTIONS)
        self.assertEqual(rec["公司名称"], "点点互动")
        self.assertEqual(rec["所属赛道"], ["内容创作"])
        self.assertEqual(rec["标签细分"], ["游戏"])
        self.assertEqual(rec["融资动态"], ["A+轮"])
        self.assertEqual(rec["背后资方/投资机构"], "腾讯")
        self.assertEqual(rec["跟进状态"], ["未跟进"])
        self.assertEqual(rec["GCP切入点"], "买量与云成本")
        self.assertTrue(rec["官网链接"].startswith("[暂无官网(新闻报道)](http://x/a)"))
        self.assertIsInstance(rec["抓取时间"], int)
        # 选项约束：无效标签被剔除
        lead["industries"] = ["互联网"]
        rec2 = self.bitable.build_record(lead, {}, self.cfg, options=self.OPTIONS)
        self.assertNotIn("标签细分", rec2)
        self.assertNotIn("所属赛道", rec2)


class TestEnrichment(unittest.TestCase):
    """AI 调研补全（log/enrichments.jsonl）：fields 合并进记录、select 过选项校验、空值不写。"""

    def setUp(self):
        sys.path.insert(0, core.SCRIPT_DIR)
        import bitable
        self.bitable = bitable
        self.cfg = {"field_map": {}, "default_status": "未跟进"}
        self.opts = {"企业规模": {"10-99", "未公开"}, "成立年限": {"2023年", "未公开"},
                     "所在城市/区域": {"深圳", "北京"}}

    def test_apply_enrichment_merges_and_validates(self):
        rec = {"公司名称": "X", "所属赛道": ["AI应用"]}
        enr = {"tech_stack": "智谱AI", "ceo": "张三 (创始人)", "size": "10-99",
               "founded": "2023年", "city": ["深圳", "火星"], "contact": "x@x.ai",
               "icebreaker": "您好，祝贺……", "website": "https://x.ai",
               "investors": "红杉中国"}
        self.bitable.apply_enrichment(rec, {}, self.cfg, enr, options=self.opts)
        self.assertEqual(rec["技术底座"], "智谱AI")
        self.assertEqual(rec["CEO/高管姓名"], "张三 (创始人)")
        self.assertEqual(rec["企业规模"], ["10-99"])
        self.assertEqual(rec["成立年限"], ["2023年"])
        self.assertEqual(rec["所在城市/区域"], ["深圳"], "非法城市选项必须丢弃")
        self.assertEqual(rec["联系方式(LinkedIn/邮箱)"], "x@x.ai")
        self.assertEqual(rec["打单破冰话术"], "您好，祝贺……")
        self.assertEqual(rec["背后资方/投资机构"], "红杉中国")
        self.assertEqual(rec["官网链接"], "[官网](https://x.ai)")

    def test_apply_enrichment_none_noop(self):
        rec = {"公司名称": "X"}
        self.bitable.apply_enrichment(rec, {}, self.cfg, None)
        self.assertEqual(rec, {"公司名称": "X"})

    def test_apply_enrichment_bad_select_dropped(self):
        rec = {}
        enr = {"size": "一百人", "city": "北京"}
        self.bitable.apply_enrichment(rec, {}, self.cfg, enr, options=self.opts)
        self.assertNotIn("企业规模", rec)
        self.assertEqual(rec["所在城市/区域"], ["北京"])

    def test_load_enrichments_latest_wins(self):
        with tempfile.TemporaryDirectory() as ws:
            core.workspace(ws)
            log = os.path.join(ws, "log", "enrichments.jsonl")
            with open(log, "w", encoding="utf-8") as f:
                f.write(json.dumps({"entity_id": "a", "fields": {"ceo": "旧"}}, ensure_ascii=False) + "\n")
                f.write(json.dumps({"entity_id": "a", "fields": {"ceo": "新"}}, ensure_ascii=False) + "\n")
                f.write(json.dumps({"entity_id": "b", "fields": {"size": "10-99"}}, ensure_ascii=False) + "\n")
            out = self.bitable.load_enrichments(ws)
            self.assertEqual(out["a"]["ceo"], "新")
            self.assertEqual(out["b"]["size"], "10-99")


class TestLeadCardJump(unittest.TestCase):
    """线索卡「查看线索」跳转：record 级定位 + 回退表视图 + 活动行粗体渲染。"""

    def setUp(self):
        sys.path.insert(0, core.SCRIPT_DIR)

    def test_lead_pool_url(self):
        view = "https://webeye.feishu.cn/base/x?table=t&view=v"
        self.assertEqual(judge.lead_pool_url({"bitable_record_id": "rec1"}, view),
                         view + "&record=rec1")
        self.assertEqual(judge.lead_pool_url({}, view), view)
        self.assertIsNone(judge.lead_pool_url({}, None))

    def test_lead_blocks_has_view_button(self):
        leads = [{"entity_name": "深言科技", "industries": ["AI"], "priority": "P0",
                  "score": 90, "matched_group_names": ["p0-funding-recent"],
                  "evidence": [{"detail": "近期融资"}],
                  "sources": [{"name": "量子位", "url": "https://q/1"}],
                  "bitable_record_id": "rec9"}]
        blocks = judge.lead_blocks(leads, view_url="https://x/base?table=t&view=v")
        btns = [e for b in blocks for e in (b.get("elements") or [])
                if isinstance(e, dict) and e.get("tag") == "button"]
        self.assertTrue(btns, "每条线索来源旁必须有查看线索按钮")
        self.assertEqual(btns[0]["behaviors"][0]["default_url"],
                         "https://x/base?table=t&view=v&record=rec9")
        self.assertIn("查看线索", btns[0]["text"]["content"])

    def test_card_fields_not_lost(self):
        """各卡片渲染面完整性：数据有就必须渲染，不许静默丢字段。"""
        lead = {"entity_name": "美团", "industries": ["电商/互联网"], "priority": "P0",
                "score": 80, "matched_group_names": ["p0-surge"], "evidence": [],
                "profile": "本地生活巨头，Keeta 出海", "opportunity": "拉美节点与多云成本",
                "sources": [{"name": "白鲸出海", "url": "https://bw/1"}]}
        # 独立线索卡：业务描述 + 最大机会 + 来源行都要在
        blocks = judge.lead_blocks([lead], view_url=None)
        text = "".join(e["content"] for b in blocks for e in (b.get("elements") or [])
                       if isinstance(e, dict) and e.get("tag") == "markdown")
        self.assertIn("业务描述：本地生活巨头", text)
        self.assertIn("最大机会：拉美节点", text)
        self.assertIn("来源 · [白鲸出海]", text)
        # 内参卡潜在客户段：命中规则行要在
        els = judge._lead_elements([lead], view_url=None)
        md = " ".join(e["content"] for e in els if e.get("tag") == "markdown")
        self.assertIn("命中规则：p0-surge", md)
        self.assertIn("来源 · [白鲸出海]", md)
        # 内参卡新闻段与活动段：来源行要在
        rec = {"title": "T", "summary": "S", "type": "产品发布", "significance": "important",
               "sources": [{"name": "钛媒体", "url": "https://tm/1"}],
               "url": "https://tm/1"}
        news = judge._news_elements([rec])
        self.assertTrue(any("来源 · [钛媒体]" in e.get("content", "") for e in news))
        act = judge._activity_elements([{**rec, "type": "活动情报",
                                         "activity": {"name": "GTC", "time": "11月"}}])
        self.assertTrue(any("来源 · [钛媒体]" in e.get("content", "") for e in act))

    def test_filtered_leads_excluded_and_button_honest(self):
        """filtered 线索不进卡片；未入池线索不能声称"已登记"。"""
        fresh = {"entity_name": "美团", "industries": ["电商/互联网"], "priority": "P0",
                 "score": 80, "matched_group_names": ["p0-surge"], "evidence": [],
                 "sources": [{"name": "白鲸出海", "url": "https://bw/1"}],
                 "bitable_status": "exists", "bitable_record_id": "rec1",
                 "logged_at": "2026-10-23T18:00:00+08:00"}
        stale = {"entity_name": "OpenAI", "industries": ["AI"], "priority": "P0",
                 "score": 90, "matched_group_names": ["p0-funding"], "evidence": [],
                 "bitable_status": "filtered",
                 "logged_at": "2026-10-23T17:00:00+08:00"}
        pending = {"entity_name": "新线索公司", "industries": ["游戏"], "priority": "P1",
                   "score": 60, "matched_group_names": ["p1-profile"], "evidence": [],
                   "bitable_status": "pending",
                   "logged_at": "2026-10-23T18:30:00+08:00"}
        view = "https://x/base?table=t&view=v"
        blocks = judge.lead_blocks([stale, fresh, pending], view_url=view)
        text = json.dumps(blocks, ensure_ascii=False)
        self.assertNotIn("OpenAI", text, "filtered 线索不得出现在卡片")
        self.assertIn("新线索公司", text)
        # 内参卡：未入池的不许写"已登记"，必须用中性文案
        els = judge._lead_elements([stale, fresh, pending], view_url=view)
        md = json.dumps(els, ensure_ascii=False)
        self.assertNotIn("OpenAI", md)
        has_honest = any("查看【猎隼线索池】" in (e.get("text", {}).get("content") or "")
                         for e in els if e.get("tag") == "button")
        self.assertTrue(has_honest, "未入池线索按钮文案必须中性")
        self.assertNotIn("新线索公司已登记", md.replace("**新线索公司", ""),
                         "未入池线索不能声称已登记")

    def test_activity_bold_renders(self):
        recs = [{"type": "活动情报", "activity": {"name": "WAIC 2026", "time": "2026-11-01",
                                                 "location": "上海", "highlight": "出海论坛"}}]
        els = judge._activity_elements(recs)
        content = els[0]["content"]
        self.assertIn("**时间：** ", content, "闭合粗体后必须跟空格，否则星号原样显示")
        self.assertIn("**地点：** ", content)
        self.assertIn("**看点：** ", content)

    def test_activity_elements_high_first(self):
        """活动日历截断前按 significance 排序：high 不能被原序靠前的 normal 挤出。"""
        recs = [
            {"type": "活动情报", "significance": "normal",
             "activity": {"name": "普通活动A", "time": "t1"}},
            {"type": "活动情报", "significance": "normal",
             "activity": {"name": "普通活动B", "time": "t2"}},
            {"type": "活动情报", "significance": "high",
             "activity": {"name": "重点物流展", "time": "t3"}},
        ]
        els = judge._activity_elements(recs, limit=2)
        md = " ".join(e.get("content", "") for e in els)
        self.assertIn("重点物流展", md, "high 活动必须入选，即使原序靠后")
        self.assertNotIn("普通活动B", md)

    def test_activity_no_highlight_prefix_dup(self):
        """highlight 不自带"看点："前缀（模板已加），修正后落盘记录也不应再带。"""
        recs = [{"type": "活动情报", "activity": {"name": "X", "highlight": "看点：重复前缀"}}]
        els = judge._activity_elements(recs)
        self.assertNotIn("看点：看点：", els[0]["content"])

    def test_lead_sources_includes_other_events(self):
        """other_event 命中的线索（业务暴增等）也要有来源行，不能只剩融资+招聘。"""
        fact = {"funding_events": [],
                "other_events": [{"detail": "Keeta 巴西扩张", "source_name": None,
                                  "source_url": "http://www.baijing.cn/article/57116"}],
                "hiring_signals": []}
        srcs = judge.lead_sources(fact)
        self.assertEqual(srcs, [{"name": "动态报道", "url": "http://www.baijing.cn/article/57116"}])
        blocks = judge.lead_blocks([{"entity_name": "美团", "industries": ["电商/互联网"],
                                     "priority": "P0", "score": 80,
                                     "matched_group_names": ["p0-surge"], "evidence": [],
                                     "sources": srcs}], view_url=None)
        content = "".join(e["content"] for b in blocks
                          for e in (b.get("elements") or []) if e.get("tag") == "markdown")
        self.assertIn("来源 · [动态报道](http://www.baijing.cn/article/57116)", content)


class TestGroupMention(unittest.TestCase):
    """群卡 @ 提醒：notify.mention 驱动（all/列表/空），沿用 change-watch <at id=all></at> 语法。"""

    def setUp(self):
        sys.path.insert(0, core.SCRIPT_DIR)

    def test_mention_md_variants(self):
        self.assertEqual(judge.mention_md({"mention": "all"}), "<at id=all></at>")
        self.assertEqual(judge.mention_md({"mention": ["ou_a", "ou_b"]}),
                         "<at id=ou_a></at> <at id=ou_b></at>")
        self.assertEqual(judge.mention_md({"mention": ""}), "")
        self.assertEqual(judge.mention_md({}), "")
        self.assertEqual(judge.mention_md(None), "")

    def test_mention_lead_element(self):
        el = judge._mention_lead("⚠️ 含重要动态", {"mention": "all"})
        self.assertIsNotNone(el)
        self.assertEqual(el["tag"], "markdown")
        self.assertIn("<at id=all></at>", el["content"])
        self.assertIn("⚠️ 含重要动态", el["content"])
        self.assertIsNone(judge._mention_lead("x", {}), "mention 为空不产元素")

    def test_group_card_contains_mention(self):
        at = judge._mention_lead("⚠️ 含重要动态，请关注", {"mention": "all"})
        card = judge.build_card("市场情报简报 · 重要", "t", [at] + [{"tag": "markdown", "content": "b"}],
                                template="red")
        s = json.dumps(card, ensure_ascii=False)
        self.assertIn("<at id=all></at>", s)


class TestIntelCardStyle(unittest.TestCase):
    """视觉差异纪律：情报卡族紫色系（violet 头 + report 图标 + violet 块），
    与 change-watch 蓝色监控卡区分；改回蓝/丢图标=回归。"""

    def setUp(self):
        sys.path.insert(0, core.SCRIPT_DIR)

    def test_default_template_is_violet(self):
        card = judge.build_card("市场情报简报", "t", [{"tag": "markdown", "content": "b"}])
        self.assertEqual(card["header"]["template"], "violet",
                         "情报卡默认头应为 violet，与 change-watch 的 blue 监控卡区分")

    def test_intel_blocks_are_violet(self):
        blocks, _ = judge.intel_blocks([{"significance": "normal", "type": "动态",
                                         "title": "t", "summary": "s", "sources": []}])
        self.assertTrue(blocks)
        blk = blocks[0]
        self.assertEqual(blk["border_color"], "violet-100")
        self.assertEqual(blk["background_style"], "violet-50")

    def test_intel_card_carries_report_icon(self):
        card = judge.build_card("市场情报简报", "t", [], template="violet",
                                icon={**judge.INTEL_ICON, "color": "violet"})
        self.assertEqual(card["header"]["icon"]["token"], "report_outlined")
        self.assertEqual(card["header"]["icon"]["color"], "violet")
        # 语义色不动：群卡强提醒仍为 red 头
        gcard = judge.build_card("市场情报简报 · 重要", "t", [], template="red")
        self.assertEqual(gcard["header"]["template"], "red")


class TestDigestAnchor(unittest.TestCase):
    """日报锚点：必须同时覆盖 intel 与 leads 的 logged_at，漏 leads 会重复展示已发线索。"""

    def setUp(self):
        sys.path.insert(0, core.SCRIPT_DIR)
        import tempfile
        self.ws = tempfile.mkdtemp(prefix="mi-digest-")
        os.makedirs(os.path.join(self.ws, "log"), exist_ok=True)
        with open(os.path.join(self.ws, "log", "intel.jsonl"), "w", encoding="utf-8") as f:
            f.write(json.dumps({"change_id": "a", "logged_at": "2026-10-23T08:00:00+08:00"}, ensure_ascii=False) + "\n")
        with open(os.path.join(self.ws, "log", "leads.jsonl"), "w", encoding="utf-8") as f:
            f.write(json.dumps({"lead_id": "b", "logged_at": "2026-10-23T21:00:00+08:00"}, ensure_ascii=False) + "\n")

    def test_commit_anchor_covers_leads(self):
        judge.cmd_digest(self.ws, commit=True)
        anchor = core.jload(os.path.join(self.ws, "state", "digest_anchor.json"), {}).get("anchor")
        self.assertEqual(anchor, "2026-10-23T21:00:00+08:00",
                         "锚点取 intel+leads 的 max logged_at；只看 intel 会漏掉更晚的线索")

    def test_window_includes_lead_after_anchor(self):
        core.jdump(os.path.join(self.ws, "state", "digest_anchor.json"),
                   {"anchor": "2026-10-23T08:00:00+08:00"})
        # 模拟 cmd_digest 的窗口过滤：锚点之后落盘的线索必须进窗口
        anchor = "2026-10-23T08:00:00+08:00"
        leads = [l for l in core.read_jsonl(os.path.join(self.ws, "log", "leads.jsonl"))
                 if (core.parse_iso(l.get("logged_at")) or 0) > (core.parse_iso(anchor) or 0)]
        self.assertEqual(len(leads), 1)

    def test_digest_commit_combined_produces_card_and_anchor(self):
        """--digest --commit 连用必须先产卡再推锚（2026-10-27 实测坑：旧实现
        commit 分支提前 return，连用只推锚点、摘要卡从未产出且无提示）。"""
        with open(os.path.join(self.ws, "log", "intel.jsonl"), "w", encoding="utf-8") as f:
            f.write(json.dumps({"change_id": "a", "type": "融资事件",
                                "title": "测试公司完成融资", "summary": "测试",
                                "url": "https://example.com", "notify": True,
                                "significance": "normal",
                                "logged_at": "2026-10-23T08:00:00+08:00"},
                               ensure_ascii=False) + "\n")
        judge.cmd_digest(self.ws, commit=True)
        cards = [f for f in os.listdir(os.path.join(self.ws, "out"))
                 if f.startswith("card-daily-") or f.startswith("card-digest-")]
        self.assertTrue(cards, "连用 --commit 也必须产出摘要卡，不许静默跳过")
        anchor = core.jload(os.path.join(self.ws, "state", "digest_anchor.json"), {}).get("anchor")
        self.assertEqual(anchor, "2026-10-23T21:00:00+08:00", "产卡后锚点正常推进")

    def test_commit_marks_window_records_as_sent(self):
        """锚点推进时必须给窗口内记录补 suppressed_by_guard（2026-10-27 实测缺口）。

        旧实现只改 anchor 不打标 → 记录永远留着 notify=true 且无「已通报」标记。
        平时无害（锚点之后才进窗口），但锚点一旦重置/回滚，它们会以旧闻身份
        重新推进内参卡（旧闻重推）。锚点覆盖 = 已随内参发出，两件事必须原子完成。
        """
        with open(os.path.join(self.ws, "log", "intel.jsonl"), "w", encoding="utf-8") as f:
            f.write(json.dumps({"change_id": "old", "type": "融资事件",
                                "title": "窗口内已发情报", "summary": "s", "url": "u",
                                "notify": True, "significance": "high",
                                "logged_at": "2026-10-23T08:00:00+08:00"},
                               ensure_ascii=False) + "\n")
            f.write(json.dumps({"change_id": "future", "type": "融资事件",
                                "title": "窗口外未发情报", "summary": "s", "url": "u",
                                "notify": True, "significance": "high",
                                "logged_at": "2026-10-24T08:00:00+08:00"},
                               ensure_ascii=False) + "\n")
        # 锚点在 10-23 08:00 之前 → old 在窗口内，future 也在窗口内；
        # 用 mark_sent 显式验证：推进后窗口内记录全部被打标
        judge._advance_anchor(self.ws, None, "2026-10-27T10:00:00+08:00", mark_sent=True)
        rows = {r["change_id"]: r for r in core.read_jsonl(
            os.path.join(self.ws, "log", "intel.jsonl"))}
        self.assertTrue(rows["old"].get("suppressed_by_guard"),
                        "锚点推进覆盖的记录必须补「已通报」标记")
        self.assertFalse(rows["old"].get("notify"), "已通报记录的 notify 应压为 False")
        self.assertTrue(rows["future"].get("suppressed_by_guard"))

    def test_commit_marks_record_exactly_on_anchor(self):
        """logged_at 恰好等于锚点的记录也必须补标（2026-10-27 实测边界缺陷）。

        旧实现用 `ts > anchor_ts`（严格大于）→ 落在锚点上的记录永远补不上标，
        停在 notify=true 且无 suppressed_by_guard：digest 窗口用 `> anchor` 同样
        排除它（当前不重推），但锚点一旦回滚它就变成漏网旧闻。实测 6 条历史情报
        正是此状态。锚点语义是「已覆盖到的时刻」，边界包含，故用 >=。
        """
        T = "2026-10-27T11:18:50+08:00"
        with open(os.path.join(self.ws, "log", "intel.jsonl"), "w", encoding="utf-8") as f:
            f.write(json.dumps({"change_id": "on_anchor", "type": "融资事件",
                                "title": "落盘时刻恰等于锚点", "summary": "s", "url": "u",
                                "notify": True, "significance": "high",
                                "logged_at": T}, ensure_ascii=False) + "\n")
        judge._advance_anchor(self.ws, T, "2026-10-27T12:00:00+08:00", mark_sent=True)
        r = core.read_jsonl(os.path.join(self.ws, "log", "intel.jsonl"))[0]
        self.assertTrue(r.get("suppressed_by_guard"),
                        "logged_at == 锚点 的记录必须补标（边界包含）")
        self.assertFalse(r.get("notify"))


class TestHeroImage(unittest.TestCase):
    """内参自定义头图：img_key 直通 / 本地路径上传缓存 / 三级优先级 / 异常回退。"""

    def setUp(self):
        import tempfile
        self.ws = tempfile.mkdtemp(prefix="mi-hero-")
        os.makedirs(os.path.join(self.ws, "state"), exist_ok=True)
        core.jdump(os.path.join(self.ws, "notify.json"),
                   {"notify": {"user_id": "ou_x", "chat_id": "oc_x"}})

    def test_img_key_passthrough(self):
        """CLI 传入现成 img_v3 key → 直通不触碰上传。"""
        self.assertEqual(judge.resolve_banner_key(self.ws, "img_v3_abc"), "img_v3_abc")

    def test_notify_hero_image_config(self):
        """notify.hero_image 配置 img_v3 key（未传 CLI 参数）→ 生效（配置单源）。"""
        core.jdump(os.path.join(self.ws, "notify.json"),
                   {"notify": {"user_id": "ou_x", "chat_id": "oc_x",
                               "hero_image": "img_v3_from_config"}})
        self.assertEqual(judge.resolve_banner_key(self.ws), "img_v3_from_config")

    def test_cli_overrides_notify(self):
        """CLI --hero-image 优先于 notify.hero_image。"""
        core.jdump(os.path.join(self.ws, "notify.json"),
                   {"notify": {"user_id": "ou_x", "chat_id": "oc_x",
                               "hero_image": "img_v3_from_config"}})
        self.assertEqual(judge.resolve_banner_key(self.ws, "img_v3_from_cli"),
                         "img_v3_from_cli")

    def test_local_path_upload_and_cache(self):
        """本地路径 → 上传换 key 并写缓存；同图第二次命中缓存不再上传。"""
        import hashlib, tempfile
        f = os.path.join(self.ws, "banner.png")
        open(f, "wb").write(b"png-bytes")
        calls = []
        orig = judge._upload_hero_image
        judge._upload_hero_image = lambda p: calls.append(p) or "img_v3_uploaded"
        try:
            self.assertEqual(judge.resolve_banner_key(self.ws, f), "img_v3_uploaded")
            self.assertEqual(len(calls), 1)
            # 缓存生效
            self.assertEqual(judge.resolve_banner_key(self.ws, f), "img_v3_uploaded")
            self.assertEqual(len(calls), 1, "同图第二次必须命中缓存，不得重复上传")
            cache = core.jload(os.path.join(self.ws, "state", "hero_images.json"), {})
            self.assertEqual(cache[hashlib.sha1(b"png-bytes").hexdigest()],
                             "img_v3_uploaded")
        finally:
            judge._upload_hero_image = orig

    def test_missing_file_falls_back(self):
        """路径不存在 → 返回 None 回退默认头图（不阻断产卡）。"""
        self.assertIsNone(judge.resolve_banner_key(self.ws, "/nonexistent/banner.png"))

    def test_upload_failure_falls_back(self):
        """上传失败 → 返回 None 回退默认头图，不抛异常。"""
        orig = judge._upload_hero_image
        judge._upload_hero_image = lambda p: None
        try:
            f = os.path.join(self.ws, "banner.png")
            open(f, "wb").write(b"x")
            self.assertIsNone(judge.resolve_banner_key(self.ws, f))
        finally:
            judge._upload_hero_image = orig

    def test_extract_image_key_from_various_forms(self):
        """纯函数：从飞书图片链接/混杂文本提取 image_key；普通 URL 提不出。"""
        self.assertEqual(
            judge._extract_image_key("https://example.feishu.cn/space/api/img_v3_0216s_4204751b-aa/x"),
            "img_v3_0216s_4204751b-aa")
        self.assertEqual(
            judge._extract_image_key("就用这张 img_v3_02ba_abcdef-12 谢谢"),
            "img_v3_02ba_abcdef-12")
        self.assertIsNone(judge._extract_image_key("https://pics.example.com/banner.png"))
        self.assertIsNone(judge._extract_image_key(""))

    def test_feishu_link_embedded_key_passthrough(self):
        """飞书图片链接（内嵌 key）→ 提取直通，不下载不上传。"""
        url = "https://example.feishu.cn/file/img_v3_0216s_4204751b-aa-11"
        calls = []
        orig = judge._upload_hero_image
        judge._upload_hero_image = lambda p: calls.append(p) or "should_not_call"
        try:
            self.assertEqual(judge.resolve_banner_key(self.ws, url),
                             "img_v3_0216s_4204751b-aa-11")
            self.assertEqual(calls, [], "内嵌 key 链接必须直通，不得触碰上传")
        finally:
            judge._upload_hero_image = orig

    def test_http_url_download_and_upload(self):
        """http 图片链接 → mock 下载 + mock 上传 → 换 key；URL 二次命中缓存。"""
        import hashlib, tempfile
        url = "https://pics.example.com/event-banner.png"
        dl = []
        def fake_download(u, dst):
            dl.append(u)
            open(dst, "wb").write(b"downloaded-banner-bytes")
        calls = []
        orig_dl, orig_up = judge._download_image, judge._upload_hero_image
        judge._download_image = fake_download
        judge._upload_hero_image = lambda p: calls.append(p) or "img_v3_from_url"
        try:
            self.assertEqual(judge.resolve_banner_key(self.ws, url), "img_v3_from_url")
            self.assertEqual(dl, [url])
            self.assertEqual(judge.resolve_banner_key(self.ws, url), "img_v3_from_url")
            self.assertEqual(len(dl), 1, "同 URL 第二次必须命中 URL 缓存，不再下载")
            cache = core.jload(os.path.join(self.ws, "state", "hero_images.json"), {})
            self.assertEqual(cache["url:" + hashlib.sha1(url.encode()).hexdigest()],
                             "img_v3_from_url")
        finally:
            judge._download_image, judge._upload_hero_image = orig_dl, orig_up

    def test_http_url_download_failure_falls_back(self):
        """图片链接下载失败 → 明确回退默认图，不抛异常不阻断产卡。"""
        def boom(u, dst):
            raise RuntimeError("network down")
        orig_dl, orig_up = judge._download_image, judge._upload_hero_image
        judge._download_image = boom
        judge._upload_hero_image = lambda p: "never"
        try:
            self.assertIsNone(judge.resolve_banner_key(self.ws, "https://x.y/z.png"))
        finally:
            judge._download_image, judge._upload_hero_image = orig_dl, orig_up

    def test_mixed_text_embedded_key_fallback(self):
        """非路径非链接的混杂文本 → 尽力提取内嵌 key 兜底。"""
        self.assertEqual(judge.resolve_banner_key(self.ws, "头图用 img_v3_02ba_abcdef-12 这张"),
                         "img_v3_02ba_abcdef-12")

    def test_briefing_card_uses_custom_banner(self):
        """briefing_card 接收 banner_key 时覆盖默认头图。"""
        recs = [{"type": "行业趋势", "title": "测试动态", "notify": True,
                 "sources": [{"name": "s", "url": "https://x"}]}]
        card = judge.briefing_card(recs, [], banner_key="img_v3_custom")
        first_img = card["body"]["elements"][0]
        self.assertEqual(first_img["img_key"], "img_v3_custom")
        # 不传时用默认
        card2 = judge.briefing_card(recs, [])
        self.assertEqual(card2["body"]["elements"][0]["img_key"],
                         judge.BRIEFING_IMGS["banner"])


class TestNewsLimits(unittest.TestCase):
    """notify=true 的新闻不得被 limit 截断（2026-10-27 实测漏发，陷阱 45）。

    实测：02:40 轮 86 条曾通知的非活动情报，卡片只展示 3 条 → 83 条因锚点推进
    `logged_at` 早于新锚点而永久漏发。活动段当初因同一原因把 limit 3→8，
    新闻段漏了同样的修复。
    """

    def _shown_titles(self, els):
        txt = json.dumps(els, ensure_ascii=False)
        return txt

    def test_all_notify_records_shown_beyond_limit(self):
        """5 条 notify=true（limit=3）→ 必须全部展示，不得截断。"""
        recs = [{"type": "融资动态", "title": "情报%d" % i, "summary": "s",
                 "url": "https://e/%d" % i, "notify": True, "significance": "normal"}
                for i in range(1, 6)]
        blob = self._shown_titles(judge._news_elements(recs))
        for i in range(1, 6):
            self.assertIn("情报%d" % i, blob, "notify=true 的第 %d 条不许被截断" % i)

    def test_archive_records_still_limited(self):
        """notify=false 属归档语义，仍按 limit 少量补充，不无限膨胀。"""
        recs = ([{"type": "a", "title": "通知1", "notify": True, "significance": "normal"}]
                + [{"type": "a", "title": "归档%d" % i, "notify": False,
                    "significance": "low"} for i in range(1, 11)])
        blob = self._shown_titles(judge._news_elements(recs))
        shown_archive = sum(1 for i in range(1, 11) if "归档%d" % i in blob)
        self.assertLessEqual(shown_archive, 3, "归档记录应按 limit 收敛")
        self.assertIn("通知1", blob)

    def test_rank_elements_exempt_notify_from_limit(self):
        """榜单段同纪律：notify=true 不得被 limit=4 截断（陷阱 46）。"""
        recs = [{"type": "榜单新晋", "title": "榜单%d" % i, "notify": True,
                 "significance": "normal"} for i in range(1, 7)]
        blob = json.dumps(judge._rank_elements(recs), ensure_ascii=False)
        for i in range(1, 7):
            self.assertIn("榜单%d" % i, blob, "榜单 notify=true 的第 %d 条不许截断" % i)

    def test_rank_elements_archive_still_limited(self):
        """榜单归档记录仍按 limit 收敛，不无限膨胀。"""
        recs = [{"type": "榜单新晋", "title": "归档%d" % i, "notify": False,
                 "significance": "low"} for i in range(1, 9)]
        blob = json.dumps(judge._rank_elements(recs), ensure_ascii=False)
        shown = sum(1 for i in range(1, 9) if "归档%d" % i in blob)
        self.assertLessEqual(shown, 4, "归档榜单应按 limit=4 收敛")

    def test_notify_sorted_before_archive(self):
        """notify=true 排在前（high 优先），归档在后。"""
        recs = [{"type": "a", "title": "低价值归档", "notify": False, "significance": "high"},
                {"type": "a", "title": "要通知的", "notify": True, "significance": "normal"}]
        blob = self._shown_titles(judge._news_elements(recs))
        self.assertLess(blob.index("要通知的"), blob.index("低价值归档"))


class TestHighlightPrefix(unittest.TestCase):
    """活动 highlight 不得渲染成「看点：看点：…」（2026-10-27 实测 18 条受污染）。

    根因：契约示例把「看点：」写进了 highlight 值里，AI 照填；渲染层又自动加
    `**看点：**` 标签 → 双重前缀。契约示例已改，渲染层同步兜底剥离既有前缀。
    """

    def _render(self, highlight):
        recs = [{"type": "活动情报", "title": "测试活动", "notify": True,
                 "significance": "normal",
                 "activity": {"name": "某种活动", "time": "2026-10-30 14:00",
                              "location": "广州", "highlight": highlight}}]
        card = judge.briefing_card(recs, [])
        def walk(o):
            if isinstance(o, dict):
                if o.get("tag") == "markdown":
                    yield o["content"]
                else:
                    for v in o.values():
                        yield from walk(v)
            elif isinstance(o, list):
                for v in o:
                    yield from walk(v)
        return "\n".join(walk(card))

    def test_prefix_already_in_data_stripped(self):
        """数据自带「看点：」→ 剥离，不出现双重前缀。"""
        txt = self._render("看点：Meta 官方出海体验")
        self.assertNotIn("看点：看点", txt)
        self.assertIn("**看点：** Meta 官方出海体验", txt)

    def test_prefix_with_space_and_fullwidth_colon(self):
        """全角冒号/带空格的变体也要剥离。"""
        for variant in ("看点: 内容A", "看点 ：内容B", " 看点： 内容C"):
            txt = self._render(variant)
            self.assertNotIn("看点：看点", txt)

    def test_normal_highlight_untouched(self):
        """正常数据不被误伤。"""
        txt = self._render("面向跨境卖家的实操分享")
        self.assertIn("**看点：** 面向跨境卖家的实操分享", txt)


class TestBriefingRankSection(unittest.TestCase):
    """内参卡「榜单监控」段（需求文档榜单监控板块）：type 含「榜单」的情报独立成段。"""

    def test_rank_record_gets_dedicated_section(self):
        recs = [{"type": "榜单新晋", "title": "NovaChat 登顶 AI产品榜出海增速榜",
                 "summary": "本月新晋黑马，背后公司符合画像", "url": "https://aicpb.com/zh/product/x",
                 "sources": [{"name": "AI产品榜", "url": "https://aicpb.com/zh"}]},
                {"type": "行业趋势", "title": "普通新闻", "sources": []}]
        els = judge._rank_elements(recs)
        text = "".join(e.get("content", "") for e in els if e.get("tag") == "markdown")
        self.assertIn("NovaChat", text, "榜单新晋情报必须进榜单监控段")
        self.assertNotIn("普通新闻", text, "非榜单情报不得混入榜单段")

    def test_briefing_has_rank_heading_only_with_data(self):
        rec_rank = [{"type": "榜单新晋", "title": "X 新上榜", "summary": "s",
                     "url": "https://a", "sources": []}]
        card = judge.briefing_card(rec_rank, [])
        heads = [e["content"] for e in card["body"]["elements"]
                 if e.get("tag") == "markdown" and "榜单监控" in e.get("content", "")]
        self.assertTrue(heads, "有榜单情报时内参卡必须有「榜单监控」段标题")
        # 无榜单数据：不出现该段（占位空段不许出现）
        card2 = judge.briefing_card([{"type": "行业趋势", "title": "t", "summary": "s",
                                      "url": "https://b", "sources": []}], [])
        heads2 = [e["content"] for e in card2["body"]["elements"]
                  if e.get("tag") == "markdown" and "榜单监控" in e.get("content", "")]
        self.assertFalse(heads2, "无榜单数据时不得渲染空榜单段")

    def test_rank_section_has_explainer(self):
        """段标题下必须有释义行：读者不知道「榜单监控」盯的是海外软件榜单新晋黑马。"""
        recs = [{"type": "榜单新晋", "title": "X 新上榜", "summary": "s",
                 "url": "https://a", "sources": []}]
        card = judge.briefing_card(recs, [])
        els = card["body"]["elements"]
        text = "".join(e.get("content", "") for e in els if e.get("tag") == "markdown")
        self.assertIn("海外软件榜单", text, "段标题应点明监控对象是海外软件榜单")
        self.assertIn("排除长久霸榜", text, "释义必须说明「排除霸榜、只看新晋」语义")
        self.assertIn("画像", text, "释义必须说明要调研背后公司是否符合画像")


class TestEnrichDetail(unittest.TestCase):
    """活动行详情页富化：RSS feed 只有标题+链接，时间/地点须从详情页 ativityJson 补全。"""

    FIXTURE = '''var ativityJson = {"Id":7877592160200,"Title":"AI文娱（短剧）产业生态大会",
        "Start":"\\\\/Date(1793838600000)\\\\/","End":"\\\\/Date(1793959200000)\\\\/",
        "StartShort":"11/05 08:30","EndShort":"11/06 18:00","City":"杭州",
        "Address":"白马湖国际会展中心·B馆3楼","Tag":"文博会,文化产业","MaxInstance":2000,
        "InstanceNumber":410,"Organizers":[{"Id":1,"Name":"短剧自习室"}]};'''

    def test_enrich_fills_time_location(self):
        entry = {"title": "AI文娱（短剧）产业生态大会", "link": "https://www.huodongxing.com/event/1",
                 "published": None, "summary": "", "text": ""}
        out = collect.enrich_huodongxing(entry, self.FIXTURE)
        self.assertEqual(out["published"], "2026-11-05 08:30", "开始时间须解析为北京时间 ISO")
        self.assertIn("时间：2026-11-05 08:30 ~ 11-06 18:00", out["summary"])
        self.assertIn("地点：杭州 · 白马湖国际会展中心·B馆3楼", out["summary"])
        self.assertIn("主办方：短剧自习室", out["summary"])
        self.assertIn("报名规模：410/2000", out["summary"], "报名规模是热度信号")

    def test_enrich_failure_keeps_entry(self):
        entry = {"title": "t", "link": "x", "published": None, "summary": "原摘要", "text": ""}
        for bad in ["<html>无 JSON</html>", 'var ativityJson = {broken;', 'var ativityJson = {"Start":"xx"};']:
            out = collect.enrich_huodongxing(dict(entry), bad)
            self.assertEqual(out["summary"], "原摘要", "解析失败必须原样返回，富化是增益不是门槛")

    def test_enrich_detail_dispatch_and_budget(self):
        entry = {"title": "t", "link": "https://example.com/x", "published": None, "summary": "", "text": ""}
        # 非活动行域名不分发，不消耗配额
        budget = [3]
        out = collect.enrich_detail({"config": {"detail_enrich": True}}, entry, {}, budget)
        self.assertEqual(budget[0], 3, "无 enricher 的域名不消耗抓取配额")
        self.assertEqual(out["summary"], "")


class TestLeadsLatestCarryOver(unittest.TestCase):
    """leads-latest 防覆盖：未入池线索跨轮保留，不因下一轮判断静默丢失。"""

    def test_carry_over_unpooled_leads(self):
        with tempfile.TemporaryDirectory() as ws:
            old = {"run_at": "t0", "leads": [
                {"entity_name": "A未入池", "status": "new", "dedupe_key": "k-a"},
                {"entity_name": "B已入池", "status": "new", "record_id": "recX", "dedupe_key": "k-b"},
                {"entity_name": "C本轮重现", "status": "new", "dedupe_key": "k-c"},
            ]}
            core.jdump(os.path.join(ws, "out", "leads-latest.json") if os.path.isdir(os.path.join(ws, "out")) else _mk_out(ws), old)
            fresh = [{"entity_name": "C本轮重现", "status": "new", "dedupe_key": "k-c"}]
            carried = judge.save_leads_latest(ws, fresh, "t1", 1)
            out = core.jload(os.path.join(ws, "out", "leads-latest.json"), None)
            names = [l["entity_name"] for l in out["leads"]]
            self.assertEqual(names, ["C本轮重现", "A未入池"],
                             "仅未入池且本轮未重现的线索被并入；已入池的不重复")
            self.assertEqual(out["summary"]["carried_unpooled"], 1)
            self.assertEqual([l["entity_name"] for l in carried], ["A未入池"])

    def test_no_prev_file(self):
        with tempfile.TemporaryDirectory() as ws:
            _mk_out(ws)
            judge.save_leads_latest(ws, [], "t1", 0)
            out = core.jload(os.path.join(ws, "out", "leads-latest.json"), None)
            self.assertEqual(out["leads"], [])


class TestEnrichmentGate(unittest.TestCase):
    """调研完整度门槛：14 必研字段缺一不可（兜底值算完成），入池硬前置。"""

    @staticmethod
    def _complete_fields():
        return {k: "v-%s" % k for k in bitable.REQUIRED_ENRICH_FIELDS}

    def test_gaps_complete_and_fallback_ok(self):
        self.assertEqual(bitable.enrichment_gaps(self._complete_fields()), [])
        # 兜底值本身就是调研结论，算完成
        f = self._complete_fields()
        f.update({"funding": "未公开", "contact": "未查询到有效信息", "tech_stack": "未查明"})
        self.assertEqual(bitable.enrichment_gaps(f), [])
        # 兼容完整调研记录形态（含 fields 键）
        self.assertEqual(bitable.enrichment_gaps({"fields": f}), [])

    def test_gaps_detect_missing_and_empty(self):
        f = {k: v for k, v in self._complete_fields().items() if k not in ("funding", "website")}
        gaps = bitable.enrichment_gaps(f)
        self.assertEqual(len(gaps), 2)
        self.assertTrue(any("融资动态" in g for g in gaps))
        f["funding"] = ""  # 空串与缺失同级
        self.assertEqual(len(bitable.enrichment_gaps(f)), 2)
        # 完全没调研 = 全缺
        self.assertEqual(len(bitable.enrichment_gaps(None)),
                         len(bitable.REQUIRED_ENRICH_FIELDS))

    def test_audit_enrichments_counts_and_pass(self):
        with tempfile.TemporaryDirectory() as ws:
            os.makedirs(os.path.join(ws, "log"))
            complete = self._complete_fields()
            partial = {k: v for k, v in complete.items() if k != "financials"}
            leads = [
                {"lead_id": "l1", "entity_id": "e1", "entity_name": "甲公司"},
                {"lead_id": "l2", "entity_id": "e2", "entity_name": "乙公司"},
                {"lead_id": "l3", "entity_id": "e3", "entity_name": "丙公司"},
                {"lead_id": "l4", "entity_id": "e4", "entity_name": "丁公司",
                 "bitable_status": "filtered"},  # 被规则淘汰的不参与审计
            ]
            enrichments = [
                {"entity_id": "e1", "entity_name": "甲公司", "fields": complete},
                {"entity_id": "e2", "entity_name": "乙公司", "fields": partial},
                {"entity_id": "orphan", "entity_name": "孤儿", "fields": complete},  # 孤儿不记失败
            ]
            for name, rows in (("leads.jsonl", leads), ("enrichments.jsonl", enrichments)):
                with open(os.path.join(ws, "log", name), "w", encoding="utf-8") as fh:
                    for r in rows:
                        fh.write(json.dumps(r, ensure_ascii=False) + "\n")
            # e2 缺 financials、e3 未调研 → 2 家不达标
            self.assertEqual(bitable.audit_enrichments(ws), 2)
            # 补齐后归零（append-only 后写覆盖先写）
            with open(os.path.join(ws, "log", "enrichments.jsonl"), "a", encoding="utf-8") as fh:
                fh.write(json.dumps({"entity_id": "e2", "fields": complete}, ensure_ascii=False) + "\n")
                fh.write(json.dumps({"entity_id": "e3", "fields": complete}, ensure_ascii=False) + "\n")
            self.assertEqual(bitable.audit_enrichments(ws), 0)

    def test_apply_fact_origin_never_downgrades(self):
        """origin 只升不降：档案已有 CN 时，重跑同一 judged.json 的旧申报值不得回灌。"""
        with tempfile.TemporaryDirectory() as ws:
            entities = {"industries": [], "entities": {}}
            fact = {"entity_name": "某出海公司", "industries": ["应用"], "origin": "domestic",
                    "event_type": "other", "event": {"kind": "product_launch", "detail": "发布新品",
                                                     "source_url": "https://x.com/a"},
                    "change_ids": ["c1"], "confidence": "high"}
            facts_mod.apply_fact(ws, fact, entities, "2026-10-26T18:00:00+08:00")
            # 人工/AI 修正为 CN（升格）
            path = os.path.join(ws, "facts", "unknown__某出海公司.json")
            rec = core.jload(path, None)
            rec["origin"] = "CN"
            core.jdump(path, rec)
            # 重跑同一申报（origin 仍为 domestic）→ 不得回灌
            facts_mod.apply_fact(ws, fact, entities, "2026-10-26T18:05:00+08:00")
            self.assertEqual(core.jload(path, None)["origin"], "CN")
            # going_global 一旦为真也不被抹掉
            rec = core.jload(path, None)
            rec["going_global"] = True
            core.jdump(path, rec)
            facts_mod.apply_fact(ws, fact, entities, "2026-10-26T18:06:00+08:00")
            self.assertTrue(core.jload(path, None)["going_global"])
            # 空位仍允许申报写入
            fact2 = {"entity_name": "另一公司", "industries": ["应用"], "origin": "CN",
                     "event_type": "other", "event": {"kind": "milestone", "detail": "d",
                                                      "source_url": "https://x.com/b"},
                     "change_ids": ["c2"], "confidence": "high"}
            facts_mod.apply_fact(ws, fact2, entities, "2026-10-26T18:07:00+08:00")
            self.assertEqual(core.jload(os.path.join(ws, "facts", "unknown__另一公司.json"), None)["origin"], "CN")

    def test_apply_fact_events_idempotent_and_upgrade(self):
        """事件幂等：重跑同 judged 不重复落盘；funding 同 URL 只补空字段不新增。"""
        with tempfile.TemporaryDirectory() as ws:
            entities = {"industries": [], "entities": {}}
            oe = {"entity_name": "甲公司", "industries": ["应用"],
                  "event_type": "other",
                  "event": {"kind": "product_launch", "detail": "发布新品 X",
                            "source_url": "https://x.com/news/1"},
                  "change_ids": ["c1"], "confidence": "high"}
            fe = {"entity_name": "乙公司", "industries": ["AI硬件"],
                  "event_type": "funding",
                  "event": {"kind": "funding", "source_url": "https://x.com/news/2"},
                  "change_ids": ["c2"], "confidence": "high"}
            for _ in range(5):
                facts_mod.apply_fact(ws, oe, entities, "2026-10-26T18:00:00+08:00")
                facts_mod.apply_fact(ws, fe, entities, "2026-10-26T18:00:00+08:00")
            pa = os.path.join(ws, "facts", "unknown__甲公司.json")
            pb = os.path.join(ws, "facts", "unknown__乙公司.json")
            self.assertEqual(len(core.jload(pa, None)["other_events"]), 1)
            fe_list = core.jload(pb, None)["funding_events"]
            self.assertEqual(len(fe_list), 1)
            self.assertIsNone(fe_list[0]["date"])  # 首次无 date
            # 申报补充 date/amount → 升级合并同一条，不新增
            fe["event"].update({"date": "2026-10-26", "amount_text": "2亿元"})
            facts_mod.apply_fact(ws, fe, entities, "2026-10-26T18:10:00+08:00")
            fe_list = core.jload(pb, None)["funding_events"]
            self.assertEqual(len(fe_list), 1)
            self.assertEqual(fe_list[0]["date"], "2026-10-26")
            self.assertEqual(fe_list[0]["amount_cny"], 2e8)
            # 存量无 _dedupe 的 other_events 也能被现算键匹配（兼容旧数据）
            core.jdump(pa, {**core.jload(pa, None),
                            "other_events": [{k: v for k, v in e.items() if k != "_dedupe"}
                                             for e in core.jload(pa, None)["other_events"]]})
            facts_mod.apply_fact(ws, oe, entities, "2026-10-26T18:11:00+08:00")
            self.assertEqual(len(core.jload(pa, None)["other_events"]), 1)


class TestBatchCreateResponse(unittest.TestCase):
    """+record-batch-create 响应解析：真实结构 data.record_id_list，兼容旧 records[].record_id。"""

    def test_record_id_list_structure(self):
        self.assertEqual(bitable._extract_new_rid(
            {"ok": True, "data": {"record_id_list": ["recA"], "rev": 1}}), "recA")

    def test_legacy_records_structure(self):
        self.assertEqual(bitable._extract_new_rid(
            {"ok": True, "data": {"records": [{"record_id": "recB"}]}}), "recB")

    def test_missing_returns_none(self):
        self.assertIsNone(bitable._extract_new_rid({"ok": True, "data": {}}))
        self.assertIsNone(bitable._extract_new_rid({"ok": False}))


class TestExistsLeadSemantics(unittest.TestCase):
    """exists 线索语义：已在线索池的公司不得再以「新潜在线索」面孔推送或群@轰炸。"""

    @staticmethod
    def _lead(**kw):
        base = {"entity_name": "月之暗面", "industries": ["应用"], "priority": "P0",
                "score": 90, "matched_group_names": ["p0-funding-recent"], "evidence": [],
                "sources": [{"name": "36氪", "url": "https://36kr/1"}],
                "bitable_status": "exists", "bitable_record_id": "rec1",
                "logged_at": "2026-10-26T20:00:00+08:00"}
        base.update(kw)
        return base

    def test_exists_labeled_and_not_red(self):
        """exists 线索在卡片上必须标注「已存在」，且不用 P0 红块。"""
        blocks = judge.lead_blocks([self._lead()], view_url="https://x/base?table=t&view=v")
        text = json.dumps(blocks, ensure_ascii=False)
        self.assertIn("已存在·已在线索池", text, "exists 线索必须显式标注")
        self.assertIn("已在线索池·查看既有记录", text, "按钮文案必须点明是既有记录")
        for b in blocks:
            if isinstance(b, dict):
                self.assertNotEqual(b.get("background_style"), "red-50",
                                    "exists 线索不得用 P0 红块渲染")
        # 内参卡潜在客户段同样标注
        els = judge._lead_elements([self._lead()], view_url=None)
        md = json.dumps(els, ensure_ascii=False)
        self.assertIn("此前已登记【猎隼线索池】，本次未重复写入", md)
        self.assertIn("已在线索池·点此查看既有记录", md)

    def test_card_split_fresh_vs_exists(self):
        """群卡素材必须剔除 exists：lead_card_split 分流正确。"""
        fresh_lead = self._lead(entity_name="新公司", bitable_status=None,
                                bitable_record_id=None)
        fresh, exists = judge.lead_card_split([self._lead(), fresh_lead])
        self.assertEqual([l["entity_name"] for l in fresh], ["新公司"])
        self.assertEqual([l["entity_name"] for l in exists], ["月之暗面"])
        # 全 exists 时 fresh 为空 → 群卡应不产（cmd_lead_card 以 p0_leads 空判定）
        only_exists = judge.lead_card_split([self._lead()])
        self.assertEqual(only_exists[0], [])


class TestCarryOverHydration(unittest.TestCase):
    """结转必须回查 leads.jsonl 最新状态：期间已推送的线索不得再当待办重复通知。"""

    def test_pushed_between_rounds_not_carried(self):
        import tempfile, os
        with tempfile.TemporaryDirectory() as ws:
            os.makedirs(os.path.join(ws, "log"))
            os.makedirs(os.path.join(ws, "out"))
            # 上一轮视图：两条待办（无 record_id）
            old_a = {"lead_id": "a1", "entity_name": "甲", "status": "new",
                     "dedupe_key": "k1", "logged_at": "2026-10-26T10:00:00+08:00"}
            old_b = {"lead_id": "b2", "entity_name": "乙", "status": "new",
                     "dedupe_key": "k2", "logged_at": "2026-10-26T10:01:00+08:00"}
            core.jdump(os.path.join(ws, "out", "leads-latest.json"),
                       {"run_at": "2026-10-26T10:05:00+08:00", "leads": [old_a, old_b],
                        "summary": {}})
            # 其间 a1 已推送入池（leads.jsonl 状态已更新），b2 仍待写
            with open(os.path.join(ws, "log", "leads.jsonl"), "w", encoding="utf-8") as fh:
                fh.write(json.dumps({**old_a, "bitable_status": "pushed",
                                     "bitable_record_id": "recX"}, ensure_ascii=False) + "\n")
                fh.write(json.dumps(old_b, ensure_ascii=False) + "\n")
            new_lead = {"lead_id": "c3", "entity_name": "丙", "status": "new",
                        "dedupe_key": "k3"}
            carried = judge.save_leads_latest(ws, [new_lead],
                                              "2026-10-26T22:00:00+08:00", 1)
            self.assertEqual([l["lead_id"] for l in carried], ["b2"],
                             "已推送的 a1 不得结转（会重复通知），待写的 b2 保留")
            view = core.jload(os.path.join(ws, "out", "leads-latest.json"), None)
            self.assertEqual(view["summary"]["carried_unpooled"], 1)


class TestDedupeAndRetry(unittest.TestCase):
    """写入层去重与重试：别名候选防异写重复建行；failed 线索必须可重试。"""

    def test_dedupe_candidates_aliases(self):
        self.assertEqual(
            bitable._dedupe_candidates({"company": "月之暗面",
                                        "aliases": ["Kimi", "月之暗面", ""]}),
            ["月之暗面", "Kimi"], "主名优先、去重、剔除空串")
        self.assertEqual(bitable._dedupe_candidates({"company": "X"}), ["X"])
        self.assertLessEqual(
            len(bitable._dedupe_candidates({"company": "A", "aliases": list("BCDEF")})), 4,
            "候选上限 4，控制检索次数")

    def test_retryable_statuses(self):
        """终态（pushed/exists/filtered）不重写；failed/无状态/pending 都可重试。"""
        self.assertFalse(bitable.retryable({"bitable_status": "pushed"}))
        self.assertFalse(bitable.retryable({"bitable_status": "exists"}))
        self.assertFalse(bitable.retryable({"bitable_status": "filtered"}))
        self.assertTrue(bitable.retryable({"bitable_status": "failed"}),
                        "写入失败必须可重试，不得静默淘汰")
        self.assertTrue(bitable.retryable({}))
        self.assertTrue(bitable.retryable({"bitable_status": "pending"}))


class TestAbsorbDiscards(unittest.TestCase):
    """judged.json 顶层 discarded 键自动并入 intel（构建脚本忘合并的实战坑，代码层兜底）。"""

    def test_absorbs_top_level_discarded(self):
        judged = {"intel": [{"change_id": "a", "title": "x"}],
                  "discarded": [{"change_id": "b", "discard": True, "discard_reason": "r"}]}
        intel, absorbed = judge.absorb_discards(judged)
        self.assertTrue(absorbed)
        self.assertEqual([r["change_id"] for r in intel], ["a", "b"])

    def test_no_discarded_key_passthrough(self):
        judged = {"intel": [{"change_id": "a"}]}
        intel, absorbed = judge.absorb_discards(judged)
        self.assertFalse(absorbed)
        self.assertEqual(len(intel), 1)
        # 顶层无 intel 且无 discarded → 空列表，不炸
        intel2, absorbed2 = judge.absorb_discards({})
        self.assertFalse(absorbed2)
        self.assertEqual(intel2, [])

    def test_ignores_non_list_discarded(self):
        intel, absorbed = judge.absorb_discards({"discarded": "oops", "intel": []})
        self.assertFalse(absorbed)
        self.assertEqual(intel, [])


class TestMakeCid(unittest.TestCase):
    """内容寻址 change_id：跨轮/跨洁净重置稳定（防重发幂等的根基）。"""

    def test_stable_across_runs(self):
        # 同一内容两次生成（模拟不同轮次/洁净重置后重扫）必须同 id
        self.assertEqual(pipeline.make_cid("qbitai-feed", "added", "https://x/a"),
                         pipeline.make_cid("qbitai-feed", "added", "https://x/a"))

    def test_differs_by_kind_and_key(self):
        base = pipeline.make_cid("s", "added", "k1")
        self.assertNotEqual(base, pipeline.make_cid("s", "removed", "k1"))
        self.assertNotEqual(base, pipeline.make_cid("s", "added", "k2"))
        self.assertNotEqual(base, pipeline.make_cid("s2", "added", "k1"))

    def test_length_12_and_no_timestamp(self):
        cid = pipeline.make_cid("s", "added", "k")
        self.assertEqual(len(cid), 12)
        # 公式只含三个因子——重建结果必须与再次生成一致（无隐藏时刻成分）
        import core as _core
        self.assertEqual(cid, _core.sha1("s|added|k")[:12])


class TestDemoteAlreadySent(unittest.TestCase):
    """洁净重置防重发：--already-sent 命中的已发情报必须强制 notify=false（代码层兜底）。"""

    def test_demotes_sent_intel(self):
        intel = [{"change_id": "a", "notify": True},
                 {"change_id": "b", "notify": True},
                 {"change_id": "c", "discard": True, "notify": True}]
        out, n = judge.demote_already_sent(intel, {"a"})
        self.assertEqual(n, 1)
        self.assertFalse(out[0]["notify"])
        self.assertTrue(out[1]["notify"])   # 未命中保持通知
        self.assertTrue(out[2]["notify"])   # 弃权条目不动（也不该有 notify 语义）

    def test_empty_sent_ids_noop(self):
        intel = [{"change_id": "a", "notify": True}]
        out, n = judge.demote_already_sent(intel, set())
        self.assertEqual(n, 0)
        self.assertTrue(out[0]["notify"])

    def test_merged_change_ids_count_as_sent(self):
        """被合并条目也属已发：合并报道换源重现时不得重复通知（merge 语义闭环）。"""
        # 模拟旧登记：主条记录带 merged_change_ids
        old_log = [{"change_id": "main", "notify": True,
                    "merged_change_ids": ["m1", "m2"]}]
        import tempfile
        with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False) as f:
            for r in old_log:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
            path = f.name
        # 重建 sent_ids 的逻辑与 cmd_input 一致：change_id + merged_change_ids
        sent_ids = set()
        for r in core.read_jsonl(path):
            if r.get("discard"):
                continue
            sent_ids.add(r.get("change_id"))
            for mid in r.get("merged_change_ids") or []:
                sent_ids.add(mid)
        os.unlink(path)
        intel = [{"change_id": "m1", "notify": True},   # 换源重现的被合并条目
                 {"change_id": "new", "notify": True}]  # 真新增不受影响
        out, n = judge.demote_already_sent(intel, sent_ids)
        self.assertEqual(n, 1)
        self.assertFalse(out[0]["notify"], "被合并条目换源重现必须被压制")
        self.assertTrue(out[1]["notify"])

    def test_sent_before_spares_unsent_recent_records(self):
        """--sent-before：锚点之后落盘的 notify=true 记录从未发出，不算已发（防漏发）。

        场景：上轮判定落盘 notify=true 但未 digest/未推进锚点即洁净重置——
        这些记录若按旧登记全量压制，本轮内参会漏掉从未发出的内容。
        """
        import tempfile
        old_log = [
            {"change_id": "old-sent", "notify": True, "logged_at": "2026-10-27T00:50:00+08:00"},
            {"change_id": "pending-send", "notify": True, "logged_at": "2026-10-27T01:33:00+08:00"},
            {"change_id": "archived", "notify": False, "logged_at": "2026-10-27T01:40:00+08:00"},
        ]
        with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False) as f:
            for r in old_log:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
            path = f.name
        cut = core.parse_iso("2026-10-27T00:54:19+08:00")
        sent_ids = set()
        for r in core.read_jsonl(path):
            if r.get("discard"):
                continue
            if r.get("notify") and (core.parse_iso(r.get("logged_at")) or 0) >= cut:
                continue  # 锚点后落盘的 notify=true：从未发出
            sent_ids.add(r.get("change_id"))
        os.unlink(path)
        intel = [{"change_id": "old-sent", "notify": True},
                 {"change_id": "pending-send", "notify": True},
                 {"change_id": "archived", "notify": True}]
        out, n = judge.demote_already_sent(intel, sent_ids)
        self.assertEqual(n, 2)
        self.assertFalse(out[0]["notify"], "锚点前已随内参发出的必须压制")
        self.assertTrue(out[1]["notify"], "锚点后落盘从未发出的不得压制（宁重勿漏）")
        self.assertFalse(out[2]["notify"], "归档语义（notify=false）无条件算已发")


class TestStripWriteback(unittest.TestCase):
    """洁净重置复用旧登记：落盘回写字段必须自动剥离，不再被白名单拦截。"""

    def test_strips_known_writeback_fields(self):
        intel = [{"change_id": "a", "title": "T", "notify": True,
                  "logged_at": "2026-10-26T23:54:37+08:00",
                  "grounding": {"ok": 1},
                  "grounding_notes": {"300": "换算"}},
                 {"change_id": "b", "discard": True, "discard_reason": "x"}]
        stripped = judge.strip_writeback(intel)
        self.assertEqual(stripped, {"logged_at", "grounding", "grounding_notes"})
        self.assertEqual(set(intel[0]), {"change_id", "title", "notify"})
        # 二次调用（幂等）：已剥离后无字段可剥
        self.assertEqual(judge.strip_writeback(intel), set())

    def test_unknown_fields_survive_for_check_keys(self):
        """真正未知的字段（拼写错误）不能被静默剥离——留给 check_keys 点名拦截。"""
        intel = [{"change_id": "a", "notif": True}]  # notify 拼错
        stripped = judge.strip_writeback(intel)
        self.assertEqual(stripped, set())
        self.assertIn("notif", intel[0])

    def test_discard_log_structure_normalized(self):
        """复用 discarded.jsonl 的 {change_id, reason} 结构自动归一为 judged 契约。"""
        intel = [{"change_id": "d1", "reason": "行情噪声", "logged_at": "2026-10-26T01:00:00+08:00"}]
        judge.strip_writeback(intel)
        self.assertTrue(intel[0]["discard"])
        self.assertEqual(intel[0]["discard_reason"], "行情噪声")
        self.assertNotIn("reason", intel[0])
        self.assertNotIn("logged_at", intel[0])


class TestNotifyContentGuard(unittest.TestCase):
    """通知内容护栏：notify=true 缺 title/url 直接产坏卡，必须在落盘前拦截（退出码 2）。

    放在 sent-guard 之后执行：复用旧精简登记的墓碑记录（无 title）已被压成
    notify=false，不受影响——护栏只拦 AI 判定真要通知却漏带正文的场景。
    """

    def _run_input(self, intel, tmp):
        latest = {"run_at": "2026-10-27T01:00:00+08:00", "changes": [
            {"change_id": r["change_id"], "title": "原文标题", "summary": "", "url": "https://x/1"}
            for r in intel]}
        core.jdump(os.path.join(tmp, "out", "latest.json"), latest)
        core.jdump(os.path.join(tmp, "judged.json"), {"intel": intel, "facts": []})
        return judge.cmd_input(tmp, os.path.join(tmp, "judged.json"))

    def test_notify_true_missing_title_rejected(self):
        with tempfile.TemporaryDirectory(prefix="mi-guard-") as tmp:
            os.makedirs(os.path.join(tmp, "out"))
            intel = [{"change_id": "a", "type": "行业动态", "significance": "normal",
                      "title": "完整记录", "url": "https://x/1", "sources": [],
                      "notify": True, "notify_reason": "r"},
                     {"change_id": "b", "type": "行业动态", "significance": "normal",
                      "notify": True, "notify_reason": "r"}]  # 缺 title/url
            with self.assertRaises(SystemExit) as cm:
                self._run_input(intel, tmp)
            self.assertEqual(cm.exception.code, 2)

    def test_demoted_skeleton_passes(self):
        """被 sent-guard 压制的无 title 墓碑记录不得被护栏误伤（复用流不被破坏）。"""
        with tempfile.TemporaryDirectory(prefix="mi-guard-") as tmp:
            os.makedirs(os.path.join(tmp, "out"))
            os.makedirs(os.path.join(tmp, "log"))
            intel = [{"change_id": "a", "significance": "normal", "notify": True}]  # 无 title
            sent_path = os.path.join(tmp, "sent.jsonl")
            with open(sent_path, "w") as f:
                f.write(json.dumps({"change_id": "a", "title": "旧文", "notify": True}) + "\n")
            latest = {"run_at": "2026-10-27T01:00:00+08:00", "changes": [
                {"change_id": "a", "title": "原文标题", "summary": "", "url": "https://x/1"}]}
            core.jdump(os.path.join(tmp, "out", "latest.json"), latest)
            core.jdump(os.path.join(tmp, "judged.json"), {"intel": intel, "facts": []})
            rc = judge.cmd_input(tmp, os.path.join(tmp, "judged.json"), already_sent=sent_path)
            self.assertEqual(rc, 0, "已压制的墓碑记录（notify=false）必须放行")

    def test_bare_skeleton_rejected(self):
        """窗口内的裸 {change_id} 骨架必须在落盘前拦截——
        组装脚本对旧 discarded.jsonl 的 {change_id, reason} 形态误剥的实测坑。"""
        with tempfile.TemporaryDirectory(prefix="mi-guard-") as tmp:
            os.makedirs(os.path.join(tmp, "out"))
            os.makedirs(os.path.join(tmp, "log"))
            intel = [{"change_id": "a"},  # 窗口内裸骨架
                     {"change_id": "b", "discard": True, "discard_reason": "行情噪声"}]
            latest = {"run_at": "2026-10-27T01:00:00+08:00", "changes": [
                {"change_id": "a", "title": "t", "url": "u"},
                {"change_id": "b", "title": "t2", "url": "u2"}]}
            core.jdump(os.path.join(tmp, "out", "latest.json"), latest)
            core.jdump(os.path.join(tmp, "judged.json"), {"intel": intel, "facts": []})
            with self.assertRaises(SystemExit) as cm:
                judge.cmd_input(tmp, os.path.join(tmp, "judged.json"))
            self.assertEqual(cm.exception.code, 2)

    def test_offwindow_legacy_skeleton_passes(self):
        """窗口外的历史精简留痕（v0.3.2 前 unknown+false 形态，本就无 title）放行。"""
        with tempfile.TemporaryDirectory(prefix="mi-guard-") as tmp:
            os.makedirs(os.path.join(tmp, "out"))
            os.makedirs(os.path.join(tmp, "log"))
            intel = [{"change_id": "legacy", "notify": False, "significance": "unknown"},
                     {"change_id": "other", "discard": True, "discard_reason": "真消失"}]
            latest = {"run_at": "2026-10-27T01:00:00+08:00", "changes": [
                {"change_id": "other", "title": "t", "url": "u"}]}  # legacy 不在窗口
            core.jdump(os.path.join(tmp, "out", "latest.json"), latest)
            core.jdump(os.path.join(tmp, "judged.json"), {"intel": intel, "facts": []})
            self.assertEqual(judge.cmd_input(tmp, os.path.join(tmp, "judged.json")), 0)

    def test_suppressed_tombstone_without_title_passes(self):
        """带 suppressed_by_guard 的墓碑（无 title）放行——防重发契约不被误伤。"""
        with tempfile.TemporaryDirectory(prefix="mi-guard-") as tmp:
            os.makedirs(os.path.join(tmp, "out"))
            os.makedirs(os.path.join(tmp, "log"))
            intel = [{"change_id": "a", "significance": "unknown", "notify": False,
                      "suppressed_by_guard": True}]
            latest = {"run_at": "2026-10-27T01:00:00+08:00", "changes": [
                {"change_id": "a", "title": "t", "url": "u"}]}
            core.jdump(os.path.join(tmp, "out", "latest.json"), latest)
            core.jdump(os.path.join(tmp, "judged.json"), {"intel": intel, "facts": []})
            self.assertEqual(judge.cmd_input(tmp, os.path.join(tmp, "judged.json")), 0)


class TestCoverageHistoryCredit(unittest.TestCase):
    """历史登记视为已判：facts 补录/增量判定不被覆盖度误拦，且已判条目被核销出 pending。"""

    def _ws(self, tmp, latest_changes, log_lines):
        os.makedirs(os.path.join(tmp, "out"))
        os.makedirs(os.path.join(tmp, "log"))
        core.jdump(os.path.join(tmp, "out", "latest.json"),
                   {"run_at": "2026-10-27T01:00:00+08:00", "changes": latest_changes})
        with open(os.path.join(tmp, "log", "intel.jsonl"), "w") as f:
            for l in log_lines:
                f.write(json.dumps(l) + "\n")

    def test_facts_only_input_passes_coverage(self):
        """窗口条目全部已判时，facts-only 输入放行（此前误拦退出码 2）。"""
        with tempfile.TemporaryDirectory(prefix="mi-cov-") as tmp:
            self._ws(tmp, [{"change_id": "a", "title": "t", "url": "u"}],
                     [{"change_id": "a", "title": "旧文", "notify": False}])
            jp = os.path.join(tmp, "judged.json")
            core.jdump(jp, {"intel": [], "facts": []})
            self.assertEqual(judge.cmd_input(tmp, jp), 0)

    def test_unjudged_item_still_blocks(self):
        """历史未覆盖的条目仍须本轮回答——护栏语义不放松。"""
        with tempfile.TemporaryDirectory(prefix="mi-cov-") as tmp:
            self._ws(tmp, [{"change_id": "a", "title": "t", "url": "u"},
                           {"change_id": "b", "title": "t2", "url": "u2"}],
                     [{"change_id": "a", "title": "旧文", "notify": False}])
            jp = os.path.join(tmp, "judged.json")
            core.jdump(jp, {"intel": [], "facts": []})
            with self.assertRaises(SystemExit) as cm:
                judge.cmd_input(tmp, jp)
            self.assertEqual(cm.exception.code, 2)

    def test_history_judged_items_cleared_from_pending(self):
        """增量输入核销后，历史已判条目不得留在 pending（防下轮 reopened 误回放）。"""
        with tempfile.TemporaryDirectory(prefix="mi-cov-") as tmp:
            self._ws(tmp, [{"change_id": "a", "title": "t", "url": "u"},
                           {"change_id": "b", "title": "t2", "url": "u2"}],
                     [{"change_id": "a", "title": "旧文", "notify": False}])
            # 预置 pending：a（历史已判）+ b（本轮判）
            core.jdump(os.path.join(tmp, "state", "pending.json"),
                       {"items": [{"change_id": "a"}, {"change_id": "b"}]})
            jp = os.path.join(tmp, "judged.json")
            core.jdump(jp, {"intel": [{"change_id": "b", "discard": True,
                                       "discard_reason": "真消失"}], "facts": []})
            self.assertEqual(judge.cmd_input(tmp, jp), 0)
            p = core.jload(os.path.join(tmp, "state", "pending.json"), {})
            self.assertEqual([i["change_id"] for i in p.get("items", [])], [],
                             "历史已判的 a 与本轮判的 b 都应被核销")


class TestSuppressedDigestFilter(unittest.TestCase):
    """sent-guard 压制标记：已通报内容不得被内参重新打包（洁净重置全量窗防旧闻重推）。"""

    def test_demote_marks_suppressed(self):
        intel = [{"change_id": "a", "notify": True},
                 {"change_id": "b", "notify": True}]
        out, n = judge.demote_already_sent(intel, {"a"})
        self.assertEqual(n, 1)
        self.assertTrue(out[0].get("suppressed_by_guard"), "压制记录必须带独立标记")
        self.assertNotIn("suppressed_by_guard", out[1], "未命中记录不得误标")

    def test_digest_excludes_suppressed_records(self):
        """cmd_digest 产卡时剔除 suppressed_by_guard 记录，保留 notify=false 的归档记录。"""
        ws = tempfile.mkdtemp(prefix="mi-digest-")
        os.makedirs(os.path.join(ws, "log"), exist_ok=True)
        os.makedirs(os.path.join(ws, "out"), exist_ok=True)
        now = "2026-10-27T01:00:00+08:00"
        recs = [
            {"change_id": "old1", "type": "行业动态", "significance": "normal",
             "title": "已通报旧闻", "summary": "S", "url": "https://x/1",
             "sources": [], "notify": False, "suppressed_by_guard": True,
             "logged_at": now},
            {"change_id": "arc1", "type": "行业动态", "significance": "normal",
             "title": "归档展示内容", "summary": "S", "url": "https://x/2",
             "sources": [], "notify": False,
             "logged_at": now},
        ]
        with open(os.path.join(ws, "log", "intel.jsonl"), "w") as f:
            for r in recs:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        rc = judge.cmd_digest(ws)
        self.assertEqual(rc, 0)
        cards = [f for f in os.listdir(os.path.join(ws, "out")) if f.startswith("card-daily")]
        if cards:  # 产了卡就必须不含已通报旧闻
            text = json.dumps(json.load(open(os.path.join(ws, "out", cards[0]))),
                              ensure_ascii=False)
            self.assertNotIn("已通报旧闻", text, "sent-guard 压制内容不得进内参")
            self.assertIn("归档展示内容", text, "AI 判定不通知的归档内容保留展示")


class TestColdStartVsNewSource(unittest.TestCase):
    """冷启动（全新环境）必须产变更，不能因"无 prev"把整轮判成 baseline。

    2026-10-27 回归事故：陷阱 50 的 `if not prev` 是**逐源**判断，
    全新环境里所有源都没有快照 → 全部只建基线 → `total=0`，
    与 SOP「全量首扫产出全量变更」直接矛盾（实测 16 源 256 条全被吞）。
    判别标准：本轮开始前 `state/streams/` 一个快照都没有 = 冷启动 → 产变更；
    有别的源快照、只本源没有 = 老环境加新源 → 只建基线（陷阱 50 原意）。
    """

    def _mk(self, name, has_other_snapshot):
        ws = tempfile.mkdtemp(prefix="mi-cold-")
        os.makedirs(os.path.join(ws, "state", "streams"), exist_ok=True)
        if has_other_snapshot:
            core.jdump(os.path.join(ws, "state", "streams", "existing.json"),
                       {"entries": [{"key": "old", "title": "旧"}]})
        return ws

    def test_cold_start_detected_when_no_snapshots(self):
        """一个快照都没有 → 适用冷启动（产变更）。"""
        ws = self._mk("cold", has_other_snapshot=False)
        streams = os.path.join(ws, "state", "streams")
        had = os.path.isdir(streams) and any(
            f.endswith(".json") for f in os.listdir(streams))
        self.assertFalse(had, "空 streams 目录应判定为冷启动")

    def test_cold_start_filters_archived_old_items(self):
        """冷启动必须把「早于窗口」的归档旧文剔掉，否则两年历史全进判定窗。

        实测：DeepSeek 官方动态 19 条覆盖 2024-07 起两年，冷启动全判 added →
        用户看到的正是"严重过时的旧闻当新动态"（这条抱怨换了个入口复发）。
        口径：只滤**可解析时间**的条目，`published=None` 一律保留
        （活动行/榜单/页面监测无时间戳且都是当前窗口内容）。
        """
        import pipeline
        from unittest import mock
        import datetime

        ws = self._mk("e2e-cold-age", has_other_snapshot=False)
        core.jdump(os.path.join(ws, "sources.json"), {
            "version": 1, "settings": {"cold_start_max_age_days": 30},
            "sources": [{"id": "arch", "name": "归档源", "kind": "rss",
                         "url": "https://e/f", "enabled": True, "serves": ["intel"]}]})
        core.jdump(os.path.join(ws, "rules.json"), core.jload(
            os.path.join(core.SCRIPT_DIR, "..", "config", "rules.json.example"), None))

        now = datetime.datetime.now()
        fresh_iso = (now - datetime.timedelta(days=2)).isoformat()
        old_iso = (now - datetime.timedelta(days=800)).isoformat()
        entries = [
            {"key": "https://e/new", "title": "近期新文", "link": "https://e/new",
             "published": fresh_iso, "summary": "", "text": "t"},
            {"key": "https://e/old", "title": "两年前归档", "link": "https://e/old",
             "published": old_iso, "summary": "", "text": "t"},
            {"key": "https://e/nots", "title": "无时间戳条目", "link": "https://e/nots",
             "published": None, "summary": "", "text": "t"},
        ]
        with mock.patch.object(pipeline.collect, "run_collection",
                               return_value=[(True, entries, None)]):
            with mock.patch.object(pipeline, "acquire_lock",
                                   return_value=open(os.devnull, "w")):
                pipeline.run_round(ws)
        latest = core.jload(os.path.join(ws, "out", "latest.json"), {})
        titles = [c.get("title") for c in (latest.get("changes") or [])]
        self.assertIn("近期新文", titles, "窗口内新文必须保留")
        self.assertIn("无时间戳条目", titles, "无时间戳必须保留（否则活动行/榜单全丢）")
        self.assertNotIn("两年前归档", titles, "归档旧文必须被窗口剔除")

    def test_cold_start_snapshot_keeps_all_entries(self):
        """冷启动过滤只影响「产变更」，快照必须存完整条目。

        否则全被过滤时快照为空 → 次轮 `load_snapshot` 返回 {} → 又判"新源"
        重走 baseline（2026-10-27 实测：qwen-blog 40 条全过滤 → 快照空 →
        次轮报「无历史快照」）。这是"过滤快照"而非"过滤变更集"的经典踩坑。
        """
        import pipeline
        from unittest import mock
        import datetime

        ws = self._mk("e2e-snap", has_other_snapshot=False)
        core.jdump(os.path.join(ws, "sources.json"), {
            "version": 1, "settings": {"cold_start_max_age_days": 30},
            "sources": [{"id": "arch", "name": "全归档源", "kind": "rss",
                         "url": "https://e/f", "enabled": True, "serves": ["intel"]}]})
        core.jdump(os.path.join(ws, "rules.json"), core.jload(
            os.path.join(core.SCRIPT_DIR, "..", "config", "rules.json.example"), None))

        old_iso = (datetime.datetime.now() - datetime.timedelta(days=800)).isoformat()
        entries = [{"key": "https://e/o%d" % i, "title": "旧文%d" % i,
                    "link": "https://e/o%d" % i, "published": old_iso,
                    "summary": "", "text": "t"} for i in range(5)]
        with mock.patch.object(pipeline.collect, "run_collection",
                               return_value=[(True, entries, None)]):
            with mock.patch.object(pipeline, "acquire_lock",
                                   return_value=open(os.devnull, "w")):
                pipeline.run_round(ws)
        latest = core.jload(os.path.join(ws, "out", "latest.json"), {})
        self.assertEqual((latest.get("summary") or {}).get("total"), 0,
                         "归档旧文不应产变更")
        snap = diffcore.load_snapshot(ws, "arch")
        self.assertEqual(len(snap), 5,
                         "快照必须存完整 5 条（否则次轮误判为新源，实测过）")

    def test_new_source_in_old_ws_not_cold_start(self):
        """已有其他源快照 → 不是冷启动（本源仍走只建基线）。"""
        ws = self._mk("warm", has_other_snapshot=True)
        streams = os.path.join(ws, "state", "streams")
        had = os.path.isdir(streams) and any(
            f.endswith(".json") for f in os.listdir(streams))
        self.assertTrue(had, "已有快照时不应判定为冷启动")

    def test_cold_start_produces_changes(self):
        """端到端：冷启动首扫必须产出变更（不能是 0）。"""
        import pipeline
        from unittest import mock

        ws = self._mk("e2e-cold", has_other_snapshot=False)
        # 最小配置
        core.jdump(os.path.join(ws, "sources.json"), {
            "version": 1, "settings": {},
            "sources": [{"id": "s1", "name": "示例源", "kind": "rss",
                         "url": "https://e/feed", "enabled": True,
                         "serves": ["intel"]}]})
        core.jdump(os.path.join(ws, "rules.json"), core.jload(
            os.path.join(core.SCRIPT_DIR, "..", "config", "rules.json.example"), None))
        entries = [{"key": "https://e/%d" % i, "title": "条目%d" % i,
                    "link": "https://e/%d" % i, "published": None,
                    "summary": "", "text": "t"} for i in range(3)]
        with mock.patch.object(pipeline.collect, "run_collection",
                               return_value=[(True, entries, None)]):
            with mock.patch.object(pipeline, "acquire_lock",
                                   return_value=open(os.devnull, "w")):
                rc = pipeline.run_round(ws)
        self.assertEqual(rc, 0)
        latest = core.jload(os.path.join(ws, "out", "latest.json"), {})
        total = (latest.get("summary") or {}).get("total")
        self.assertEqual(total, 3, "冷启动首扫应把全部条目标为新增，实际 total=%s" % total)


class TestInitCompleteness(unittest.TestCase):
    """新环境初始化必须把「用户要维护的配置」全部落到工作区（2026-10-27 实测缺失）。

    事故：`init_ws` 只复制 sources/entities/rules/notify 四份，**漏了 denylist.json**
    —— v0.6.0 引入的黑名单文件。`load_denylist` 有内置模板回退所以功能不崩，
    但文件不在工作区时用户看不到、改不了，增删名单静默不生效（设计意图是
    「名单由用户改这个文件」）。空工作区 --run 还会静默用 config 模板跑通。
    """

    def test_init_copies_denylist(self):
        """--init 必须把 denylist.json 复制到工作区。"""
        import pipeline
        ws = tempfile.mkdtemp(prefix="mi-init-")
        rc = pipeline.init_ws(ws)
        self.assertEqual(rc, 0)
        self.assertTrue(os.path.exists(os.path.join(ws, "denylist.json")),
                        "init 未复制 denylist.json，用户无法维护黑名单")
        d = core.jload(os.path.join(ws, "denylist.json"), {})
        self.assertTrue(d.get("deny_entities"), "denylist 模板内容为空")
        self.assertTrue(d.get("deny_by_round"), "denylist 缺轮次口径")

    def test_init_copies_all_user_configs(self):
        """四份核心配置 + 黑名单：一个不能少。"""
        import pipeline
        ws = tempfile.mkdtemp(prefix="mi-init2-")
        pipeline.init_ws(ws)
        for name in ("sources.json", "entities.json", "rules.json",
                     "notify.json", "denylist.json"):
            self.assertTrue(os.path.exists(os.path.join(ws, name)),
                            "init 缺 %s" % name)


class TestCleanCommand(unittest.TestCase):
    """`--clean` 是洁净重置的单点定义（2026-10-27 新增）。

    动机：SOP 第 2 步原要求 AI 手工敲 rm 删 6 类文件，历史上漏删过
    `log/intel.jsonl`（增量差分失效）、也多删过配置。把「删什么/留什么」
    写死在代码里，比每轮现编命令可靠。安全要求：**默认只演练，--yes 才删**。
    """

    def setUp(self):
        import pipeline
        self.pipeline = pipeline
        self.ws = tempfile.mkdtemp(prefix="mi-clean-")
        core.workspace(self.ws)
        # 造出"脏"状态
        for rel in ("state/streams/x.json", "state/digest_anchor.json",
                    "state/pending.json", "out/a.json", "log/intel.jsonl",
                    "log/discarded.jsonl", "log/rolled_out.jsonl", "log/runs.jsonl"):
            p = os.path.join(self.ws, rel)
            os.makedirs(os.path.dirname(p), exist_ok=True)
            core.jdump(p, {"x": 1})
        # 造出"必须保留"的配置与业务记忆
        for rel in ("sources.json", "rules.json", "notify.json", "entities.json",
                    "denylist.json", "log/leads.jsonl", "log/enrichments.jsonl",
                    "state/hero_images.json"):
            core.jdump(os.path.join(self.ws, rel), {"keep": 1})

    def test_dry_run_deletes_nothing(self):
        """不带 --yes 必须零删除（演练模式）。"""
        before = sorted(os.path.relpath(p, self.ws) for p, _, _ in os.walk(self.ws))
        self.pipeline.clean_ws(self.ws, yes=False)
        after = sorted(os.path.relpath(p, self.ws) for p, _, _ in os.walk(self.ws))
        self.assertEqual(before, after, "演练模式不应删除任何东西")
        self.assertTrue(os.path.exists(os.path.join(self.ws, "log", "intel.jsonl")))

    def test_yes_deletes_baseline_keeps_memory(self):
        """--yes 删基线，但配置与业务记忆必须全保留。"""
        self.pipeline.clean_ws(self.ws, yes=True)
        gone = ["state/streams/x.json", "state/digest_anchor.json", "state/pending.json",
                "out/a.json", "log/intel.jsonl", "log/discarded.jsonl",
                "log/rolled_out.jsonl", "log/runs.jsonl"]
        for rel in gone:
            self.assertFalse(os.path.exists(os.path.join(self.ws, rel)),
                             "%s 应被删除" % rel)
        kept = ["sources.json", "rules.json", "notify.json", "entities.json",
                "denylist.json", "log/leads.jsonl", "log/enrichments.jsonl",
                "state/hero_images.json"]
        for rel in kept:
            self.assertTrue(os.path.exists(os.path.join(self.ws, rel)),
                            "%s 必须保留（环境身份/业务记忆）" % rel)
        # 目录要重建，否则下游首次运行报路径不存在
        self.assertTrue(os.path.isdir(os.path.join(self.ws, "state", "streams")))
        self.assertTrue(os.path.isdir(os.path.join(self.ws, "out")))

    def test_new_runtime_file_auto_cleaned(self):
        """保留清单驱动：未来新增的运行时文件也必须被清（防漏删）。

        历史坑：早期用「列举要删的」清单，`state/health.json` 就没被列进去 →
        静默保留旧 down 状态 → 清基线后仍报 down，误判源健康。
        改成「列举要留的」后，任何新产物自动落到删除侧。
        """
        self.pipeline.clean_ws(self.ws, yes=True)
        # 造三类"未来可能新增"的产物
        for rel in ("state/health.json", "state/some_future_cache.json",
                    "log/some_new_log.jsonl", "out/sub/dir/x.json"):
            p = os.path.join(self.ws, rel)
            os.makedirs(os.path.dirname(p), exist_ok=True)
            core.jdump(p, {"x": 1})
        self.pipeline.clean_ws(self.ws, yes=True)
        for rel in ("state/health.json", "state/some_future_cache.json",
                    "log/some_new_log.jsonl", "out/sub/dir/x.json"):
            self.assertFalse(os.path.exists(os.path.join(self.ws, rel)),
                             "%s 是运行时产物，应被自动清理" % rel)
        # 保留项仍在
        for rel in ("sources.json", "denylist.json", "log/leads.jsonl"):
            self.assertTrue(os.path.exists(os.path.join(self.ws, rel)),
                            "%s 必须保留" % rel)

    def test_health_json_not_kept(self):
        """state/health.json 必须清（旧 down 状态会污染新环境的健康判断）。"""
        core.jdump(os.path.join(self.ws, "state", "health.json"),
                   {"s1": {"fails": 3, "status": "down"}})
        self.pipeline.clean_ws(self.ws, yes=True)
        self.assertFalse(os.path.exists(os.path.join(self.ws, "state", "health.json")),
                         "源健康账簿属于运行时状态，新环境必须重置")


class TestDryRun(unittest.TestCase):
    """--dry-run 沙箱预检：全流程演练，真实工作区零写入；问题在落盘前暴露。"""

    @staticmethod
    def _mk_ws():
        ws = tempfile.mkdtemp(prefix="mi-test-")
        os.makedirs(os.path.join(ws, "out"), exist_ok=True)
        changes = [{"change_id": "a1", "source_id": "s", "source_name": "测试源",
                    "serves": ["intel"], "tier": "T2", "source_kind": "rss",
                    "change_kind": "added", "key": "k1", "url": "https://x/1",
                    "title": "测试标题", "summary": "测试摘要"}]
        latest = {"run_at": "2026-10-27T00:00:00+08:00", "summary": {},
                  "health": {}, "changes": changes}
        with open(os.path.join(ws, "out", "latest.json"), "w") as f:
            json.dump(latest, f, ensure_ascii=False)
        return ws

    @staticmethod
    def _write_judged(ws, intel):
        jp = os.path.join(ws, "judged.json")
        with open(jp, "w") as f:
            json.dump({"intel": intel, "facts": []}, f, ensure_ascii=False)
        return jp

    def test_dry_run_passes_and_writes_nothing_real(self):
        ws = self._mk_ws()
        jp = self._write_judged(ws, [{"change_id": "a1", "type": "行业动态",
                                       "significance": "normal", "title": "测试标题",
                                       "summary": "测试摘要", "url": "https://x/1",
                                       "sources": [{"name": "测试源", "url": "https://x/1"}],
                                       "notify": True, "notify_reason": "预检"}])
        rc = judge.cmd_input(ws, jp, dry_run=True)
        self.assertEqual(rc, 0)
        self.assertFalse(os.path.exists(os.path.join(ws, "log")),
                         "dry-run 不得在真实工作区创建 log")
        leftover = [f for f in os.listdir(os.path.join(ws, "out"))
                    if f.startswith("card-")]
        self.assertEqual(leftover, [], "dry-run 不得在真实工作区产生卡片")

    def test_dry_run_catches_bad_record(self):
        """问题判定（未知字段）在 dry-run 被拦截退出，真实工作区仍零写入。"""
        ws = self._mk_ws()
        jp = self._write_judged(ws, [{"change_id": "a1", "type": "行业动态",
                                       "significance": "normal", "title": "T",
                                       "summary": "S", "url": "https://x/1",
                                       "sources": [], "notify": True,
                                       "notify_reason": "r", "oops": 1}])
        with self.assertRaises(SystemExit) as ctx:
            judge.cmd_input(ws, jp, dry_run=True)
        self.assertEqual(ctx.exception.code, 1)
        self.assertFalse(os.path.exists(os.path.join(ws, "log")))


# ============================================================================
# v0.6.5：全链路审计补测（2026-10-27 第三轮深度审查）
# 每一条都对应一个**实测复现过的缺陷**，不是"看起来应该这样"。
# ============================================================================

class TestDenylistWordBoundary(unittest.TestCase):
    """黑名单拉丁词条必须按词边界匹配（v0.6.5 修，最严重的一类误伤）。

    实测：旧实现一律子串匹配，黑名单里的短英文名把大量**真正的腰部黑马**全拦掉：
      `nio`（蔚来）→ Union AI / Senior Robotics / Companion Labs / Onion Games / Pinion AI
      `meta`（Meta）→ Metaverse Works / MetaStudio / Metamorphic Games
      `apple`（Apple）→ Pineapple Studio
      `grab`/`goto`（Grab/GoTo）→ Grabber Tech / GoTopper
      `branch`（Branch 归因）→ Branchline Media
    14 个候选 100% 误伤。用户的核心诉求恰恰是"专盯腰部黑马"——**误伤比漏过滤更严重**：
    漏过滤只是多一条噪音，误伤是目标客户永久消失且线索池无从发现。
    """

    def setUp(self):
        sys.path.insert(0, core.SCRIPT_DIR)
        self.deny = match_mod.load_denylist(tempfile.mkdtemp(prefix="mi-wb-"))

    def test_target_companies_not_falsely_blocked(self):
        """短英文黑名单词不得误伤同名片段的成长型公司。"""
        cases = ["Union AI", "Senior Robotics", "Companion Labs", "Onion Games",
                 "Pinion AI", "Metaverse Works", "MetaStudio", "Metamorphic Games",
                 "Pineapple Studio", "Grabber Tech", "GoTopper", "Branchline Media"]
        bad = []
        for name in cases:
            hit, why = match_mod.check_denylist({"name": name, "aliases": []}, self.deny)
            if hit:
                bad.append("%s（%s）" % (name, why))
        self.assertEqual(bad, [], "以下目标客群被黑名单误伤: %s" % "、".join(bad))

    def test_real_giants_still_blocked(self):
        """修边界不许把真巨头放过（含别名、带后缀、带空格的写法）。"""
        cases = ["希音", "SHEIN", "字节跳动", "ByteDance", "腾讯", "米哈游",
                 "Meta", "Meta Platforms", "Apple", "Apple Inc.", "Grab", "GoTo",
                 "NIO", "蔚来", "Branch", "声网", "Agora", "钛动科技"]
        miss = []
        for name in cases:
            hit, _ = match_mod.check_denylist({"name": name, "aliases": []}, self.deny)
            if not hit:
                miss.append(name)
        self.assertEqual(miss, [], "以下真巨头/服务商漏拦: %s" % "、".join(miss))

    def test_chinese_substring_still_works(self):
        """中文仍走子串匹配：'希音集团' / '腾讯云' 这类组合名必须命中。"""
        self.assertTrue(match_mod.check_denylist({"name": "希音集团", "aliases": []}, self.deny)[0])
        self.assertTrue(match_mod.check_denylist({"name": "腾讯云科技", "aliases": []}, self.deny)[0])

    def test_alias_path_uses_same_boundary(self):
        """别名同样按词边界——别名 "Apple" 命中，别名 "Pineapple" 不命中。"""
        self.assertTrue(match_mod.check_denylist(
            {"name": "某科技", "aliases": ["Apple"]}, self.deny)[0])
        self.assertFalse(match_mod.check_denylist(
            {"name": "某科技", "aliases": ["Pineapple"]}, self.deny)[0])


class TestRoundNormalization(unittest.TestCase):
    """融资轮次归一化（v0.6.5 修）：空格与英文写法曾全部漏拦。

    实测漏拦：`C 轮`（带空格）、`Series C`、`Series C+`、`series d`、`E 轮`
    ——用户明令"已上市或 C 轮及以上绝对丢弃"，这些公司全被当成长型公司放行。
    """

    def setUp(self):
        sys.path.insert(0, core.SCRIPT_DIR)
        self.deny = match_mod.load_denylist(tempfile.mkdtemp(prefix="mi-rnd-"))

    def test_spaced_and_english_rounds_blocked(self):
        """带空格 / 英文 Series 写法都要拦。"""
        for rnd in ("C轮", "C 轮", "C+轮", "D轮", "E 轮", "Series C", "Series C+",
                    "series d", "Pre-IPO轮", "战略融资", "IPO上市"):
            hit, why = match_mod.check_denylist(
                {"name": "某成长公司", "aliases": [], "funding_events": [{"round": rnd}]},
                self.deny)
            self.assertTrue(hit, "%r 应被轮次拦截（%s）" % (rnd, why))

    def test_target_rounds_still_pass(self):
        """修归一化不许误杀目标轮次。"""
        for rnd in ("种子轮", "天使轮", "Pre-A轮", "A轮", "A+轮", "Pre-B轮", "B轮", "B+轮",
                    "Series A", "Series B"):
            hit, why = match_mod.check_denylist(
                {"name": "某成长公司", "aliases": [], "funding_events": [{"round": rnd}]},
                self.deny)
            self.assertFalse(hit, "%r 属目标轮次，不该拦（%s）" % (rnd, why))


class TestUnlistedCheckMerge(unittest.TestCase):
    """`deny_unlisted_check` 必须随模板补齐，且用户显式 false 不得被覆盖（v0.6.5）。

    实测缺陷：用户只覆盖实体名单（`{"version":1,"deny_entities":[...]}`）时该键恒为
    None → 「已上市」检测整段失效，含"纳斯达克上市"的档案未被拦截。
    """

    def setUp(self):
        sys.path.insert(0, core.SCRIPT_DIR)
        self.ws = tempfile.mkdtemp(prefix="mi-ul-")
        core.workspace(self.ws)

    def _write(self, content):
        core.jdump(os.path.join(self.ws, "denylist.json"), content)

    def test_missing_key_filled_from_builtin(self):
        """只给实体名单 → unlisted 开关应被模板补齐为 True。"""
        self._write({"version": 1, "deny_entities": ["只测这家"]})
        d = match_mod.load_denylist(self.ws)
        self.assertTrue(d.get("deny_unlisted_check"), "该开关必须随模板补齐，否则已上市检测失效")
        hit, _ = match_mod.check_denylist(
            {"name": "某公司", "profile": "该公司于 2025 年在纳斯达克上市"}, d)
        self.assertTrue(hit, "补齐后已上市字样必须被拦")

    def test_explicit_false_not_overridden(self):
        """用户显式写 false = 主动关闭，模板不得覆盖回来（保留出口）。"""
        self._write({"version": 1, "deny_entities": ["x"], "deny_unlisted_check": False})
        d = match_mod.load_denylist(self.ws)
        self.assertFalse(d.get("deny_unlisted_check"), "显式 false 必须被尊重")


class TestIntelCardNoTruncation(unittest.TestCase):
    """情报卡 / 线索卡 / digest 降级分支的 notify 截断（v0.6.5 修，陷阱 58）。

    陷阱 47 声称"四段统一豁免"（活动/新闻/榜单/线索），实际只落地了三段。
    本轮实测出另外三处硬截断，全部导致**判定成功但永久漏发**（锚点推进后
    `logged_at` 早于新锚点，下期不再出现）。
    """

    def test_intel_blocks_exempts_notify(self):
        """情报卡：9 条 notify=true，旧实现 limit=5 只渲染 5 条。"""
        recs = [{"change_id": "c%d" % i, "type": "技术战报", "significance": "normal",
                 "title": "情报标题%d" % i, "summary": "s", "notify": True,
                 "sales_playbook": "针对跨境电商客户，用 Gemini 多模态生成商品图文替代第三方 API，"
                                   "话术：数据驻留合规 + 全球骨干网低延迟。",
                 "sources": [{"name": "s", "url": "http://x"}]} for i in range(9)]
        blocks, total = judge.intel_blocks(recs)
        blob = json.dumps(blocks, ensure_ascii=False)
        missing = [i for i in range(9) if ("情报标题%d" % i) not in blob]
        self.assertEqual(missing, [], "情报卡 notify=true 不许截断，漏了 %s" % missing)
        self.assertEqual(total, 9)

    def test_intel_blocks_archive_still_limited(self):
        """情报卡：notify=false 归档仍按 limit 收敛，不无限膨胀。"""
        recs = ([{"change_id": "n0", "title": "要通知", "notify": True, "significance": "normal"}]
                + [{"change_id": "a%d" % i, "title": "归档%d" % i, "notify": False,
                    "significance": "low"} for i in range(10)])
        blob = json.dumps(judge.intel_blocks(recs)[0], ensure_ascii=False)
        self.assertIn("要通知", blob)
        self.assertLessEqual(sum(1 for i in range(10) if "归档%d" % i in blob), 5)

    def test_lead_elements_exempts_notify(self):
        """线索段：6 条 P0 线索，旧实现 limit=3 只展示 3 条。"""
        leads = [{"lead_id": "l%d" % i, "entity_name": "客户%d" % i, "priority": "P0",
                  "score": 95, "notify": True, "industries": ["短剧"],
                  "matched_groups": ["g"],
                  "logged_at": "2026-10-27T10:0%d:00+08:00" % i} for i in range(6)]
        blob = json.dumps(judge._lead_elements(leads), ensure_ascii=False)
        missing = [i for i in range(6) if ("客户%d" % i) not in blob]
        self.assertEqual(missing, [], "P0 线索不许被 limit=3 截断，漏了 %s" % missing)

    def test_lead_blocks_exempts_notify(self):
        """线索卡：6 条 notify=true，旧实现 limit=5 只展示 5 条。"""
        leads = [{"lead_id": "l%d" % i, "entity_name": "公司%d" % i, "priority": "P0",
                  "score": 90, "notify": True, "matched_groups": ["g"]} for i in range(6)]
        blob = json.dumps(judge.lead_blocks(leads), ensure_ascii=False)
        missing = [i for i in range(6) if ("公司%d" % i) not in blob]
        self.assertEqual(missing, [], "线索卡 notify=true 不许截断，漏了 %s" % missing)

    def test_digest_fallback_exempts_notify(self):
        """digest simple 降级分支：旧实现 `[:4]` 预截断，8 条只渲染 4 条。"""
        ws = tempfile.mkdtemp(prefix="mi-dg-")
        core.workspace(ws)
        for i in range(8):
            core.append_jsonl(os.path.join(ws, "log", "intel.jsonl"), {
                "change_id": "d%d" % i, "type": "技术战报", "significance": "high",
                "title": "降级标题%d" % i, "summary": "s", "url": "http://x",
                "notify": True, "logged_at": core.now_iso(),
                "sales_playbook": "针对跨境电商客户，用 Gemini 多模态生成商品图文替代第三方 API，"
                                  "话术：数据驻留合规 + 全球骨干网低延迟。",
                "sources": [{"name": "s", "url": "http://x"}]})
        orig = judge.briefing_card
        judge.briefing_card = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("模拟渲染失败"))
        try:
            judge.cmd_digest(ws, commit=False)
        finally:
            judge.briefing_card = orig
        import glob
        path = sorted(glob.glob(os.path.join(ws, "out", "card-digest-*.json")))[-1]
        blob = json.dumps(json.load(open(path, encoding="utf-8")), ensure_ascii=False)
        missing = [i for i in range(8) if ("降级标题%d" % i) not in blob]
        self.assertEqual(missing, [], "降级分支同样不许截断 notify=true，漏了 %s" % missing)


class TestActivityWithoutDetail(unittest.TestCase):
    """type=活动情报 但缺 activity 明细，不得两头落空导致漏发（v0.6.5 修）。

    实测：活动段要求 `x.get("activity")`、新闻段按 `type != "活动情报"` 排除 →
    notify=true 的活动情报被两段同时抛弃，`briefing_card` 甚至返回 None（全空不产卡）。
    """

    def test_falls_back_to_news_section(self):
        recs = [{"change_id": "a1", "type": "活动情报", "significance": "high",
                 "notify": True, "title": "某出海大会", "summary": "11月举办",
                 "url": "http://x", "sources": [{"name": "s", "url": "http://x"}]}]
        card = judge.briefing_card(recs, [])
        self.assertIsNotNone(card, "有 notify=true 内容时不得返回空卡")
        self.assertIn("某出海大会", json.dumps(card, ensure_ascii=False),
                      "缺 activity 明细的活动情报必须降级进新闻段，不能被丢弃")

    def test_normal_activity_keeps_calendar_section(self):
        """带 activity 明细的正常活动仍走活动日历段（修复不许改变正常路径）。"""
        recs = [{"change_id": "a2", "type": "活动情报", "significance": "high",
                 "notify": True, "title": "出海峰会", "summary": "x", "url": "http://x",
                 "sources": [{"name": "s", "url": "http://x"}],
                 "activity": {"name": "出海峰会", "time": "11月1日", "location": "北京",
                              "highlight": "面向出海团队"}}]
        blob = json.dumps(judge.briefing_card(recs, []), ensure_ascii=False)
        self.assertIn("WebEye活动日历", blob)

    def test_guard_rejects_notify_without_detail(self):
        """判定落盘：缺 activity 明细但**无 title** 才拦（有 title 走降级渲染，不算漏发）。

        口径说明：activity 明细是**排版增益**（让活动进日历段、带上时间地点），
        不是内容本身。有 title/summary 的记录即使缺明细也能正常渲染进新闻段，
        硬拦它等于把"排版不完美"升级成"判定失败"，属过度约束。真正会丢内容的是
        **既缺 activity 又缺 title** 的裸记录——由既有骨架护栏拦截。
        """
        ws = tempfile.mkdtemp(prefix="mi-act-")
        core.workspace(ws)
        core.jdump(os.path.join(ws, "out", "latest.json"),
                   {"run_at": core.now_iso(), "changes": [], "summary": {}})
        jp = os.path.join(ws, "judged.json")
        core.jdump(jp, {"intel": [{"change_id": "a1", "type": "活动情报",
                                   "significance": "high", "title": "某活动",
                                   "summary": "s", "url": "http://x", "sources": [],
                                   "notify": True, "notify_reason": "r"}]})
        # 有 title → 应正常落盘（降级渲染），不 exit
        self.assertEqual(judge.cmd_input(ws, jp, dry_run=True), 0)


class TestListOrdinalStrip(unittest.TestCase):
    """列表页序号污染标题（v0.6.5 修，显示缺陷类）。

    实测（品牌方舟列表页）：锚文本为「5. AI软硬件公司「Ambi」完成…」——「5.」是
    **页面列表序号**，不是标题内容。不剥会导致卡片里出现「3. 5. xxx」双重编号
    （新闻段自己也编号），读者以为漏了 1-4 条；且同一内容排到不同位时标题漂移。
    """

    def test_strips_leading_ordinal(self):
        cases = [
            ("5. AI软硬件公司「Ambi」完成1.74亿元Pre-A轮融资",
             "AI软硬件公司「Ambi」完成1.74亿元Pre-A轮融资"),
            ("4. 揽金6个亿，国产吸尘器在TikTok卖爆了！", "揽金6个亿，国产吸尘器在TikTok卖爆了！"),
            ("6. Meta推出AI广告、视频制作、结账等新功能", "Meta推出AI广告、视频制作、结账等新功能"),
            ("12、某产品完成A轮融资", "某产品完成A轮融资"),
        ]
        for src, want in cases:
            self.assertEqual(collect._strip_list_ordinal(src), want, "应剥掉列表序号: %r" % src)

    def test_does_not_touch_real_numeric_prefix(self):
        """不许误伤以数字开头的真实标题（3D/5G/100天/年份）。"""
        keep = ["3D 打印迎来新突破", "5G 芯片出货量翻倍", "100 天环游世界",
                "2026 卫星应用大会", "AI 文娱（短剧）产业生态大会"]
        for t in keep:
            self.assertEqual(collect._strip_list_ordinal(t), t, "不该动: %r" % t)

    def test_page_links_applies_strip(self):
        """links 模式端到端生效。"""
        body = '<a href="https://e.com/a">5. 某公司完成 Pre-A 轮融资</a>'
        entries = collect.parse_web_page(body, {"url": "https://e.com/",
                                                "config": {"mode": "links"}})
        self.assertEqual(entries[0]["title"], "某公司完成 Pre-A 轮融资")


class TestCardSizeGuard(unittest.TestCase):
    """卡片体积护栏（v0.6.6，飞书硬上限，本技能自己的回归）。

    事故链：v0.6.5 把 notify=true 改成「全部展示」防漏发（方向正确），
    但**没同时处理体积** → 真实内参卡膨胀到 39599 字节 / 178 元素，
    **双双超过飞书硬上限**（30KB / 200 元素）→ 发送必报 230025 / 300305 失败。
    更糟的是失败发生在 `--digest --commit` 之后（先产卡后推锚），内容已标已通报 → 永久丢失。

    官方限制来源（2026-10-27 核实）：
      - 30KB：open.feishu.cn/document/server-docs/im-v1/faq（"JSON 长度不超过 30KB"）
      - 200 元素：open.feishu.cn/document/cardkit-v1/card-element/create（错误码 300305）
    """

    def setUp(self):
        sys.path.insert(0, core.SCRIPT_DIR)
        self.ws = tempfile.mkdtemp(prefix="mi-card-size-")
        core.workspace(self.ws)

    def _recs(self, n):
        return [{"change_id": "c%d" % i, "type": "技术战报", "significance": "high",
                 "title": "情报标题%d：某模型降价" % i,
                 "summary": "这是第 %d 条摘要，包含足够多文字模拟真实判定内容。" % i,
                 "notify": True,
                 "sales_playbook": "客户拿低价压我们时不要跟价，把话题拉回合规与稳定性成本。"
                                   "话术：模型单价只是账单的一部分，数据驻留合规、全球骨干网"
                                   "低延迟、Vertex AI 统一托管带来的运维人力节省才是真实 TCO。",
                 "sources": [{"name": "测试源", "url": "http://x"}]} for i in range(n)]

    def test_limits_are_the_official_values(self):
        """常量必须与飞书官方一致（30KB / 200 元素），并留出余量。"""
        self.assertLessEqual(judge.CARD_MAX_BYTES, 30 * 1024)
        self.assertGreater(judge.CARD_MAX_BYTES, 28000, "余量过大等于白白浪费可用空间")
        self.assertLessEqual(judge.CARD_MAX_ELEMENTS, 200)

    def test_oversized_card_is_split(self):
        """超限卡必须被切成多张，且每张都在限制内（内容不许丢）。"""
        n = 45  # 实测 30 条约 27.6KB 未超限，45 条必超
        blocks, _ = judge.intel_blocks(self._recs(n))
        card = judge.build_card("测试简报", "t", blocks, template="violet")
        self.assertGreater(judge.card_bytes(card), judge.CARD_MAX_BYTES,
                           "前提：这张卡本身是超限的")
        paths = judge.write_card(self.ws, "card-test.json", card)
        self.assertIsInstance(paths, list, "超限应返回分片列表")
        self.assertGreater(len(paths), 1)
        for p in paths:
            c = json.load(open(p, encoding="utf-8"))
            self.assertLessEqual(judge.card_bytes(c), judge.CARD_MAX_BYTES,
                                 "%s 仍超 30KB" % p)
            self.assertLessEqual(judge.card_element_count(c), judge.CARD_MAX_ELEMENTS)
        # 内容完整性：全部标题一条都不许丢
        blob = "".join(json.dumps(json.load(open(p, encoding="utf-8")), ensure_ascii=False)
                       for p in paths)
        missing = [i for i in range(n) if ("情报标题%d：" % i) not in blob]
        self.assertEqual(missing, [], "分片后丢了内容: %s" % missing)

    def test_split_marks_part_index(self):
        """分片标题要带 (1/N) 标记，读者才知道内容有后续。"""
        blocks, _ = judge.intel_blocks(self._recs(45))
        card = judge.build_card("测试简报", "t", blocks, template="violet")
        paths = judge.write_card(self.ws, "card-test.json", card)
        paths = paths if isinstance(paths, list) else [paths]
        if len(paths) > 1:
            n = len(paths)
            for i, p in enumerate(paths, 1):
                title = json.load(open(p, encoding="utf-8"))["header"]["title"]["content"]
                self.assertIn("（%d/%d）" % (i, n), title)

    def test_normal_card_returns_str(self):
        """未超限时保持历史语义：返回单个 path 字符串（调用方不必改）。"""
        blocks, _ = judge.intel_blocks(self._recs(2))
        card = judge.build_card("小卡", "t", blocks, template="violet")
        r = judge.write_card(self.ws, "card-small.json", card)
        self.assertIsInstance(r, str)

    def test_giant_single_element_warns_not_drops(self):
        """单个元素本身超限：不可切割时必须告警保留，不得静默丢弃。"""
        card = judge.build_card("巨大块", "t",
                                [{"tag": "markdown", "content": "X" * 50000}], template="violet")
        paths = judge.write_card(self.ws, "card-huge.json", card)
        paths = paths if isinstance(paths, list) else [paths]
        blob = "".join(json.dumps(json.load(open(p, encoding="utf-8")), ensure_ascii=False)
                       for p in paths)
        self.assertEqual(blob.count("X"), 50000, "超限内容不许被丢掉")

    def test_element_count_counts_nested(self):
        """元素统计必须含 interactive_container 内嵌子元素（否则 200 限制形同虚设）。"""
        blocks, _ = judge.intel_blocks(self._recs(3))
        card = judge.build_card("x", "t", blocks, template="violet")
        # 每个 _block 是 1 个容器 + 内部若干 markdown
        self.assertGreater(judge.card_element_count(card), len(blocks))


class TestDigestPendingSend(unittest.TestCase):
    """待发送清单（v0.6.6）：给「发送 → 核对 → 推锚」留可核对凭据。

    `--digest --commit` 的缺陷：先产卡、再推锚，两步之间没有「发送成功」的确认点。
    一旦发送失败（如卡片超限被拒收），内容已标 suppressed_by_guard → 永久丢失且无从追溯。
    待发送清单把「这一批包含哪些卡、覆盖哪些记录」落盘，失败时可据此精确补发。
    """

    def setUp(self):
        sys.path.insert(0, core.SCRIPT_DIR)
        self.ws = tempfile.mkdtemp(prefix="mi-batch-")
        core.workspace(self.ws)
        core.append_jsonl(os.path.join(self.ws, "log", "intel.jsonl"), {
            "change_id": "k1", "type": "技术战报", "significance": "high",
            "title": "测试情报", "summary": "s", "url": "http://x", "notify": True,
            "sales_playbook": "客户拿低价压价时把话题拉回合规与稳定性成本，"
                              "话术：模型单价只是账单一部分，数据驻留合规与骨干网低延迟才是 TCO。",
            "logged_at": core.now_iso(), "sources": [{"name": "s", "url": "http://x"}]})

    def test_non_commit_writes_batch_manifest(self):
        """不带 --commit：产卡 + 落待发送清单，锚点不动。"""
        judge.cmd_digest(self.ws, commit=False)
        batch = os.path.join(self.ws, "state", "digest_pending_send.json")
        self.assertTrue(os.path.exists(batch), "必须落待发送清单，供发送后核对")
        rec = json.load(open(batch, encoding="utf-8"))
        self.assertGreaterEqual(rec["card_count"], 1)
        self.assertIn("k1", rec["record_ids"])
        self.assertFalse(os.path.exists(os.path.join(self.ws, "state", "digest_anchor.json")),
                         "未 commit 不许推进锚点")

    def test_commit_advances_and_clears_manifest(self):
        """带 --commit：推锚 + 清凭据（这一批已完成闭环）。"""
        judge.cmd_digest(self.ws, commit=True)
        self.assertTrue(os.path.exists(os.path.join(self.ws, "state", "digest_anchor.json")))
        self.assertFalse(os.path.exists(
            os.path.join(self.ws, "state", "digest_pending_send.json")),
            "已推锚应清掉凭据，避免陈旧清单误导补发")

    def test_card_files_match_manifest(self):
        """清单里的卡片路径必须真实存在（否则补发时找不到文件）。"""
        judge.cmd_digest(self.ws, commit=False)
        rec = json.load(open(os.path.join(self.ws, "state", "digest_pending_send.json"),
                             encoding="utf-8"))
        for p in rec["cards"]:
            self.assertTrue(os.path.exists(p), "清单引用了不存在的卡片: %s" % p)


class TestQuerySinceFilter(unittest.TestCase):
    """`query.py --since` 必须按时间戳比较，不能按字符串（v0.6.6 修）。

    旧实现 `(logged_at or "") >= since` 是字典序比较，跨时区直接算错：
    `2026-10-27T06:00:00Z` 与 `2026-10-27T14:00:00+08:00` 是**同一时刻**，
    但字符串 "06..." < "14..." → UTC 写法的记录被误判为"更早"，静默漏掉。
    """

    def setUp(self):
        sys.path.insert(0, core.SCRIPT_DIR)
        import query
        self.q = query

    def test_cross_timezone_equivalent(self):
        """UTC 写法与 +08:00 写法的同一时刻，必须同进同出。"""
        recs = [{"logged_at": "2026-10-27T06:00:00Z", "x": 1},
                {"logged_at": "2026-10-27T14:00:00+08:00", "x": 2},
                {"logged_at": "2026-10-26T10:00:00+08:00", "x": 3}]
        out = self.q._since_filter(recs, "2026-10-27T13:00:00+08:00")
        self.assertEqual([r["x"] for r in out], [1, 2],
                         "06:00Z 等于 14:00+08:00，两者都该命中；26 日那条不该命中")

    def test_date_only_since(self):
        """只给日期时，当天所有记录都该命中。"""
        recs = [{"logged_at": "2026-10-27T01:00:00+08:00", "x": 1},
                {"logged_at": "2026-10-26T23:00:00+08:00", "x": 2}]
        out = self.q._since_filter(recs, "2026-10-27")
        self.assertEqual([r["x"] for r in out], [1])

    def test_unparseable_since_degrades_with_warning(self):
        """无法解析时退化比较，不能抛异常也不能静默全丢。"""
        recs = [{"logged_at": "2026-10-27T10:00:00+08:00", "x": 1}]
        out = self.q._since_filter(recs, "不是时间")
        self.assertIsInstance(out, list)


class TestDuplicateDetection(unittest.TestCase):
    """同一事件多源报道的兜底检测（v0.6.7，用户实测反馈驱动）。

    用户原话：「很多信息实际是重复的，虽然标题和文案有些不一样，但实际表达内容是一致的，
    AI 协助和兜底就能很好的处理，为什么还会重？」

    事实核对：`intel-criteria.md` 早写了「同一事件多家报道：合并成一条，merged_change_ids
    列出被合并的 change_id，只通知一次」——**契约有、执行没做**。所以补的是"兜底"。

    设计底线：**只提示、不删改、不阻断**。
      - 「两篇是否同一事件」是语义问题，机械合并一定错改；
      - 自动删会造成静默漏发，比重复更严重（用户首要诉求是"不漏"）。

    判据经四次迭代，每次都是被实测打回：
      ① 整串特征重合 → 早报把多事件塞一条标题，特征稀释，漏判（0.36）
      ② 锚点含型号词 → Kimi 泄露 / DeepSeek 斩杀 GPT-6 共有 gpt-6，误报
      ③ 只比主体实体 → Kimi 发布 vs Kimi 泄露 主体都是 kimi，误报；Meta 被罚 vs Meta 合作同
      ④ **主体 + 事件类型双维度** ← 现在的方案，正反例全过
    """

    def setUp(self):
        sys.path.insert(0, core.SCRIPT_DIR)

    def _pairs(self, recs):
        return {frozenset((a["change_id"], b["change_id"]))
                for a, b, _ in judge.find_duplicate_intel(recs)}

    def test_same_event_detected(self):
        """同一事件多源报道必须检出。"""
        recs = [
            {"change_id": "a", "title": "Kimi 发布 K3.1 Pro，加量降价挑战 Opus 5.5"},
            {"change_id": "b", "title": "iPhone Duo正式交付；Kimi K3.1 Pro 、Seedance 2.5 4K正式发布｜极客早知道"},
            {"change_id": "d", "title": "DeepSeek 新模型上线，价格更低能力更强｜极客早知道"},
            {"change_id": "f", "title": "梁圣又掀桌了！DeepSeek新模型斩杀GPT-6，许愿式编程时代降临"},
        ]
        p = self._pairs(recs)
        self.assertIn(frozenset(("a", "b")), p, "Kimi K3.1 Pro 发布被早报重复报道，应检出")
        self.assertIn(frozenset(("d", "f")), p, "DeepSeek 新模型被两家报道，应检出")

    def test_same_entity_different_event_not_flagged(self):
        """同一主体的不同事件**不许**判重（这是判据能用的前提）。"""
        recs = [
            {"change_id": "a", "title": "Kimi 发布 K3.1 Pro，加量降价挑战 Opus 5.5"},
            {"change_id": "c", "title": "Kimi K3.1 泄露：3D 成本仅有 GPT-6 Astra 的 1/14"},
            {"change_id": "k", "title": "Meta 泄露用户隐私被罚 250 亿美元"},
            {"change_id": "l", "title": "英伟达与 Meta 宣布扩大 Muse 系列模型合作"},
        ]
        p = self._pairs(recs)
        self.assertNotIn(frozenset(("a", "c")), p, "Kimi 发布 ≠ Kimi 泄露，不许判重")
        self.assertNotIn(frozenset(("k", "l")), p, "Meta 被罚 ≠ Meta 合作，不许判重")

    def test_different_entity_never_flagged(self):
        """主体不同的事件不许判重（即使都属于"欧洲跨境"话题）。"""
        recs = [
            {"change_id": "h", "title": "欧洲百强在线市场GMV约1750亿欧元 近八成来自跨境"},
            {"change_id": "i", "title": "希音欧洲物流枢纽启动大规模招聘"},
            {"change_id": "j", "title": "速卖通欧洲官方配送降价 合作仓最高返30%物流费"},
        ]
        self.assertEqual(self._pairs(recs), set(), "主体不同，一个都不该报")

    def test_only_warns_never_deletes(self):
        """兜底只在 stderr 提示，绝不修改记录内容。"""
        recs = [{"change_id": "a", "title": "DeepSeek 新模型上线"},
                {"change_id": "b", "title": "DeepSeek新模型发布，能力更强"}]
        before = json.dumps(recs, ensure_ascii=False, sort_keys=True)
        n = judge.warn_duplicates(recs)
        after = json.dumps(recs, ensure_ascii=False, sort_keys=True)
        self.assertGreaterEqual(n, 1, "应报出候选")
        self.assertEqual(before, after, "绝不允许修改记录（只提示）")


class TestAutoMergeDuplicates(unittest.TestCase):
    """同一事件多源报道的**自动合并**（v0.6.7，用户实测反馈驱动）。

    用户原话：「很多信息实际是重复的…AI 协助和兜底就能很好的处理，为什么还会重？」
    → 只提示不够（提示了没人改，卡片照样重复），兜底必须真的合并。

    合并语义严格复用既有 `merged_change_ids` 契约，不新造字段：
      - 从条 change_id 进主条 merged_change_ids（覆盖度校验据此认可，不报缺口）
      - 从条 sources 并入主条（去重）
      - 从条标题留在 merged_titles（可溯源"哪几篇被合并了"）
    安全阀：只合并 notify=true 的非 discard 条目；判据仍是「主体+事件类型」双维度。
    """

    def setUp(self):
        sys.path.insert(0, core.SCRIPT_DIR)

    def _rec(self, cid, title, sig="high", notify=True):
        return {"change_id": cid, "title": title, "significance": sig,
                "notify": notify, "url": "https://e/%s" % cid,
                "sources": [{"name": "源" + cid, "url": "https://e/%s" % cid}]}

    def test_title_variants_require_semantic_judgment(self):
        """标题变体只有候选证据；缺少正文时不得自动断言同一事件。"""
        intel = [self._rec("c1", "DeepSeek 新模型上线，价格更低能力更强｜极客早知道"),
                 self._rec("c2", "DeepSeek V4.1 Flash 新版本：许愿式编程一键成真"),
                 self._rec("c3", "梁圣又掀桌了！DeepSeek新模型斩杀GPT-6", sig="normal")]
        out, n = judge.auto_merge_duplicates(intel)
        self.assertEqual((len(out), n), (3, 0))
        self.assertTrue(judge.find_duplicate_intel(intel), "仍须提示 AI 做语义核查")

    def test_merges_identical_content_with_all_sources(self):
        """相同标题与内容可以确定性合并，完整保留来源与从条。"""
        title = "DeepSeek V4.1 Flash 正式发布"
        intel = [self._rec("c1", title), self._rec("c2", title),
                 self._rec("c3", title, sig="normal")]
        out, n = judge.auto_merge_duplicates(intel)
        self.assertEqual((len(out), n), (1, 2))
        main = out[0]
        self.assertEqual(sorted(main["merged_change_ids"]), ["c2", "c3"])
        self.assertEqual(len(main["sources"]), 3)
        self.assertEqual(len(main["merged_titles"]), 2)
        self.assertEqual(len(main["merged_records"]), 2)

    def test_does_not_merge_different_events(self):
        """同一主体的两件不同事不许合并（Kimi 发布 vs Kimi 泄露）。"""
        intel = [self._rec("a", "Kimi 发布 K3.1 Pro，加量降价挑战 Opus 5.5"),
                 self._rec("b", "Kimi K3.1 泄露：3D 成本仅有 GPT-6 的 1/14")]
        out, n = judge.auto_merge_duplicates(intel)
        self.assertEqual(len(out), 2, "两件不同的事不许合并")
        self.assertEqual(n, 0)

    def test_ignores_non_notify(self):
        """归档条目（notify=false）不参与合并——它们不上卡，合并无收益。"""
        intel = [self._rec("a", "DeepSeek 新模型上线", notify=True),
                 self._rec("b", "DeepSeek 新模型发布，能力更强", notify=False)]
        out, n = judge.auto_merge_duplicates(intel)
        self.assertEqual(n, 0, "notify=false 不参与自动合并")

    def test_can_be_disabled(self):
        """enable=False 时退化为不合（留出口）。"""
        intel = [self._rec("a", "DeepSeek 新模型上线"),
                 self._rec("b", "DeepSeek 新模型发布")]
        out, n = judge.auto_merge_duplicates(intel, enable=False)
        self.assertEqual((len(out), n), (2, 0))

    def test_merged_ids_satisfy_coverage(self):
        """合并后覆盖度校验不能报缺口（merged_change_ids 已被认可）。"""
        intel = [self._rec("c1", "DeepSeek V4.1 Flash 正式发布"),
                 self._rec("c2", "DeepSeek V4.1 Flash 正式发布")]
        out, n = judge.auto_merge_duplicates(intel)
        self.assertEqual(n, 1, "必须真的发生合并后再验证覆盖度")
        latest = {"changes": [{"change_id": "c1"}, {"change_id": "c2"}]}
        missing = judge.coverage_check(latest, out, [])
        self.assertEqual(missing, [], "被合并的 c2 应通过 merged_change_ids 被认可")


class TestRankNoButton(unittest.TestCase):
    """榜单段不渲染「查看榜单原文」按钮（v0.6.8，2026-10-27 用户明令）。

    用户只点了这一处：「榜单监控的 查看榜单原文 按钮可以去掉」。

    为什么删了也安全：榜单记录的 `url` 与 `sources[0].url` 实测 4/4 完全一致，
    而 `_sources_line()` 本身就把媒体名渲染成可点链接 —— 跳转能力一点没丢，
    去掉的只是与正上方来源链接指向同一地址的重复按钮。

    这个测试同时是**防连带改动**的护栏：用户明令不许随意篡改模板，
    所以这里既验证"该删的删了"，也验证"其余段落一个都没动"。
    """

    def setUp(self):
        sys.path.insert(0, core.SCRIPT_DIR)

    def _rank_rec(self):
        return {"change_id": "r1", "type": "榜单新晋", "title": "BEEMO AI",
                "summary": "榜单新面孔，需核实主体", "notify": False,
                "url": "https://aicpb.com/p1",
                "sources": [{"name": "AI产品榜", "url": "https://aicpb.com/p1"}]}

    def test_no_button_in_rank_section(self):
        """榜单段：不产按钮。"""
        els = judge._rank_elements([self._rank_rec()])
        btns = [e for e in els if e.get("tag") == "button"]
        self.assertEqual(btns, [], "榜单段不该再出现按钮")

    def test_rank_source_link_preserved(self):
        """删按钮后跳转能力不能丢：来源链接必须仍在。"""
        els = judge._rank_elements([self._rank_rec()])
        blob = json.dumps(els, ensure_ascii=False)
        self.assertIn("https://aicpb.com/p1", blob, "来源行必须仍带原链接")

    def test_rank_content_complete(self):
        """标题/摘要/来源三段齐全，不能因为删按钮把内容也删了。"""
        els = judge._rank_elements([self._rank_rec()])
        self.assertEqual(len(els), 3, "应是 标题 + 摘要 + 来源 三块")
        blob = json.dumps(els, ensure_ascii=False)
        self.assertIn("BEEMO AI", blob)
        self.assertIn("榜单新面孔", blob)

    def test_other_sections_buttons_untouched(self):
        """**防连带改动**：新闻/线索段的按钮必须原样保留。"""
        news = [{"type": "技术战报", "title": "T", "summary": "S", "url": "https://n/1",
                 "notify": True, "significance": "high",
                 "sources": [{"name": "源", "url": "https://n/1"}]}]
        nbtn = [e for e in judge._news_elements(news) if e.get("tag") == "button"]
        self.assertTrue(nbtn, "新闻段「让我看看👀」按钮不许被连带删掉")
        self.assertIn("让我看看", nbtn[0]["text"]["content"])

        leads = [{"lead_id": "l1", "entity_name": "某公司", "priority": "P0", "score": 90,
                  "notify": True, "matched_groups": ["g"],
                  "logged_at": "2026-10-27T10:00:00+08:00"}]
        lbtn = [e for e in judge._lead_elements(leads, view_url="https://pool/1")
                if e.get("tag") == "button"]
        self.assertTrue(lbtn, "线索段按钮不许被连带删掉")


class TestNoBoilerplateSummary(unittest.TestCase):
    """摘要不许是套话（v0.6.9，用户实地反馈）。

    用户原话：「榜单监控的每个内容都很短，都是『xxxx。需人工核实公司主体与融资轮次后
    决定是否建联。』，而且信息 AI 不都会调研补全的吗？」

    事实：AI产品榜详情页是服务端渲染、公开可读，实测 4/4 都能取到
    榜单项/指标/月环比/名次。所以写套话不是数据不可得，是判定偷懒。
    """

    def setUp(self):
        sys.path.insert(0, core.SCRIPT_DIR)

    def test_boilerplate_detected(self):
        """典型的「需人工核实…」套话必须被检出。"""
        recs = [{"change_id": "x1", "notify": True, "title": "BEEMO AI",
                 "summary": "BEEMO AI 出现在 AI 产品榜，属榜单新面孔。"
                            "需人工核实公司主体与融资轮次后决定是否建联。"}]
        self.assertGreaterEqual(judge.warn_boilerplate(recs), 1)

    def test_real_data_summary_passes(self):
        """带真实数据的摘要不能误报。"""
        recs = [{"change_id": "x2", "notify": True, "title": "vivago.ai",
                 "summary": "vivago.ai 在 AI产品榜·出海增速榜(App) 以 4.60M App 月活跃用户 "
                            "位列第 1 名，月环比 +17.10%。出海增速榜第 1 名意味着海外用户"
                            "正在快速起量，直接命中目标客群画像。"}]
        self.assertEqual(judge.warn_boilerplate(recs), 0, "真实数据摘要不该被判套话")

    def test_short_summary_flagged(self):
        """过短（<25 字）也算信息量不足。"""
        recs = [{"change_id": "x3", "notify": True, "title": "T", "summary": "太短了"}]
        self.assertGreaterEqual(judge.warn_boilerplate(recs), 1)

    def test_archive_entries_ignored(self):
        """notify=false 的归档条目不检查（它们本就不上卡）。"""
        recs = [{"change_id": "x4", "notify": False, "title": "T", "summary": "短"}]
        self.assertEqual(judge.warn_boilerplate(recs), 0)


class TestSourceNameNoSuffix(unittest.TestCase):
    """源名称不许带「（页面监测）」这类实现细节后缀（v0.6.9，用户实地反馈）。

    这个后缀原本用于区分「RSS 订阅」和「网页监测」两种采集方式，属**实现细节**；
    但它会跟着来源行渲染到卡片上，读者看到「来源 · AI产品榜（页面监测）」毫无价值。
    区分采集方式用源的 `kind` 字段即可，不该出现在展示名里。
    """

    def test_no_monitoring_suffix_in_config(self):
        import json as _json
        p = os.path.join(core.SCRIPT_DIR, "..", "config", "sources.json.example")
        d = _json.load(open(p, encoding="utf-8"))
        bad = [s["name"] for s in d.get("sources", []) if "页面监测" in s.get("name", "")]
        self.assertEqual(bad, [], "源名称不该带『（页面监测）』后缀: %s" % bad)

    def test_source_ids_unchanged(self):
        """只改展示名，id 必须保持不变（id 是键，改了会断基线）。"""
        import json as _json
        p = os.path.join(core.SCRIPT_DIR, "..", "config", "sources.json.example")
        d = _json.load(open(p, encoding="utf-8"))
        ids = {s["id"] for s in d.get("sources", [])}
        for must in ("aicpb-rank-page", "zhidx-page", "brandark-page", "pedaily-invest-page"):
            self.assertIn(must, ids, "源 id 不许改动（它是基线键）: %s" % must)


def _mk_out(ws):
    os.makedirs(os.path.join(ws, "out"), exist_ok=True)
    return os.path.join(ws, "out", "leads-latest.json")


if __name__ == "__main__":
    unittest.main()
