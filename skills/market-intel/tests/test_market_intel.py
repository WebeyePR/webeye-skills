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


if __name__ == "__main__":
    unittest.main()
