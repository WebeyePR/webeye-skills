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


class TestParseFeed(unittest.TestCase):
    FEED = """<?xml version="1.0"?><rss version="2.0"><channel>
      <item><title>A</title><link>https://x.com/post/1</link><pubDate>Mon, 20 Oct 2026 08:00:00 GMT</pubDate><description>正文A</description></item>
      <item><title>B</title><link>https://x.com/same</link><guid>g-b</guid><pubDate>Tue, 21 Oct 2026 08:00:00 GMT</pubDate></item>
      <item><title>C</title><link>https://x.com/same</link><guid>g-c</guid><pubDate>Wed, 22 Oct 2026 08:00:00 GMT</pubDate></item>
    </channel></rss>"""

    def test_shared_link_uses_guid(self):
        entries = collect.parse_feed(self.FEED)
        keys = [e["key"] for e in entries]
        self.assertEqual(len(keys), len(set(keys)), "键必须唯一")
        self.assertIn("https://x.com/post/1", keys)
        self.assertIn("guid:g-b", keys)
        self.assertIn("guid:g-c", keys)

    def test_shared_link_key_stable_under_rotation(self):
        """GCP 发布说明式轮换：同一批条目顺序整体偏移，键不得变（防 phantom changed）。"""
        entries = collect.parse_feed(self.FEED)
        root = collect.parse_feed(self.FEED.replace("<title>A</title>", "<title>A2</title>"))
        # 只改标题不改链接/guid：共用链接的键仍应稳定
        keys_a = {e["key"] for e in entries if e["key"].startswith("guid:")}
        keys_b = {e["key"] for e in root if e["key"].startswith("guid:")}
        self.assertEqual(keys_a, keys_b)


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


class TestCoverage(unittest.TestCase):
    def test_missing_ids_detected(self):
        latest = {"changes": [{"change_id": "a"}, {"change_id": "b"}, {"change_id": "c"}]}
        missing = judge.coverage_check(latest, [{"change_id": "a"}], [{"change_ids": ["b"]}])
        self.assertEqual(missing, ["c"])

    def test_merge_counts_as_covered(self):
        latest = {"changes": [{"change_id": "a"}, {"change_id": "b"}]}
        missing = judge.coverage_check(latest, [{"change_id": "a", "merged_change_ids": ["b"]}], [])
        self.assertEqual(missing, [])


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
                    {"entity_name": "月之暗面", "aliases": ["Kimi"], "industries": ["AI"],
                     "event_type": "funding",
                     "event": {"date": core.now_iso()[:10], "round": "B轮", "amount_text": "3亿美元",
                               "investors": "某基金", "source_name": "量子位", "source_url": "https://q/1"},
                     "change_ids": ["chg1"], "confidence": "high"}]}
            jp = os.path.join(ws, "judged.json")
            core.jdump(jp, judged)
            rc = judge.cmd_input(ws, jp)
            self.assertEqual(rc, 0)
            # 档案与线索
            fact = facts_mod.load_fact(ws, "moonshot")
            self.assertEqual(len(fact["funding_events"]), 1)
            leads = core.read_jsonl(os.path.join(ws, "log", "leads.jsonl"))
            self.assertEqual(len(leads), 1)
            # v2 口径：月之暗面（词表 origin=CN/going_global）今日 B 轮融资 → P0 双组命中
            self.assertIn("p0-funding-recent", leads[0]["matched_groups"])
            self.assertEqual(leads[0]["priority"], "P0")
            self.assertGreaterEqual(leads[0]["score"], 60)
            self.assertTrue(leads[0]["notify"])
            # 卡片与待判核销
            self.assertEqual(len([f for f in os.listdir(os.path.join(ws, "out"))
                                  if f.startswith("card-")]) >= 2, True)
            pending = core.jload(diffcore.pending_path(ws), {"items": []})
            self.assertEqual(pending["items"], [])


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


def _mk_out(ws):
    os.makedirs(os.path.join(ws, "out"), exist_ok=True)
    return os.path.join(ws, "out", "leads-latest.json")


if __name__ == "__main__":
    unittest.main()
