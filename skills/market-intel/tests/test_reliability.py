"""故障恢复与跨轮判定回归；临时工作区运行，不访问真实业务接口。"""
import contextlib
import io
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "scripts")))
import core
import diffcore
import pipeline
import judge
import bitable


class WorkspaceCase(unittest.TestCase):
    """隔离数据与输出，避免测试污染实际工作区。"""
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.ws = core.workspace(self.tmp.name)
        self.output = io.StringIO()
        self.enterContext(contextlib.redirect_stdout(self.output))
        self.enterContext(contextlib.redirect_stderr(self.output))


class TestRecovery(WorkspaceCase):
    def round(self, entries):
        source = {"id": "s", "kind": "rss", "url": "https://example.com", "windowed": True}
        with patch.object(pipeline, "load_sources", return_value=([source], {})), \
             patch.object(pipeline.collect, "run_collection", return_value=[(True, entries, None)]):
            pipeline.run_round(self.ws)
        return core.jload(self.ws + "/out/latest.json", {})

    def test_collect_without_judge_survives_next_round(self):
        old = {"key": "old", "link": "https://example.com/old"}
        new = {"key": "new", "link": "https://example.com/new", "title": "新增"}
        diffcore.save_snapshot(self.ws, "s", [old], core.now_iso())
        first = self.round([old, new])
        second = self.round([old, new])
        self.assertEqual([c["change_id"] for c in second["changes"]],
                         [c["change_id"] for c in first["changes"]])
        self.assertEqual(second["summary"]["pending_carried"], 1)
        self.assertEqual(second["summary"]["needs_judgment"], 1)
        self.assertEqual(second["changes"][0]["source_kind"], "rss")

    def test_pending_saved_before_snapshot_failure(self):
        old = {"key": "old"}
        diffcore.save_snapshot(self.ws, "s", [old], core.now_iso())
        with patch.object(diffcore, "save_snapshot", side_effect=OSError("模拟写盘失败")):
            with self.assertRaises(OSError):
                self.round([old, {"key": "new", "title": "新增"}])
        pending = core.jload(diffcore.pending_path(self.ws), {})["items"]
        self.assertEqual(len(pending), 1)

    def test_pending_keeps_enrichment_and_original_age(self):
        change = {"change_id": "x", "source_kind": "web_page", "first_seen": core.now_iso(),
                  "activity": {"name": "活动"}, "summary": "富化正文"}
        diffcore.save_pending(self.ws, [change], set(), core.now_iso())
        loaded, _ = diffcore.load_and_merge_pending(self.ws, [], core.now_iso())
        self.assertEqual(loaded[0]["source_kind"], "web_page")
        self.assertEqual(loaded[0]["activity"], change["activity"])
        self.assertEqual(loaded[0]["first_seen"], change["first_seen"])

    def test_corrupt_queue_does_not_advance_snapshot(self):
        old = {"key": "old"}
        diffcore.save_snapshot(self.ws, "s", [old], core.now_iso())
        with open(diffcore.pending_path(self.ws), "w", encoding="utf-8") as fh:
            fh.write("{损坏队列")
        with self.assertRaises(ValueError):
            self.round([old, {"key": "new"}])
        self.assertEqual(set(diffcore.load_snapshot(self.ws, "s")), {"old"})


