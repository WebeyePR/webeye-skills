"""发送批次确认回归；不实际发送卡片。"""
import os
from unittest.mock import patch

from test_reliability import WorkspaceCase
import core
import judge


class TestDeliveryBatch(WorkspaceCase):
    def record(self, cid, when="2026-10-27T01:00:00Z"):
        return {"change_id": cid, "title": cid, "summary": "活动摘要", "type": "活动情报",
                "url": "https://example.com/" + cid, "notify": True,
                "significance": "normal", "logged_at": when}

    def produce(self):
        with patch.object(judge, "resolve_banner_key", return_value=None):
            judge.cmd_digest(self.ws)
        return core.jload(self.ws + "/state/digest_pending_send.json", {})

    def test_new_record_after_production_not_marked_sent(self):
        for same_second in (True, False):
            with self.subTest(same_second=same_second):
                core.replace_jsonl(self.ws + "/log/intel.jsonl", [self.record("a")])
                for name in ("digest_anchor.json", "digest_pending_send.json"):
                    path = self.ws + "/state/" + name
                    if os.path.exists(path):
                        os.remove(path)
                self.produce()
                core.append_jsonl(self.ws + "/log/intel.jsonl", self.record(
                    "b", "2026-10-27T01:00:00Z" if same_second else "2026-10-27T02:00:00Z"))
                judge.cmd_digest(self.ws, commit=True)
                rows = core.read_jsonl(self.ws + "/log/intel.jsonl")
                self.assertFalse(rows[1].get("suppressed_by_guard", False))
                batch = self.produce()
                self.assertEqual(batch["record_ids"], ["b"])

    def test_missing_card_refuses_commit_and_keeps_manifest(self):
        core.append_jsonl(self.ws + "/log/intel.jsonl", self.record("a"))
        batch = self.produce()
        os.remove(batch["cards"][0])
        with self.assertRaises(SystemExit):
            judge.cmd_digest(self.ws, commit=True)
        self.assertTrue(os.path.exists(self.ws + "/state/digest_pending_send.json"))
        self.assertFalse(os.path.exists(self.ws + "/state/digest_anchor.json"))

    def test_changed_content_refuses_old_batch_commit(self):
        core.append_jsonl(self.ws + "/log/intel.jsonl", self.record("a"))
        self.produce()
        rec = self.record("a")
        rec["summary"] = "修正后的摘要"
        core.replace_jsonl(self.ws + "/log/intel.jsonl", [rec])
        with self.assertRaises(SystemExit):
            judge.cmd_digest(self.ws, commit=True)
        self.assertFalse(os.path.exists(self.ws + "/state/digest_anchor.json"))

    def test_lead_at_same_second_remains_in_next_batch(self):
        core.append_jsonl(self.ws + "/log/intel.jsonl", self.record("a"))
        self.produce()
        core.append_jsonl(self.ws + "/log/leads.jsonl", {
            "lead_id": "late", "entity_name": "甲公司", "logged_at": "2026-10-27T01:00:00Z"})
        judge.cmd_digest(self.ws, commit=True)
        batch = self.produce()
        self.assertEqual(batch["lead_ids"], ["late"])

    def test_changed_card_refuses_commit(self):
        core.append_jsonl(self.ws + "/log/intel.jsonl", self.record("a"))
        batch = self.produce()
        core.jdump(batch["cards"][0], {"modified": True})
        with self.assertRaises(SystemExit):
            judge.cmd_digest(self.ws, commit=True)

    def test_empty_repeated_commit_preserves_boundary_metadata(self):
        core.append_jsonl(self.ws + "/log/intel.jsonl", self.record("a"))
        self.produce()
        judge.cmd_digest(self.ws, commit=True)
        before = core.jload(self.ws + "/state/digest_anchor.json", {})
        judge.cmd_digest(self.ws, commit=True)
        self.assertEqual(core.jload(self.ws + "/state/digest_anchor.json", {}), before)
