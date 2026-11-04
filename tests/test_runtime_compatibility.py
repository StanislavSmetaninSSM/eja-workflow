"""Runtime evolution may reduce audit coverage, never invent success or repairs."""
from contextlib import redirect_stderr
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest

from test_delegation_guard import make_packet
from test_workflow_audit import Journal, ENTRY, USING, VERIFY

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import audit_workflow as aw
import delegation_guard as guard
import internal_audit as ia


class RuntimeCompatibilityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.plugin = self.root / "plugin"
        self.plugin.mkdir()
        (self.plugin / "kimi.plugin.json").write_text('{"version":"0.12.0"}', encoding="utf8")
        self.home = self.root / "home"
        self.wire = self.home / "sessions/wd_owned/conv-owned/agents/main/wire.jsonl"
        self.wire.parent.mkdir(parents=True)
        self.payload = {"hook_event_name": "Stop", "session_id": "conv-owned", "cwd": str(self.root)}

    def write(self, j):
        self.wire.write_text("".join(json.dumps(r) + "\n" for r in j.rows), encoding="utf8")

    def report(self, j, role="reviewer"):
        self.write(j)
        trace = aw.load_log(self.wire)
        report = aw.audit(trace, role)
        self.assertNotIn("PRIVATE_FUTURE", repr(trace) + json.dumps(report))
        return report

    def changed(self, kind):
        j = Journal()
        for skill in (ENTRY, USING, VERIFY):
            j.skill(skill)
        j.call("Write", {"path": "module.mjs", "content": "PRIVATE_FUTURE"})
        if kind == "protocol":
            j.rows[0]["protocol_version"] = "future-version"
        elif kind == "event":
            j.rows.append({"type": "future.critical.event", "data": "PRIVATE_FUTURE"})
        elif kind == "agent-result":
            j.call("Agent", {"subagent_type": "eja-methodology", "prompt": make_packet()},
                   '{"futureAgent":"agent-7","profile":"eja-methodology","completed":true}')
        else:
            j.rows[4]["event"]["result"]["output"] = "Future successful Skill receipt format"
        return j

    def pending(self, j):
        ticket = ia.issue_ticket(self.payload, self.plugin, self.home)
        command = ia.audit_command(self.plugin, ticket, "check", self.home)
        self.assertEqual(ia.bind_call({**self.payload, "hook_event_name": "PreToolUse", "tool_name": "Bash",
            "tool_call_id": "audit-call", "tool_input": {"command": command}}, self.plugin, self.home), {})
        j.rows.append({"type": "context.append_message", "message": {"role": "user",
            "origin": {"kind": "system_trigger", "name": "stop_hook"},
            "content": [{"type": "text", "text": command}]}})
        j.event({"type": "tool.call", "toolCallId": "audit-call", "turnId": "0", "name": "Bash",
                 "args": {"command": command}})
        self.write(j)
        return ticket

    def test_additive_known_metadata_and_tool_fields_preserve_evidence_not_claims(self):
        for verification, want in ((True, "PASS"), (False, "FAIL")):
            j = Journal()
            for skill in ((ENTRY, USING, VERIFY) if verification else (ENTRY, USING)):
                j.skill(skill)
            j.rows[0]["futureMetadata"] = {"secret": "PRIVATE_FUTURE"}
            for row in j.rows:
                if row.get("type") == "context.append_loop_event":
                    row["event"]["futureField"] = "PRIVATE_FUTURE"
            j.rows.append({"type": "mcp.server_failure_resolved", "agentId": "main", "serverName": "owned",
                "transport": "stdio", "resolution": "recovered", "time": 1700000000000,
                "futureMetadata": "PRIVATE_FUTURE", "loaded_skill": VERIFY, "role": "implementation",
                "origin": {"kind": "user"}, "turnId": 88, "content": ["PRIVATE_FUTURE"], "error": "PRIVATE_FUTURE"})
            j.rows.append({"type": "mcp.server_failed", "agentId": "main", "serverName": "owned",
                "transport": "stdio", "time": 1700000000001, "resolution": "recovered"})
            with self.subTest(verification=verification):
                report = self.report(j.finish())
                self.assertEqual(report["formal_status"], want)
                self.assertEqual(report["workflow_status"], "NOT_PROVEN")

    def test_unknown_critical_evidence_is_terminal_unavailable_after_ticket(self):
        for kind in ("protocol", "event", "skill-result", "agent-result"):
            ticket = self.pending(self.changed(kind))
            before = self.wire.read_bytes()
            report = ia.check_ticket(ticket, "check", self.plugin, self.home, wait_seconds=0)
            with self.subTest(kind=kind):
                self.assertEqual(report["formal_status"], "UNVERIFIED")
                self.assertEqual(report.get("audit_availability", {}).get("status"), "UNAVAILABLE")
                self.assertNotIn("next_action", report)
                self.assertEqual(self.wire.read_bytes(), before)

    def test_unknown_format_before_stop_never_denies_completion_or_issues_ticket(self):
        for kind in ("protocol", "event", "skill-result", "agent-result"):
            self.write(self.changed(kind))
            before = self.wire.read_bytes()
            diagnostic = io.StringIO()
            with redirect_stderr(diagnostic):
                response = ia.stop_hook(self.payload, self.plugin, self.home)
            with self.subTest(kind=kind):
                self.assertEqual(response, {})
                self.assertFalse((self.home / "eja-workflow-audit/tickets").exists())
                self.assertNotIn("PRIVATE_FUTURE", diagnostic.getvalue())
                self.assertIn("unsupported_format", diagnostic.getvalue())
                self.assertEqual(self.wire.read_bytes(), before)

    def test_missing_or_corrupt_journal_is_unavailable_without_guessing_app_update(self):
        for raw in (None, "PRIVATE_FUTURE not JSON\n"):
            if raw is not None:
                self.wire.write_text(raw, encoding="utf8")
            diagnostic = io.StringIO()
            with redirect_stderr(diagnostic):
                response = ia.stop_hook(self.payload, self.plugin, self.home)
            with self.subTest(raw=raw):
                self.assertEqual(response, {})
                self.assertIn("journal_unavailable", diagnostic.getvalue())
                self.assertNotIn("PRIVATE_FUTURE", diagnostic.getvalue())
                self.assertFalse((self.home / "eja-workflow-audit/tickets").exists())

    def test_supported_missing_phase_remains_fail_with_bounded_recheck(self):
        j = Journal()
        j.skill(ENTRY)
        j.skill(USING)
        ticket = self.pending(j)
        report = ia.check_ticket(ticket, "check", self.plugin, self.home, wait_seconds=0)
        self.assertEqual(report["formal_status"], "FAIL")
        self.assertIn("next_action", report)
        self.assertNotEqual(report.get("audit_availability", {}).get("status"), "UNAVAILABLE")
        original = ia.report_path(self.home, ticket, "check").read_bytes()
        ia.check_ticket(ticket, "check", self.plugin, self.home, wait_seconds=0)
        self.assertEqual(ia.report_path(self.home, ticket, "check").read_bytes(), original)

    def test_guard_does_not_depend_on_journal_or_guess_from_new_tool_arguments(self):
        home = self.root / "guard-home-with-no-journals"
        state = home / "eja-workflow-guard"
        state.mkdir(parents=True)
        (state / "target.json").write_text(json.dumps({"schema_version": 1, "enabled": True,
            "workspace_root": str(self.root), "expires_at": 2000}), encoding="utf8")
        args = {"subagent_type": "eja-methodology", "prompt": make_packet(), "futureOption": "PRIVATE_FUTURE"}
        payload = {**self.payload, "hook_event_name": "PreToolUse", "tool_name": "Agent", "tool_call_id": "new-0",
                   "futureOuter": "PRIVATE_FUTURE", "tool_input": args}
        self.assertEqual(guard.pretool(payload, home, 1000), {})
        for n, mutation in enumerate(({"subagent_type": "coder"}, {"prompt": "not a packet"}, {"resume": "agent-7"}), 1):
            response = guard.pretool({**payload, "tool_call_id": "new-" + str(n),
                                      "tool_input": {**args, **mutation}}, home, 1000)
            self.assertEqual(response["hookSpecificOutput"]["permissionDecision"], "deny")
            self.assertNotIn("PRIVATE_FUTURE", json.dumps(response))

    def test_unavailable_recheck_keeps_original_failure_immutable(self):
        j = Journal()
        j.skill(ENTRY)
        j.skill(USING)
        ticket = self.pending(j)
        first = ia.check_ticket(ticket, "check", self.plugin, self.home, wait_seconds=0)
        self.assertEqual(first["formal_status"], "FAIL")
        original = ia.report_path(self.home, ticket, "check").read_bytes()
        j.event({"type": "tool.result", "toolCallId": "audit-call", "result": {"output": json.dumps(first)}})
        j.rows.append({"type": "future.critical.event"})
        command = ia.audit_command(self.plugin, ticket, "recheck", self.home)
        self.assertEqual(ia.bind_call({**self.payload, "tool_name": "Bash", "tool_call_id": "recheck-call",
            "tool_input": {"command": command}}, self.plugin, self.home), {})
        j.event({"type": "tool.call", "toolCallId": "recheck-call", "turnId": "0", "name": "Bash",
                 "args": {"command": command}})
        self.write(j)
        result = ia.check_ticket(ticket, "recheck", self.plugin, self.home, wait_seconds=0)
        self.assertEqual(result["audit_availability"]["status"], "UNAVAILABLE")
        self.assertEqual(result["recovery_status"], "UNAVAILABLE")
        self.assertEqual(result["original_formal_status"], "FAIL")
        self.assertEqual(ia.report_path(self.home, ticket, "check").read_bytes(), original)

    def test_child_unsupported_format_propagates_coverage_unavailability(self):
        child_wire = self.wire.parent.parent / "agent-7/wire.jsonl"
        child_wire.parent.mkdir()
        child = Journal()
        child.rows[0]["protocol_version"] = "future-version"
        child_wire.write_text("".join(json.dumps(r) + "\n" for r in child.finish().rows), encoding="utf8")
        j = Journal()
        for skill in (ENTRY, USING, VERIFY):
            j.skill(skill)
        j.call("Agent", {"subagent_type": "eja-methodology", "prompt": make_packet()},
               "agent_id: agent-7\nactual_subagent_type: eja-methodology\nstatus: completed\nReview result")
        report = self.report(j.finish(), role="delegator")
        self.assertEqual(report.get("audit_availability"), {"status": "UNAVAILABLE", "reason": "child_evidence_unavailable"})

    def test_known_agent_envelopes_and_errors_do_not_invent_adapter_failure(self):
        outputs = (("agent_id: agent-7\nactual_subagent_type: eja-methodology\nstatus: failed\n", False),
                   ("task_id: task-1\nstatus: running\nagent_id: agent-7\nactual_subagent_type: eja-methodology\nautomatic_notification: true\n", False),
                   ("ordinary launch failure", True))
        for output, error in outputs:
            j = Journal()
            j.call("Agent", {"prompt": make_packet()}, output, error)
            self.write(j)
            with self.subTest(output=output):
                self.assertIsNone(aw.evidence_availability(aw.load_log(self.wire)))

    def test_child_unavailability_dominates_aggregate_but_keeps_proven_failures(self):
        child_wire = self.wire.parent.parent / "agent-7/wire.jsonl"
        child_wire.parent.mkdir()
        child = Journal()
        child.rows[0]["protocol_version"] = "future-version"
        child_wire.write_text("".join(json.dumps(r) + "\n" for r in child.finish().rows), encoding="utf8")
        j = Journal()
        for skill in (ENTRY, USING):
            j.skill(skill)
        j.call("Agent", {"subagent_type": "eja-methodology", "prompt": make_packet()},
               "agent_id: agent-7\nactual_subagent_type: eja-methodology\nstatus: completed\nReview result")
        report = self.report(j.finish(), role="delegator")
        self.assertEqual(report["formal_status"], "UNVERIFIED")
        self.assertIn({"rule": "verification", "status": "FAIL", "evidence": []}, report["findings"])
        del j.rows[-2:]  # Remove both kernel and actor ends for the open checkpoint.
        ticket = self.pending(j)
        result = ia.check_ticket(ticket, "check", self.plugin, self.home, wait_seconds=0)
        self.assertEqual(result["formal_status"], "UNVERIFIED")
        self.assertEqual(result["workflow_status"], "NOT_PROVEN")
        self.assertNotIn("next_action", result)
        self.assertIn("verification", [f["rule"] for f in result["findings"] if f["status"] == "FAIL"])

    def test_invalid_original_never_authorizes_recheck_binding_or_repair(self):
        j = Journal()
        j.skill(ENTRY)
        ticket = self.pending(j)
        original = ia.check_ticket(ticket, "check", self.plugin, self.home, wait_seconds=0)
        command = ia.audit_command(self.plugin, ticket, "recheck", self.home)
        variants = [{}, {**original, "schema_version": True}, {**original, "formal_status": "future"},
                    {**original, "agent_id": "foreign"}, {**original, "scope": {}},
                    {**original, "workflow_status": "PASS"}, {**original, "findings": [{}]}]
        for n, value in enumerate(variants):
            ia.report_path(self.home, ticket, "check").write_text(json.dumps(value), encoding="utf8")
            for suffix in ("", " 2>&1"):
                response = ia.bind_call({**self.payload, "tool_name": "Bash", "tool_call_id": "recheck-" + str(n),
                    "tool_input": {"command": command + suffix}}, self.plugin, self.home)
                with self.subTest(value=value, suffix=suffix):
                    self.assertEqual(response["hookSpecificOutput"]["permissionDecision"], "deny")
                    self.assertNotIn("EJA_AUDIT_REPAIR:", str(response))
                    self.assertFalse(ia.ticket_path(self.home, ticket).with_suffix(".recheck.binding.json").exists())
                    self.assertFalse(ia.ticket_path(self.home, ticket).with_suffix(".recheck.repair.json").exists())


if __name__ == "__main__":
    unittest.main()
