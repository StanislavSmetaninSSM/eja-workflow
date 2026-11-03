"""Current native assignments and observed report persistence, without body capture."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from test_delegation_guard import make_packet
from test_workflow_audit import Journal

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import audit_workflow as aw
import internal_audit as ia
from delegation_contract import parse_packet


class ReviewDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.artifact = str(self.root / ".work" / "review-a" / "report.md")
        self.wire = self.root / "agents" / "agent-7" / "wire.jsonl"
        self.wire.parent.mkdir(parents=True)

    def packet(self, **changes):
        return make_packet(write_roots=[str(self.root / ".work" / "review-a")],
                           result_delivery="artifact", result_artifact=self.artifact, **changes)

    def journal(self, prompt=None):
        j = Journal()
        j.rows[-1].update(agentId="agent-7", origin={"kind": "system_trigger", "name": "subagent"},
                          input=[{"type": "text", "text": prompt or self.packet()}])
        return j

    def persist(self, j, path=None, error=False, parent="write-uuid"):
        j.event({"type": "tool.call", "name": "Write", "args": {"path": path or self.artifact, "content": "PRIVATE_REPORT"},
                 "toolCallId": "report", "uuid": "write-uuid", "turnId": "0"})
        j.event({"type": "tool.result", "toolCallId": "report", "parentUuid": parent,
                 "result": {"output": "ok", "isError": error}})
        j.rows[-2]["agentId"] = j.rows[-1]["agentId"] = "agent-7"

    def findings(self, j):
        current = next(r["turnId"] for r in reversed(j.rows) if r["type"] == "turn.prompt")
        j.finish()
        j.rows[-2]["turnId"] = current
        self.wire.write_text("".join(json.dumps(r) + "\n" for r in j.rows), encoding="utf8")
        trace = aw.load_log(self.wire)
        self.assertNotIn("PRIVATE_REPORT", repr(trace))
        return {f["rule"]: f["status"] for f in aw.phase_evidence(trace)}

    def test_review_contract_requires_bounded_result_destination(self):
        parse_packet(self.packet())
        for changes in ({"result_artifact": str(self.root / "elsewhere.md")},
                        {"result_artifact": str(self.root / ".work/review-a/../other/report.md")},
                        {"result_artifact": "relative.md"}, {"result_delivery": "anything"},
                        {"result_artifact": None}, {"result_artifact": str(self.root / ".work/review-a/report.py")}):
            data = parse_packet(self.packet())
            data.update(changes)
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                parse_packet("EJA_HANDOFF_V1\n" + json.dumps(data) + "\nEND_EJA_HANDOFF\nReview.")

    def test_explicit_no_write_review_uses_inline_and_never_claims_persistence(self):
        prompt = make_packet(write_roots=[], result_delivery="inline", result_artifact=None,
                             artifact_unavailable_reason="user_forbids_writes")
        parse_packet(prompt)
        self.assertEqual(self.findings(self.journal(prompt))["review_inline_delivery"], "UNVERIFIED")
        for changes in ({"artifact_unavailable_reason": "small_task"}, {"write_roots": [str(self.root)]}):
            data = parse_packet(prompt)
            data.update(changes)
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                parse_packet("EJA_HANDOFF_V1\n" + json.dumps(data) + "\nEND_EJA_HANDOFF")

    def test_only_successful_matching_current_artifact_write_is_credited(self):
        j = self.journal()
        self.persist(j)
        self.assertEqual(self.findings(j)["review_artifact_persisted"], "PASS")
        for kwargs in ({"error": True}, {"parent": "different"}, {"parent": None},
                       {"path": str(self.root / ".work/review-b/report.md")}, {"path": "report.md"}):
            j = self.journal()
            self.persist(j, **kwargs)
            with self.subTest(kwargs=kwargs):
                self.assertEqual(self.findings(j)["review_artifact_persisted"], "FAIL")

    def test_existing_file_and_old_turn_write_are_not_current_evidence(self):
        Path(self.artifact).parent.mkdir(parents=True)
        Path(self.artifact).write_text("old report", encoding="utf8")
        self.assertEqual(self.findings(self.journal())["review_artifact_persisted"], "FAIL")
        j = self.journal()
        self.persist(j)
        j.rows.append({**j.rows[2], "turnId": 1})
        self.assertEqual(self.findings(j)["review_artifact_persisted"], "FAIL")

    def test_user_context_other_child_and_ambiguous_input_do_not_establish_assignment(self):
        for change in ({"origin": {"kind": "user"}}, {"agentId": "agent-8"},
                       {"input": [{"type": "text", "text": self.packet()}, {"type": "text", "text": "extra"}]}):
            j = self.journal()
            j.rows[2].update(change)
            self.persist(j)
            with self.subTest(change=change):
                self.assertNotIn("review_artifact_persisted", self.findings(j))

    def test_contradictory_or_missing_actor_and_turn_are_never_credited(self):
        for offset in (-2, -1):
            for actor in ("agent-8", None):
                j = self.journal()
                self.persist(j)
                j.rows[offset]["agentId"] = actor
                with self.subTest(offset=offset, actor=actor):
                    self.assertEqual(self.findings(j)["review_artifact_persisted"], "FAIL")
            j = self.journal()
            self.persist(j)
            j.rows[offset]["event"]["turnId"] = "99"
            with self.subTest(offset=offset):
                self.assertEqual(self.findings(j)["review_artifact_persisted"], "FAIL")

    def test_document_only_assigned_review_is_stop_eligible_without_report(self):
        home = self.root / "home"
        scope = home / "sessions/wd_owned/conv-owned"
        wire = scope / "agents/agent-7/wire.jsonl"
        wire.parent.mkdir(parents=True)
        j = self.journal()
        j.call("Read", {"path": str(self.root / "README.md")})
        wire.write_text("".join(json.dumps(r) + "\n" for r in j.rows), encoding="utf8")
        with patch.object(ia, "issue_ticket", return_value="e27bef74-acdb-49d0-9316-f8ee6a1c94ef"):
            response = ia.stop_hook({"session_id": "conv-owned"}, ROOT, home)
        self.assertEqual(response["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_inline_checkpoint_preserves_manual_unknown_instead_of_false_failure(self):
        j = self.journal(make_packet(write_roots=[], result_delivery="inline", result_artifact=None,
                                     artifact_unavailable_reason="user_forbids_writes"))
        for skill in (aw.ENTRY, aw.USING, aw.VERIFY):
            j.skill(skill)
        j.call("Bash", {"command": "inspect permitted input"})
        j.event({"type": "tool.call", "name": "Bash", "args": {"command": "pending audit"}, "toolCallId": "pending"})
        self.wire.write_text("".join(json.dumps(r) + "\n" for r in j.rows), encoding="utf8")
        report = ia._checkpoint(self.wire, "pending", {"start_line": 3, "turn_id": 0})
        self.assertEqual(next(f["status"] for f in report["findings"] if f["rule"] == "review_inline_delivery"), "UNVERIFIED")
        self.assertEqual(report["formal_status"], "UNVERIFIED")


if __name__ == "__main__":
    unittest.main()
