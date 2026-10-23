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
                       json.loads(open(os.path.join(core.SCRIPT_DIR, "..", "config",
                                                    "rules.json.example"), encoding="utf-8").read()))
            core.jdump(os.path.join(ws, "entities.json"),
                       json.loads(open(os.path.join(core.SCRIPT_DIR, "..", "config",
                                                    "entities.json.example"), encoding="utf-8").read()))
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
            self.assertIn("major-funding", leads[0]["matched_groups"])
            self.assertGreaterEqual(leads[0]["score"], 60)
            # 卡片与待判核销
            self.assertEqual(len([f for f in os.listdir(os.path.join(ws, "out"))
                                  if f.startswith("card-")]) >= 2, True)
            pending = core.jload(diffcore.pending_path(ws), {"items": []})
            self.assertEqual(pending["items"], [])


if __name__ == "__main__":
    unittest.main()
