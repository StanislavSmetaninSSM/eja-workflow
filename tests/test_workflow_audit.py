"""Behavioral tests against synthetic native event journals, never real user data."""

import json
import hashlib
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "tools" / "audit_workflow.py"
ENTRY = "eja-workflow:eja-workflow"
USING = "superpowers:using-superpowers"
VERIFY = "superpowers:verification-before-completion"
EXECUTE = "superpowers:executing-plans"


class Journal:
    """Build the supported native public envelope with realistic call/result ids."""

    def __init__(self):
        self.rows = [{"type": "metadata", "protocol_version": "1.5"}, {
            "type": "context.append_message",
            "message": {"origin": {"kind": "user"}, "content": []},
        }]
        self.counter = 0

    def event(self, event):
        self.rows.append({"type": "context.append_loop_event", "event": event})

    def call(self, name, args, output="ok", error=False):
        self.counter += 1
        cid = f"tool_{self.counter}"
        self.event({"type": "tool.call", "name": name, "args": args, "toolCallId": cid})
        self.event({"type": "tool.result", "toolCallId": cid,
                    "result": {"output": output, "isError": error}})

    def skill(self, name, failed=False):
        self.call("Skill", {"skill": name}, "denied" if failed else
                  f'Skill "{name}" loaded inline. Follow its instructions.', failed)

    def finish(self):
        self.rows.append({"type": "agent.turn.ended", "outcome": "done"})
        return self


class AuditTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.wire = self.directory / "agents" / "main" / "wire.jsonl"
        self.wire.parent.mkdir(parents=True)

    def run_audit(self, journal, role="reviewer", extra=()):
        self.wire.write_text("".join(json.dumps(r) + "\n" for r in journal.rows), encoding="utf-8")
        self.input_hash_before = hashlib.sha256(self.wire.read_bytes()).digest()
        result = subprocess.run([sys.executable, "-B", str(CLI), "--wire", str(self.wire),
                                 "--role", role, "--json", *extra],
                                capture_output=True, text=True, encoding="utf-8")
        self.assertIn(result.returncode, (0, 1, 2))
        self.assertTrue(result.stdout.strip().startswith("{"), result.stderr)
        return result.returncode, json.loads(result.stdout)

    def status(self, report, rule):
        found = [f["status"] for f in report["findings"] if f["rule"] == rule]
        self.assertTrue(found, f"Missing audit rule: {rule}")
        return found[0]

    def test_complete_reviewer_credits_actual_loads_but_never_proves_workflow(self):
        j = Journal()
        for skill in (ENTRY, USING, VERIFY):
            j.skill(skill)
        code, report = self.run_audit(j.finish())
        self.assertEqual(code, 0)
        self.assertEqual(report["formal_status"], "PASS")
        self.assertEqual(report["workflow_status"], "NOT_PROVEN")

    def test_missing_execution_is_detected_despite_green_self_report(self):
        j = Journal()
        j.skill(ENTRY)
        j.skill(USING)
        j.event({"type": "content.part", "part": {"type": "text", "text": "All skills used. All tests pass."}})
        j.call("Write", {"path": "port.test.mjs", "content": "secret payload"})
        code, report = self.run_audit(j.finish(), "implementation")
        self.assertEqual(code, 1)
        self.assertEqual(self.status(report, "execution_before_code"), "FAIL")

    def test_late_execution_does_not_repair_original_order(self):
        j = Journal()
        j.skill(ENTRY)
        j.skill(USING)
        j.call("Write", {"path": "port.mjs", "content": ""})
        j.skill(EXECUTE)
        _, report = self.run_audit(j.finish(), "implementation")
        self.assertEqual(self.status(report, "execution_before_code"), "FAIL")

    def test_failed_skill_is_not_successful_entry(self):
        j = Journal()
        j.skill(ENTRY, failed=True)
        j.skill(USING)
        j.skill(VERIFY)
        _, report = self.run_audit(j.finish())
        self.assertEqual(self.status(report, "entry_first"), "FAIL")

    def delegation(self, actual="eja-methodology"):
        j = Journal()
        for skill in (ENTRY, USING, "superpowers:requesting-code-review"):
            j.skill(skill)
        policy = (ROOT / "skills/eja-workflow/references/contract-review.md").read_text(encoding="utf-8")
        j.call("Read", {"path": "references/child-agents.md"})
        j.call("Read", {"path": "references/contract-review.md"}, policy)
        j.call("Agent", {"subagent_type": "eja-methodology", "prompt": f"{ENTRY} {USING}\n{policy}"},
               f"agent_id: agent-1\nactual_subagent_type: {actual}\nstatus: completed\nstop_reason: completed\n\n[summary]\nDone")
        j.skill("superpowers:receiving-code-review")
        j.call("Read", {"path": "references/contract-review.md"}, policy)
        j.skill(VERIFY)
        return j.finish()

    def child(self, own_entry=True):
        child = Journal()
        if own_entry:
            for skill in (ENTRY, USING, VERIFY):
                child.skill(skill)
        child.finish()
        path = self.wire.parent.parent / "agent-1" / "wire.jsonl"
        path.parent.mkdir(parents=True)
        path.write_text("".join(json.dumps(r) + "\n" for r in child.rows), encoding="utf-8")

    def test_child_must_load_its_own_skills(self):
        self.child(own_entry=False)
        code, report = self.run_audit(self.delegation(), "delegator")
        self.assertEqual(code, 1)
        self.assertEqual(self.status(report, "child_agent-1_entry_first"), "FAIL")

    def test_real_custom_role_and_child_entry_are_credited(self):
        self.child()
        _, report = self.run_audit(self.delegation(), "delegator")
        self.assertEqual(self.status(report, "child_agent-1_entry_first"), "PASS")
        self.assertEqual(self.status(report, "child_agent-1_custom_role"), "PASS")
        self.assertEqual(self.status(report, "review_receipt_reread"), "PASS")

    def test_requested_role_does_not_override_actual_coder(self):
        self.child()
        _, report = self.run_audit(self.delegation(actual="coder"), "delegator")
        self.assertEqual(self.status(report, "child_agent-1_custom_role"), "FAIL")

    def test_missing_child_cannot_pass(self):
        code, report = self.run_audit(self.delegation(), "delegator")
        self.assertEqual(code, 2)
        self.assertEqual(self.status(report, "child_agent-1_entry_first"), "UNVERIFIED")

    def test_empty_or_unfinished_stream_cannot_pass(self):
        for rows in ([], [{"type": "metadata"}], Journal().rows):
            j = Journal()
            j.rows = rows
            code, report = self.run_audit(j)
            self.assertEqual(code, 2)
            self.assertEqual(report["formal_status"], "UNVERIFIED")

    def test_unknown_public_envelope_cannot_silently_pass(self):
        j = Journal()
        for skill in (ENTRY, USING, VERIFY):
            j.skill(skill)
        j.rows.append({"type": "context.tool_call_v2", "secret": "NEVER_LEAK"})
        code, _ = self.run_audit(j.finish())
        self.assertEqual(code, 2)

    def test_untrusted_field_types_produce_safe_incomplete_report(self):
        j = Journal()
        j.rows.append({"type": "context.append_message", "message": {"origin": []}})
        j.event({"type": "tool.call", "name": "Skill", "toolCallId": "x", "args": {"skill": []}})
        code, report = self.run_audit(j.finish())
        self.assertEqual(code, 2)
        self.assertNotIn("Traceback", json.dumps(report))

    def test_reports_exclude_hidden_profiles_prompts_results_and_source_content(self):
        j = Journal()
        j.rows.append({"type": "profile.bind", "systemPrompt": "PROFILE_SECRET"})
        j.event({"type": "content.part", "part": {"type": "reasoning", "text": "REASON_SECRET"}})
        for skill in (ENTRY, USING, VERIFY):
            j.skill(skill)
        j.call("Read", {"path": "PRIVATE_PATH_SECRET"}, "RESULT_SECRET")
        j.event({"type": "content.part", "part": {"type": "text", "text": "PROMPT_SECRET"}})
        _, report = self.run_audit(j.finish())
        self.assertNotIn("SECRET", json.dumps(report))

    def test_receipt_reread_after_correction_is_late(self):
        self.child()
        j = self.delegation()
        insertion = next(i for i, r in enumerate(j.rows) if
                         r.get("event", {}).get("name") == "Skill" and
                         r["event"]["args"].get("skill") == "superpowers:receiving-code-review")
        j.rows.insert(insertion, {"type": "context.append_loop_event", "event": {
            "type": "tool.call", "name": "Edit", "toolCallId": "correction",
            "args": {"path": "port.test.mjs"}}})
        j.rows.insert(insertion + 1, {"type": "context.append_loop_event", "event": {
            "type": "tool.result", "toolCallId": "correction", "result": {"output": "ok"}}})
        _, report = self.run_audit(j, "delegator")
        self.assertEqual(self.status(report, "review_receipt_reread"), "FAIL")

    def test_output_is_explicit_outside_journal_and_input_bytes_preserved(self):
        j = Journal()
        for skill in (ENTRY, USING, VERIFY):
            j.skill(skill)
        with tempfile.TemporaryDirectory() as other:
            output = Path(other) / "report.json"
            code, report = self.run_audit(j.finish(), extra=("--output", str(output)))
            self.assertEqual(code, 0)
            self.assertEqual(json.loads(output.read_text(encoding="utf-8")), report)
            self.assertEqual(hashlib.sha256(self.wire.read_bytes()).digest(), self.input_hash_before)

    def test_output_refuses_existing_input_or_journal_tree(self):
        for output in (self.wire, self.wire.parent / "new-report.json"):
            j = Journal()
            for skill in (ENTRY, USING, VERIFY):
                j.skill(skill)
            code, report = self.run_audit(j.finish(), extra=("--output", str(output)))
            self.assertEqual(code, 2)
            self.assertEqual(report["formal_status"], "UNVERIFIED")
            self.assertTrue(self.wire.read_text(encoding="utf-8").startswith('{"type": "metadata"'))
            if output != self.wire:
                self.assertFalse(output.exists())

    def test_output_refuses_existing_unrelated_file(self):
        with tempfile.TemporaryDirectory() as other:
            output = Path(other) / "report.json"
            output.write_text("keep-me", encoding="utf-8")
            code, _ = self.run_audit(Journal().finish(), extra=("--output", str(output)))
            self.assertEqual(code, 2)
            self.assertEqual(output.read_text(encoding="utf-8"), "keep-me")

    def test_malformed_or_truncated_record_produces_incomplete_report(self):
        j = Journal().finish()
        self.run_audit(j)
        with self.wire.open("ab") as stream:
            stream.write(b'{"type": "context.append_loop_event"')
        result = subprocess.run([sys.executable, "-B", str(CLI), "--wire", str(self.wire),
                                 "--role", "reviewer", "--json"], capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(json.loads(result.stdout)["formal_status"], "UNVERIFIED")

    def test_default_output_is_human_readable(self):
        self.run_audit(Journal().finish())
        result = subprocess.run([sys.executable, "-B", str(CLI), "--wire", str(self.wire),
                                 "--role", "reviewer"], capture_output=True, text=True)
        self.assertIn("Workflow: NOT_PROVEN", result.stdout)
        self.assertIn("Formal:", result.stdout)

    def test_native_child_trigger_is_start_evidence_not_human_approval(self):
        j = Journal()
        j.rows[1]["message"]["origin"]["kind"] = "system_trigger"
        code, report = self.run_audit(j.finish())
        self.assertEqual(code, 1)
        self.assertEqual(self.status(report, "entry_first"), "FAIL")
        self.assertEqual(report["workflow_status"], "NOT_PROVEN")

    def test_unknown_or_missing_protocol_cannot_pass(self):
        for protocol in (None, "future-version"):
            j = Journal()
            j.rows[0]["protocol_version"] = protocol
            for skill in (ENTRY, USING, VERIFY):
                j.skill(skill)
            code, report = self.run_audit(j.finish())
            self.assertEqual(code, 2)
            self.assertEqual(report["formal_status"], "UNVERIFIED")

    def test_outer_result_error_prevents_successful_skill_credit(self):
        j = Journal()
        for skill in (ENTRY, USING, VERIFY):
            j.skill(skill)
        j.rows[3]["event"]["isError"] = True
        _, report = self.run_audit(j.finish())
        self.assertEqual(self.status(report, "entry_first"), "FAIL")

    def test_phase_skills_out_of_order_are_detected(self):
        j = Journal()
        for skill in (ENTRY, USING, "speckit-tasks", "speckit-plan", "speckit-analyze"):
            j.skill(skill)
        _, report = self.run_audit(j.finish(), "implementation")
        self.assertEqual(self.status(report, "planning_skill_order"), "FAIL")

    def test_unknown_reviewer_selector_is_not_silently_accepted(self):
        self.child()
        j = self.delegation()
        for skill in ("spec-kit-superpowers-bridge", "speckit-specify", "superpowers:writing-plans",
                      "speckit-plan", "speckit-tasks", "speckit-analyze", "speckit-implement",
                      EXECUTE, "superpowers:test-driven-development", "superpowers:finishing-a-development-branch"):
            j.skill(skill)
        _, report = self.run_audit(j.finish(), "implementation", ("--review-agent", "agent-999"))
        self.assertEqual(self.status(report, "review_agent_selection"), "UNVERIFIED")

    def test_exported_log_does_not_authorize_adjacent_child_discovery(self):
        self.wire = self.directory / "exports" / "wire.jsonl"
        self.wire.parent.mkdir()
        self.child()
        _, report = self.run_audit(self.delegation(), "delegator")
        self.assertEqual(self.status(report, "child_agent-1_entry_first"), "UNVERIFIED")

    def test_unrecognized_skill_success_format_is_unverified(self):
        j = Journal()
        for skill in (ENTRY, USING, VERIFY):
            j.skill(skill)
        j.rows[3]["event"]["result"]["output"] = "New native success format"
        code, report = self.run_audit(j.finish())
        self.assertEqual(code, 2)
        self.assertEqual(self.status(report, "entry_first"), "UNVERIFIED")

    def test_duplicate_call_identity_is_incomplete_evidence(self):
        j = Journal()
        for skill in (ENTRY, USING, VERIFY):
            j.skill(skill)
        j.rows.insert(4, dict(j.rows[2]))
        code, _ = self.run_audit(j.finish())
        self.assertEqual(code, 2)

    def test_oversized_record_never_becomes_clean_success(self):
        j = Journal()
        for skill in (ENTRY, USING, VERIFY):
            j.skill(skill)
        j.rows.append({"type": "profile.bind", "systemPrompt": "x" * (8 * 1024 * 1024)})
        code, report = self.run_audit(j.finish())
        self.assertEqual(code, 2)
        self.assertEqual(self.status(report, "evidence_complete"), "UNVERIFIED")

    def complete_reviewer(self):
        j = Journal()
        for skill in (ENTRY, USING, VERIFY):
            j.skill(skill)
        return j.finish()

    def test_review_unknown_inner_event_cannot_pass(self):
        j = self.complete_reviewer()
        j.rows.insert(-1, {"type": "context.append_loop_event", "event": {"type": "tool.call.v2"}})
        code, _ = self.run_audit(j)
        self.assertEqual(code, 2)

    def test_review_unhashable_unrelated_skill_field_is_safe(self):
        for field in ([], {}):
            j = self.complete_reviewer()
            j.call("Read", {"path": "foo", "skill": field})
            code, report = self.run_audit(j.finish())
            self.assertEqual(code, 2)
            self.assertEqual(report["formal_status"], "UNVERIFIED")

    def test_review_malformed_write_path_cannot_hide_a_write(self):
        j = self.complete_reviewer()
        j.call("Write", {"path": []})
        code, report = self.run_audit(j.finish())
        self.assertEqual(code, 2)
        self.assertEqual(self.status(report, "evidence_complete"), "UNVERIFIED")

    def test_review_new_unfinished_turn_resets_completion(self):
        j = self.complete_reviewer()
        j.rows.append({"type": "agent.turn.started"})
        code, report = self.run_audit(j)
        self.assertEqual(code, 2)
        self.assertEqual(self.status(report, "evidence_complete"), "UNVERIFIED")

    def test_review_successful_reload_does_not_repair_failed_first_entry(self):
        j = Journal()
        j.skill(ENTRY, failed=True)
        j.call("Read", {"path": "source.mjs"})
        for skill in (ENTRY, USING, VERIFY):
            j.skill(skill)
        code, report = self.run_audit(j.finish())
        self.assertEqual(code, 1)
        self.assertEqual(self.status(report, "entry_first"), "FAIL")

    def test_review_first_entry_must_finish_before_next_tool(self):
        j = self.complete_reviewer()
        result = j.rows.pop(3)
        j.rows.insert(4, result)
        _, report = self.run_audit(j)
        self.assertEqual(self.status(report, "entry_first"), "FAIL")

    @unittest.skipUnless(sys.platform == "win32", "Windows alternate data stream regression")
    def test_review_ads_through_hardlink_cannot_mutate_input(self):
        j = self.complete_reviewer()
        self.run_audit(j)
        with tempfile.TemporaryDirectory() as other:
            alias = Path(other) / "external-hardlink.jsonl"
            os.link(self.wire, alias)
            code, report = self.run_audit(j, extra=("--output", str(alias) + ":audit-report"))
            self.assertEqual(code, 2)
            self.assertEqual(report["formal_status"], "UNVERIFIED")
            self.assertEqual(hashlib.sha256(self.wire.read_bytes()).digest(), self.input_hash_before)
            with self.assertRaises(FileNotFoundError):
                Path(str(self.wire) + ":audit-report").read_bytes()


if __name__ == "__main__":
    unittest.main()
