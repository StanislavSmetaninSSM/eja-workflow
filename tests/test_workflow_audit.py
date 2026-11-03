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
        self.rows.append({"type": "turn.prompt", "turnId": 0})
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

    def symbol(self, path="port.mjs"):
        """Supply a real-shaped successful symbol result for isolated phase tests."""
        self.call("mcp__plugin-serena_serena__find_symbol", {"name_path_pattern": "example", "relative_path": path},
                  json.dumps([{"name_path": "example", "kind": "Function", "relative_path": path,
                               "body_location": {"start_line": 0, "end_line": 1}}]))

    def finish(self):
        self.rows.append({"type": "turn.ended", "turnId": 0, "reason": "completed"})
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

    def test_batched_verification_cannot_credit_an_earlier_check(self):
        j = Journal()
        j.skill(ENTRY)
        j.skill(USING)
        j.event({"type": "tool.call", "name": "Skill", "args": {"skill": VERIFY}, "toolCallId": "verify"})
        j.call("Bash", {"command": "node --test"})
        j.event({"type": "tool.result", "toolCallId": "verify", "result": {
            "output": f'Skill "{VERIFY}" loaded inline. Follow its instructions.'}})
        _, report = self.run_audit(j.finish())
        self.assertEqual(self.status(report, "verification_before_check"), "FAIL")

    def test_instruction_result_then_fresh_check_is_credited(self):
        j = Journal()
        for skill in (ENTRY, USING, VERIFY):
            j.skill(skill)
        j.call("Bash", {"command": "node --test"})
        _, report = self.run_audit(j.finish())
        self.assertEqual(self.status(report, "verification_before_check"), "PASS")

    def test_checkpoint_instructions_must_arrive_before_dependent_command(self):
        j = Journal()
        for skill in (ENTRY, USING, VERIFY):
            j.skill(skill)
        checkpoint = "eja-workflow:workflow-checkpoint"
        j.event({"type": "tool.call", "name": "Skill", "args": {"skill": checkpoint}, "toolCallId": "phase"})
        j.call("Bash", {"command": "node --test"})
        j.event({"type": "tool.result", "toolCallId": "phase", "result": {
            "output": f'Skill "{checkpoint}" loaded inline. Follow its instructions.'}})
        _, report = self.run_audit(j.finish())
        self.assertEqual(self.status(report, "phase_instruction_order"), "FAIL")

    def test_independent_phase_loads_may_finish_before_work(self):
        j = Journal()
        for skill in (ENTRY, USING, VERIFY):
            j.skill(skill)
        phases = {"phase-a": "eja-workflow:workflow-checkpoint", "phase-b": EXECUTE}
        for cid, skill in phases.items():
            j.event({"type": "tool.call", "name": "Skill", "args": {"skill": skill}, "toolCallId": cid})
        for cid, skill in phases.items():
            j.event({"type": "tool.result", "toolCallId": cid, "result": {
                "output": f'Skill "{skill}" loaded inline. Follow its instructions.'}})
        j.call("Bash", {"command": "node --test"})
        _, report = self.run_audit(j.finish())
        self.assertEqual(self.status(report, "phase_instruction_order"), "PASS")

    def test_debug_brainstorm_and_spec_clarification_cannot_race_dependent_work(self):
        for phase in ("superpowers:systematic-debugging", "superpowers:brainstorming",
                      "speckit-clarify", "superpowers:using-git-worktrees"):
            with self.subTest(phase=phase):
                j = Journal()
                for skill in (ENTRY, USING, VERIFY):
                    j.skill(skill)
                j.event({"type": "tool.call", "name": "Skill", "args": {"skill": phase}, "toolCallId": "phase"})
                j.call("Bash", {"command": "node --test"})
                j.event({"type": "tool.result", "toolCallId": "phase", "result": {
                    "output": f'Skill "{phase}" loaded inline. Follow its instructions.'}})
                _, report = self.run_audit(j.finish())
                self.assertEqual(self.status(report, "phase_instruction_order"), "FAIL")

    def test_check_before_parallel_write_finishes_is_not_fresh(self):
        j = Journal()
        for skill in (ENTRY, USING, VERIFY):
            j.skill(skill)
        j.event({"type": "tool.call", "name": "Write", "args": {"path": "module.mjs"}, "toolCallId": "write"})
        j.call("Bash", {"command": "node --test"})
        j.event({"type": "tool.result", "toolCallId": "write", "result": {"output": "ok"}})
        _, report = self.run_audit(j.finish())
        self.assertEqual(self.status(report, "verification_before_check"), "FAIL")

    def test_code_review_requires_real_symbolic_access_not_health_or_activation(self):
        j = Journal()
        for skill in (ENTRY, USING, VERIFY):
            j.skill(skill)
        j.call("Read", {"path": "module.mjs"})
        j.call("mcp__plugin-serena_serena__activate_project", {"project": "C:/owned"}, "Project activated")
        j.call("Bash", {"command": "node --test"})
        _, report = self.run_audit(j.finish())
        self.assertEqual(self.status(report, "serena_symbolic_access"), "FAIL")

    def test_nonempty_matching_native_symbol_result_is_credited_without_body_leak(self):
        j = Journal()
        for skill in (ENTRY, USING, VERIFY):
            j.skill(skill)
        j.call("Read", {"path": "module.mjs"})
        symbols = [{"name_path": "example", "kind": "Function", "relative_path": "module.mjs",
                    "body_location": {"start_line": 0, "end_line": 4}, "body": "PRIVATE_SYMBOL_BODY"}]
        j.call("mcp__plugin-serena_serena__find_symbol", {
            "name_path_pattern": "example", "relative_path": "module.mjs"},
            json.dumps(symbols) + '\n<mcp-result-extras>\n{"structuredContent":{}}\n</mcp-result-extras>')
        j.call("Bash", {"command": "node --test"})
        _, report = self.run_audit(j.finish())
        self.assertEqual(self.status(report, "serena_symbolic_access"), "PASS")
        self.assertNotIn("PRIVATE_SYMBOL_BODY", json.dumps(report))

    def test_empty_failed_foreign_or_fake_symbol_results_do_not_grant_credit(self):
        symbols = [{"name_path": "example", "kind": "Function", "relative_path": "other.mjs",
                    "body_location": {"start_line": 0, "end_line": 4}}]
        cases = [("mcp__plugin-serena_serena__find_symbol", "[]", False),
                 ("mcp__plugin-serena_serena__find_symbol", json.dumps(symbols), False),
                 ("mcp__plugin-serena_serena__find_symbol", "not JSON", False),
                 ("fake_serena_find_symbol", json.dumps(symbols), False),
                 ("mcp__plugin-serena_serena__find_symbol", json.dumps([{**symbols[0], "relative_path": "module.mjs"}]), True)]
        for name, output, error in cases:
            with self.subTest(name=name, output=output, error=error):
                j = Journal()
                for skill in (ENTRY, USING, VERIFY):
                    j.skill(skill)
                j.call("Read", {"path": "module.mjs"})
                j.call(name, {"relative_path": "module.mjs", "name_path_pattern": "example"}, output, error)
                j.call("Bash", {"command": "node --test"})
                _, report = self.run_audit(j.finish())
                self.assertEqual(self.status(report, "serena_symbolic_access"), "FAIL")

    def test_valid_mcp_health_metadata_preserves_phase_evidence(self):
        for transport in ("stdio", "http", "sse"):
            for resolution in ("recovered", "needs-auth", "disabled", "removed"):
                for error in (None, "PRIVATE_MCP_ERROR"):
                    with self.subTest(transport=transport, resolution=resolution, error=error):
                        failed = {"type": "mcp.server_failed", "agentId": "main",
                                  "serverName": "owned", "transport": transport, "time": 1700000000000}
                        if error is not None:
                            failed["error"] = error
                        resolved = {"type": "mcp.server_failure_resolved", "agentId": "main",
                                    "serverName": "owned", "transport": transport,
                                    "resolution": resolution, "time": 1700000000001}
                        j = self.complete_reviewer()
                        j.rows[3:3] = [failed, resolved]
                        code, report = self.run_audit(j)
                        self.assertEqual(code, 0)
                        self.assertEqual(report["formal_status"], "PASS")
                        self.assertEqual(report["workflow_status"], "NOT_PROVEN")
                        self.assertNotIn("PRIVATE_MCP_ERROR", json.dumps(report))

    def test_mcp_health_cannot_supply_verification_or_completion(self):
        event = {"type": "mcp.server_failure_resolved", "agentId": "main",
                 "serverName": "owned", "transport": "http", "resolution": "recovered",
                 "time": 1700000000000}
        j = Journal()
        for skill in (ENTRY, USING):
            j.skill(skill)
        j.rows.append(dict(event))
        code, report = self.run_audit(j.finish())
        self.assertEqual(code, 1)
        self.assertEqual(self.status(report, "verification"), "FAIL")
        j = Journal()
        for skill in (ENTRY, USING, VERIFY):
            j.skill(skill)
        j.rows.append(dict(event))
        code, report = self.run_audit(j)
        self.assertEqual(code, 2)
        self.assertEqual(report["formal_status"], "UNVERIFIED")
        j = self.complete_reviewer()
        j.rows.append(dict(event))
        self.assertEqual(self.run_audit(j)[0], 0)

    def test_malformed_mcp_health_records_remain_unverified(self):
        failed = {"type": "mcp.server_failed", "agentId": "main", "serverName": "owned",
                  "transport": "stdio", "time": 1700000000000}
        resolved = {**failed, "type": "mcp.server_failure_resolved", "resolution": "recovered"}
        variants = []
        for base in (failed, resolved):
            for key in base:
                value = dict(base)
                value.pop(key)
                variants.append(value)
            for key, invalid in (("agentId", 1), ("serverName", None), ("transport", "ftp"),
                                 ("time", True), ("time", None), ("time", "1"),
                                 ("time", -1), ("time", float("nan")), ("time", float("inf")),
                                 ("time", 10 ** 400), ("origin", {"kind": "user"}),
                                 ("turnId", 1), ("content", [])):
                variants.append({**base, key: invalid})
        variants.extend([{**failed, "error": {"payload": "PRIVATE_MCP_ERROR"}},
                         {**failed, "resolution": "recovered"},
                         {**resolved, "resolution": "healthy"},
                         {**resolved, "error": "PRIVATE_MCP_ERROR"}])
        for index, event in enumerate(variants):
            with self.subTest(index=index):
                j = self.complete_reviewer()
                j.rows.insert(3, event)
                code, report = self.run_audit(j)
                self.assertEqual(code, 2)
                self.assertEqual(report["formal_status"], "UNVERIFIED")
                self.assertNotIn("PRIVATE_MCP_ERROR", json.dumps(report))

    def test_complete_reviewer_credits_actual_loads_but_never_proves_workflow(self):
        j = Journal()
        for skill in (ENTRY, USING, VERIFY):
            j.skill(skill)
        code, report = self.run_audit(j.finish())
        self.assertEqual(code, 0)
        self.assertEqual(report["formal_status"], "PASS")
        self.assertEqual(report["workflow_status"], "NOT_PROVEN")

    def test_actor_end_before_native_stop_is_not_kernel_completion(self):
        j = Journal()
        for skill in (ENTRY, USING, VERIFY):
            j.skill(skill)
        j.rows.append({"type": "agent.turn.ended", "turnId": 0, "outcome": "done"})
        code, report = self.run_audit(j)
        self.assertEqual(code, 2)
        self.assertEqual(report["formal_status"], "UNVERIFIED")
        j.rows.append({"type": "turn.ended", "turnId": 0, "reason": "completed"})
        self.assertEqual(self.run_audit(j)[0], 0)

    def test_new_kernel_prompt_cannot_reuse_previous_completion(self):
        j = self.complete_reviewer()
        j.rows.extend([{"type": "turn.prompt", "turnId": 1},
                       {"type": "agent.turn.ended", "turnId": 1, "outcome": "done"}])
        self.assertEqual(self.run_audit(j)[0], 2)
        j.rows.append({"type": "turn.ended", "turnId": 0, "reason": "completed"})
        self.assertEqual(self.run_audit(j)[0], 2)

    def test_context_only_new_work_cannot_accept_a_stale_kernel_end(self):
        j = self.complete_reviewer()
        j.rows.extend([{"type": "context.append_message", "message": {"origin": {"kind": "user"}, "content": []}},
                       {"type": "agent.turn.started", "turnId": 1}])
        j.call("Read", {"path": "source.mjs"})
        j.rows.extend([{"type": "agent.turn.ended", "turnId": 1, "outcome": "done"},
                       {"type": "turn.ended", "turnId": 0, "reason": "completed"}])
        self.assertEqual(self.run_audit(j)[0], 2)

    def test_new_actor_without_new_kernel_completion_is_unverified(self):
        j = self.complete_reviewer()
        j.rows.extend([{"type": "agent.turn.started", "turnId": 1},
                       {"type": "agent.turn.ended", "turnId": 1, "outcome": "done"}])
        self.assertEqual(self.run_audit(j)[0], 2)
        j.rows.append({"type": "turn.ended", "turnId": 0, "reason": "completed"})
        self.assertEqual(self.run_audit(j)[0], 2)

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
        from test_delegation_guard import make_packet
        j.call("Agent", {"subagent_type": "eja-methodology", "prompt": make_packet()},
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

    def test_json_packet_preserves_full_review_protocol_and_serena_row(self):
        self.child()
        _, report = self.run_audit(self.delegation(), "delegator")
        self.assertEqual(self.status(report, "review_dispatch_audit"), "PASS")
        self.assertEqual(self.status(report, "child_agent-1_serena_packet"), "PASS")

    def test_legacy_packet_does_not_invent_missing_serena_state(self):
        self.child()
        j = self.delegation()
        for row in j.rows:
            event = row.get("event", {})
            if event.get("type") == "tool.call" and event.get("name") == "Agent":
                policy = (ROOT / "skills/eja-workflow/references/contract-review.md").read_text(encoding="utf8")
                event["args"]["prompt"] = f"{ENTRY} {USING}\n{policy}"
        _, report = self.run_audit(j, "delegator")
        self.assertEqual(self.status(report, "child_agent-1_serena_packet"), "FAIL")

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
        next(r["event"] for r in j.rows if r.get("event", {}).get("type") == "tool.result")["isError"] = True
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
        next(r["event"] for r in j.rows if r.get("event", {}).get("type") == "tool.result")["result"]["output"] = "New native success format"
        code, report = self.run_audit(j.finish())
        self.assertEqual(code, 2)
        self.assertEqual(self.status(report, "entry_first"), "UNVERIFIED")

    def test_duplicate_call_identity_is_incomplete_evidence(self):
        j = Journal()
        for skill in (ENTRY, USING, VERIFY):
            j.skill(skill)
        call = next(r for r in j.rows if r.get("event", {}).get("type") == "tool.call")
        j.rows.insert(4, dict(call))
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
        result_index = next(i for i, r in enumerate(j.rows) if r.get("event", {}).get("type") == "tool.result")
        result = j.rows.pop(result_index)
        second_call = next(i for i, r in enumerate(j.rows) if r.get("event", {}).get("type") == "tool.call" and r["event"]["toolCallId"] == "tool_2")
        j.rows.insert(second_call + 1, result)
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
