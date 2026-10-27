"""潜客筛选、事件时效与保守去重的正反样本。"""
import copy
from test_reliability import WorkspaceCase
import core
import facts
import judge
import match


class TestScopeBoundaries(WorkspaceCase):
    def test_foreign_origin_cannot_be_overridden_by_city(self):
        self.assertFalse(match.ev_chinese({}, {"origin": "US", "other_events": [{"detail": "在上海参展"}]})[0])

    def test_explicit_false_overrides_keywords(self):
        self.assertFalse(match.ev_going_global({}, {"going_global": False,
                         "other_events": [{"detail": "暂不出海，仅服务国内"}]})[0])

    def test_unknown_origin_can_still_use_existing_evidence(self):
        self.assertTrue(match.ev_chinese({}, {"funding_events": [{"amount_text": "100万元人民币"}]})[0])
        self.assertTrue(match.ev_going_global({}, {"other_events": [{"detail": "海外订单增长"}]})[0])


class TestEventTime(WorkspaceCase):
    def test_funding_without_date_does_not_use_extraction_time(self):
        self.assertFalse(match.ev_funding({"within_days": 30}, {"funding_events": [
            {"round": "A轮", "extracted_at": core.now_iso()}]})[0])

    def test_future_funding_is_not_recent(self):
        self.assertFalse(match.ev_funding({"within_days": 30}, {"funding_events": [
            {"date": "2099-01-01", "round": "A轮"}]})[0])

    def test_launch_uses_event_date_and_rank_uses_first_observation(self):
        cond = {"within_days": 60}
        self.assertFalse(match.ev_other_event(cond, {"other_events": [
            {"kind": "product_launch", "date": "2020-01-01", "extracted_at": core.now_iso()}]})[0])
        self.assertFalse(match.ev_other_event(cond, {"other_events": [
            {"kind": "product_launch", "extracted_at": core.now_iso()}]})[0])
        self.assertTrue(match.ev_other_event(cond, {"other_events": [
            {"kind": "rank_new_entry", "extracted_at": core.now_iso()}]})[0])

    def test_event_date_survives_fact_storage(self):
        eid = facts.apply_fact(self.ws, {"entity_name": "甲公司", "event_type": "other",
            "event": {"kind": "product_launch", "date": "2026-10-20", "source_url": "https://example.com"}}, {}, core.now_iso())
        self.assertEqual(facts.load_fact(self.ws, eid)["other_events"][0].get("date"), "2026-10-20")


class TestSafeMerge(WorkspaceCase):
    def rec(self, cid, title, summary=""):
        return {"change_id": cid, "title": title, "summary": summary, "notify": True,
                "url": "https://example.com/" + cid}

    def test_same_company_distinct_products_not_merged(self):
        rows = [self.rec("a", "Meta 发布 Llama 新模型"), self.rec("b", "Meta 发布 Quest 新设备")]
        self.assertEqual(judge.auto_merge_duplicates(rows)[1], 0)

    def test_chinese_identical_content_merges_with_provenance(self):
        rows = [self.rec("a", "甲公司完成A轮融资", "完成融资"), self.rec("b", "甲公司完成A轮融资", "完成融资")]
        out, n = judge.auto_merge_duplicates(copy.deepcopy(rows))
        self.assertEqual(n, 1)
        self.assertEqual(out[0]["merged_records"][0]["url"], rows[1]["url"])

    def test_same_title_different_details_not_merged(self):
        rows = [self.rec("a", "Meta 发布新产品", "发布 Llama"), self.rec("b", "Meta 发布新产品", "发布 Quest")]
        self.assertEqual(judge.auto_merge_duplicates(rows)[1], 0)