class TestRejudgment(WorkspaceCase):
    def write(self, summary="摘要", **extra):
        core.jdump(self.ws + "/out/latest.json", {"changes": [{"change_id": "x"}]})
        rec = {"change_id": "x", "title": "活动", "summary": summary,
               "url": "https://example.com", "type": "活动情报", "notify": True,
               "significance": "normal", **extra}
        core.jdump(self.ws + "/judged.json", {"intel": [rec]})
        judge.cmd_input(self.ws, self.ws + "/judged.json")

    def test_correction_updates_digest_source_and_preserves_age(self):
        self.write("错误摘要")
        before = core.read_jsonl(self.ws + "/log/intel.jsonl")[0]
        self.write("更正摘要")
        after = core.read_jsonl(self.ws + "/log/intel.jsonl")[0]
        self.assertEqual(after["summary"], "更正摘要")
        self.assertEqual(after["logged_at"], before["logged_at"])
        revisions = core.read_jsonl(self.ws + "/log/intel-revisions.jsonl")
        self.assertEqual(revisions[-1]["previous"]["summary"], "错误摘要")

    def test_discard_removes_active_record_but_keeps_history(self):
        self.write()
        core.jdump(self.ws + "/judged.json", {"intel": [
            {"change_id": "x", "discard": True, "discard_reason": "不相关"}]})
        judge.cmd_input(self.ws, self.ws + "/judged.json")
        self.assertEqual(core.read_jsonl(self.ws + "/log/intel.jsonl"), [])
        self.assertTrue(core.read_jsonl(self.ws + "/log/intel-revisions.jsonl"))
        self.assertEqual(core.read_jsonl(self.ws + "/log/discarded.jsonl")[0]["reason"], "不相关")

    def test_sent_record_is_not_reactivated(self):
        self.write(suppressed_by_guard=True, notify=False)
        self.write("修正文案", notify=True)
        rec = core.read_jsonl(self.ws + "/log/intel.jsonl")[0]
        self.assertEqual(rec["summary"], "修正文案")
        self.assertTrue(rec["suppressed_by_guard"])
        self.assertFalse(rec["notify"])

    def test_corrupt_existing_log_refuses_rewrite(self):
        self.write()
        path = self.ws + "/log/intel.jsonl"
        with open(path, "a", encoding="utf-8") as fh:
            fh.write("{损坏的记录\n")
        with open(path, encoding="utf-8") as fh:
            before = fh.read()
        with self.assertRaises(ValueError):
            self.write("修正文案")
        with open(path, encoding="utf-8") as fh:
            self.assertEqual(fh.read(), before, "不能悄悄丢弃损坏行后重写")

    def test_sent_discarded_then_restored_never_reactivates(self):
        self.write(suppressed_by_guard=True, notify=False)
        core.jdump(self.ws + "/judged.json", {"intel": [
            {"change_id": "x", "discard": True, "discard_reason": "撤回"}]})
        judge.cmd_input(self.ws, self.ws + "/judged.json")
        self.write("核实后恢复", notify=True)
        rec = core.read_jsonl(self.ws + "/log/intel.jsonl")[0]
        self.assertTrue(rec.get("suppressed_by_guard"))
        self.assertFalse(rec["notify"])


class TestPoolSearch(WorkspaceCase):
    def test_search_failure_never_creates(self):
        core.jdump(bitable.pending_path(self.ws), {"records": [
            {"lead_id": "l", "company": "甲公司", "research_gaps": [], "record": {"公司名称": "甲公司"}}]})
        calls = []
        def cli(args, cfg):
            calls.append(args[0])
            if args[0] == "+field-list":
                return {"ok": True, "data": {"fields": []}}
            return {"ok": False, "error": "模拟接口失败"}
        with patch.object(bitable, "_cli", side_effect=cli):
            result = bitable.commit(self.ws, {"enabled": True})
        self.assertNotIn("+record-batch-create", calls)
        self.assertEqual(result, (0, 0, 1, 0))

    def test_search_can_find_exact_company_on_second_page(self):
        pages = [{"ok": True, "data": {"data": [["其他"]] * 20, "record_id_list": ["a"] * 20}},
                 {"ok": True, "data": {"data": [["甲公司"]], "record_id_list": ["found"]}}]
        with patch.object(bitable, "_cli", side_effect=pages) as cli:
            self.assertEqual(bitable._search_company("甲公司", {}), "found")
        self.assertIn("--offset", cli.call_args_list[1].args[0])


if __name__ == "__main__":
    unittest.main()
