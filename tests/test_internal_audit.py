"""Internal feedback uses native tool routing, never a fabricated user message."""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import threading
import time
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import internal_audit as ia
from test_workflow_audit import Journal, ENTRY, USING, VERIFY


class InternalAuditTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name) / "home"
        self.root = Path(self.temp.name) / "plugin"
        self.root.mkdir()
        (self.root / "kimi.plugin.json").write_text('{"version":"0.10.0"}', encoding="utf-8")
        self.session = "conv-owned"
        self.scope = self.home / "sessions" / "wd_owned" / self.session
        self.wire = self.scope / "agents" / "main" / "wire.jsonl"
        self.wire.parent.mkdir(parents=True)
        self.payload = {"hook_event_name": "Stop", "session_id": self.session,
                        "cwd": str(Path(self.temp.name)), "prompt": "PRIVATE_USER_PROMPT"}

    def write(self, journal, agent="main"):
        wire = self.scope / "agents" / agent / "wire.jsonl"
        wire.parent.mkdir(parents=True, exist_ok=True)
        wire.write_text("".join(json.dumps(r) + "\n" for r in journal.rows), encoding="utf-8")
        return wire

    def issue(self):
        return ia.issue_ticket(self.payload, self.root, self.home)

    def bind(self, ticket, phase="check", call="audit-call", session=None):
        command = ia.audit_command(self.root, ticket, phase, self.home)
        payload = {"hook_event_name": "PreToolUse", "session_id": session or self.session,
                   "tool_name": "Bash", "tool_call_id": call, "tool_input": {"command": command}}
        return ia.bind_call(payload, self.root, self.home), command

    def pending(self, command, agent="main", skills=(ENTRY, USING), code=False, include_stop=True):
        j = Journal()
        j.rows.extend([{"type": "turn.prompt", "turnId": 0}, {"type": "agent.turn.started", "turnId": 0}])
        for skill in skills:
            j.skill(skill)
        if code:
            j.call("Write", {"path": "port.mjs", "content": "PRIVATE_SOURCE"})
        if include_stop:
            j.rows.append({"type": "context.append_message", "message": {"role": "user", "content": [
                {"type": "text", "text": command}], "origin": {"kind": "system_trigger", "name": "stop_hook"}}})
        j.event({"type": "tool.call", "name": "Bash", "args": {"command": command}, "toolCallId": "audit-call", "turnId": 0})
        return j, self.write(j, agent)

    def test_ticket_contains_no_prompt_and_no_network_delivery(self):
        ticket = self.issue()
        value = json.loads(ia.ticket_path(self.home, ticket).read_text())
        self.assertNotIn("PRIVATE_USER_PROMPT", json.dumps(value))
        self.assertEqual(value["session_id"], self.session)
        self.assertFalse(any("send" in k or "token" in k for k in value))

    def test_only_fixed_owned_command_binds_and_duplicate_call_is_refused(self):
        ticket = self.issue()
        decision, _ = self.bind(ticket)
        self.assertEqual(decision, {})
        again, _ = self.bind(ticket, call="other-call")
        self.assertEqual(again["hookSpecificOutput"]["permissionDecision"], "deny")
        self.assertEqual(ia.bind_call({"tool_name": "Bash", "tool_input": {"command": "echo unchanged"}}, self.root, self.home), {})

    def test_wrong_session_cannot_bind(self):
        ticket = self.issue()
        decision, _ = self.bind(ticket, session="conv-foreign")
        self.assertEqual(decision["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_exact_call_identifies_child_without_latest_file_guess(self):
        ticket = self.issue()
        _, command = self.bind(ticket)
        self.write(Journal().finish())
        self.pending(command, "agent-41")
        report = ia.check_ticket(ticket, "check", self.root, self.home, wait_seconds=0)
        self.assertEqual(report["agent_id"], "agent-41")
        self.assertEqual(report["formal_status"], "FAIL")
        self.assertIn("verification", [f["rule"] for f in report["findings"] if f["status"] == "FAIL"])
        self.assertEqual(report["task_completion"], "PENDING")
        self.assertEqual(report["workflow_status"], "NOT_PROVEN")

    def test_absent_or_ambiguous_call_is_unverified(self):
        for duplicate in (False, True):
            ticket = self.issue()
            _, command = self.bind(ticket)
            self.write(Journal().finish())
            if duplicate:
                self.pending(command, "main")
                self.pending(command, "agent-42")
            report = ia.check_ticket(ticket, "check", self.root, self.home, wait_seconds=0)
            self.assertEqual(report["formal_status"], "UNVERIFIED")

    def test_reviewer_does_not_require_implementation_but_executor_does(self):
        self.write(Journal().finish())
        for code in (False, True):
            ticket = self.issue()
            _, command = self.bind(ticket)
            skills = (ENTRY, USING, VERIFY, "speckit-implement") if code else (ENTRY, USING, VERIFY)
            _, wire = self.pending(command, "agent-43", skills=skills, code=code)
            report = ia.check_ticket(ticket, "check", self.root, self.home, wait_seconds=0)
            failed = {f["rule"] for f in report["findings"] if f["status"] == "FAIL"}
            self.assertEqual("execution_before_code" in failed, code)
            self.assertEqual("tdd_before_code" in failed, code)
            self.assertNotIn("plan", failed)
            self.assertEqual(report["formal_status"], "FAIL" if code else "PASS")

    def test_code_probe_without_implementation_evidence_keeps_role_unverified(self):
        ticket = self.issue()
        _, command = self.bind(ticket)
        journal, _ = self.pending(command, skills=(ENTRY, USING, VERIFY), code=True)
        own_call, stop = journal.rows.pop(), journal.rows.pop()
        journal.symbol()
        journal.call("Bash", {"command": "node --test"}, "review probe passed")
        journal.rows.extend((stop, own_call))
        self.write(journal)
        report = ia.check_ticket(ticket, "check", self.root, self.home, wait_seconds=0)
        self.assertEqual(report["formal_status"], "UNVERIFIED")
        self.assertEqual(report["role"], "observed_code_writes_role_unverified")
        self.assertIn({"rule": "code_write_role_applicability", "status": "UNVERIFIED", "evidence": []}, report["findings"])
        self.assertFalse(any(f["rule"] in {"execution_before_code", "tdd_before_code"} for f in report["findings"]))
        self.assertEqual(ia._recovery({"formal_status": "FAIL"}, report, self.wire, "audit-call"), "UNRESOLVED")

    def test_other_pending_tool_or_unknown_evidence_does_not_pass(self):
        for extra in ("pending", "unknown"):
            ticket = self.issue()
            _, command = self.bind(ticket)
            j, wire = self.pending(command, skills=(ENTRY, USING, VERIFY))
            if extra == "pending":
                j.rows.insert(-1, {"type": "context.append_loop_event", "event": {
                    "type": "tool.call", "name": "Bash", "args": {}, "toolCallId": "other"}})
            else:
                j.rows.insert(-1, {"type": "future.record", "private": "HIDDEN"})
            self.write(j)
            report = ia.check_ticket(ticket, "check", self.root, self.home, wait_seconds=0)
            self.assertEqual(report["formal_status"], "UNVERIFIED")

    def test_report_redacted_and_original_immutable_after_recheck(self):
        ticket = self.issue()
        _, command = self.bind(ticket)
        j, wire = self.pending(command)
        original = ia.check_ticket(ticket, "check", self.root, self.home, wait_seconds=0)
        original_bytes = ia.report_path(self.home, ticket, "check").read_bytes()
        j.event({"type": "tool.result", "toolCallId": "audit-call", "result": {"output": "PRIVATE_REPORT_BODY"}})
        j.skill(VERIFY)
        j.call("Bash", {"command": "node --test"}, "PRIVATE_OUTPUT")
        _, recheck = self.bind(ticket, "recheck", "recheck-call")
        j.event({"type": "tool.call", "name": "Bash", "args": {"command": recheck}, "toolCallId": "recheck-call"})
        self.write(j)
        recovered = ia.check_ticket(ticket, "recheck", self.root, self.home, wait_seconds=0)
        self.assertEqual(recovered["original_formal_status"], "FAIL")
        self.assertEqual(recovered["recovery_status"], "FORMAL_RECOVERED_LATE")
        self.assertEqual(ia.report_path(self.home, ticket, "check").read_bytes(), original_bytes)
        self.assertNotIn("PRIVATE", json.dumps(recovered))
        repeated = ia.check_ticket(ticket, "recheck", self.root, self.home, wait_seconds=0)
        self.assertEqual(repeated["formal_status"], "UNVERIFIED")

    def test_plain_conversation_stop_is_noop(self):
        self.write(Journal().finish())
        self.assertEqual(ia.stop_hook(self.payload, self.root, self.home), {})

    def test_read_only_code_work_is_stop_eligible_without_a_shell_command(self):
        j = Journal()
        for skill in (ENTRY, USING):
            j.skill(skill)
        j.call("Read", {"path": "module.mjs"})
        self.write(j)
        response = ia.stop_hook(self.payload, self.root, self.home)
        self.assertIn("hookSpecificOutput", response)
        self.assertEqual(response["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_internal_swarm_attempt_always_retains_manual_lifecycle_coverage(self):
        ticket = self.issue()
        _, command = self.bind(ticket)
        j, _ = self.pending(command, skills=(ENTRY, USING, VERIFY))
        own_call, stop = j.rows.pop(), j.rows.pop()
        j.call("AgentSwarm", {"items": ["a", "b"], "prompt_template": "Review {{item}}"}, "swarm mode off", True)
        j.rows.extend((stop, own_call))
        self.write(j)
        report = ia.check_ticket(ticket, "check", self.root, self.home, wait_seconds=0)
        self.assertEqual(next(f["status"] for f in report["findings"] if f["rule"] == "swarm_coverage"), "UNVERIFIED")
        self.assertIn("native_agent_swarm_lifecycle", report["manual_coverage"])
        self.assertNotEqual(report["formal_status"], "PASS")

    def test_work_stop_returns_internal_continuation_not_a_message(self):
        j = Journal()
        j.rows.append({"type": "turn.prompt", "turnId": 0})
        j.skill(ENTRY)
        j.call("Write", {"path": "port.mjs", "content": "PRIVATE"})
        self.write(j)
        result = ia.stop_hook(self.payload, self.root, self.home)
        self.assertNotIn("message", result)
        decision = result["hookSpecificOutput"]
        self.assertEqual(decision["permissionDecision"], "deny")
        self.assertIn("check --ticket", decision["permissionDecisionReason"])

    def test_ticket_paths_reject_traversal_and_alternate_streams(self):
        for name in ("../foreign", "x:stream", "", "not-uuid"):
            with self.assertRaises(ValueError):
                ia.ticket_path(self.home, name)

    def test_actual_tool_cli_does_not_require_hook_only_environment(self):
        (self.root / "tools").mkdir()
        for name in ("internal_audit.py", "audit_workflow.py", "delegation_contract.py", "delegation_guard.py"):
            shutil.copy2(ROOT / "tools" / name, self.root / "tools" / name)
        ticket = self.issue()
        decision, command = self.bind(ticket)
        self.assertEqual(decision, {})
        self.pending(command)
        environment = dict(os.environ)
        environment.pop("KIMI_PLUGIN_ROOT", None)
        environment.pop("KIMI_CODE_HOME", None)
        before = self.wire.read_bytes()
        result = subprocess.run([sys.executable, "-B", "-X", "utf8", str(self.root / "tools" / "internal_audit.py"),
            "check", "--ticket", ticket, "--home", str(self.home)], env=environment,
            capture_output=True, text=True, encoding="utf-8", timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["formal_status"], "FAIL")
        self.assertEqual(self.wire.read_bytes(), before)

    @unittest.skipUnless(os.name == "nt", "Native Windows hook transport")
    def test_manifest_stop_and_pretool_really_transfer_native_stdin(self):
        (self.root / "tools").mkdir()
        for name in ("internal_audit.py", "audit_workflow.py", "delegation_contract.py", "delegation_guard.py"):
            shutil.copy2(ROOT / "tools" / name, self.root / "tools" / name)
        j = Journal()
        j.rows.append({"type": "turn.prompt", "turnId": 0})
        j.call("Write", {"path": "port.mjs", "content": "fixture"})
        self.write(j)
        manifest = json.loads((ROOT / "kimi.plugin.json").read_text(encoding="utf-8"))
        hooks = {h["event"]: h["command"] for h in manifest["hooks"] if h.get("matcher", "Bash") == "Bash"}
        environment = dict(os.environ, KIMI_PLUGIN_ROOT=str(self.root), KIMI_CODE_HOME=str(self.home))
        response = subprocess.run(hooks["Stop"], shell=True, env=environment, input=json.dumps(self.payload),
            capture_output=True, encoding="utf-8", timeout=10)
        self.assertEqual(response.returncode, 0, response.stderr)
        output = json.loads(response.stdout)
        self.assertEqual(output["hookSpecificOutput"]["permissionDecision"], "deny")
        command = output["hookSpecificOutput"]["permissionDecisionReason"].splitlines()[-1]
        native = {"hook_event_name": "PreToolUse", "session_id": self.session, "tool_name": "Bash",
                  "tool_call_id": "native-call", "tool_input": {"command": command}}
        response = subprocess.run(hooks["PreToolUse"], shell=True, env=environment, input=json.dumps(native),
            capture_output=True, encoding="utf-8", timeout=10)
        self.assertEqual(response.returncode, 0, response.stderr)
        self.assertEqual(json.loads(response.stdout), {})
        ticket = command.split("--ticket ", 1)[1].split()[0]
        binding = json.loads(ia.ticket_path(self.home, ticket).with_suffix(".check.binding.json").read_text())
        self.assertEqual(binding["tool_call_id"], "native-call")

    @unittest.skipUnless(os.name == "nt", "Native Windows hook transport")
    def test_manifest_delegation_hooks_really_refuse_only_opted_in_calls(self):
        import delegation_guard as guard
        (self.root / "tools").mkdir()
        for name in ("delegation_contract.py", "delegation_guard.py"):
            shutil.copy2(ROOT / "tools" / name, self.root / "tools" / name)
        manifest = json.loads((ROOT / "kimi.plugin.json").read_text(encoding="utf8"))
        hooks = {h.get("matcher"): h["command"] for h in manifest["hooks"] if h["event"] == "PreToolUse"}
        environment = dict(os.environ, KIMI_PLUGIN_ROOT=str(self.root), KIMI_CODE_HOME=str(self.home))
        for opted_in in (False, True):
            if opted_in:
                guard._configure(self.home, self.payload["cwd"], 1800)
            for tool in ("Agent", "AgentSwarm"):
                payload = {**self.payload, "hook_event_name": "PreToolUse", "tool_name": tool,
                           "tool_call_id": "native-" + tool, "tool_input": {"subagent_type": "coder"}}
                response = subprocess.run(hooks[tool], shell=True, env=environment, input=json.dumps(payload),
                                          capture_output=True, encoding="utf8", timeout=10)
                self.assertEqual(response.returncode, 0, response.stderr)
                result = json.loads(response.stdout)
                if opted_in:
                    self.assertEqual(result["hookSpecificOutput"]["permissionDecision"], "deny")
                else:
                    self.assertEqual(result, {})

    def test_missing_own_stop_message_cannot_consume_another_agents_ticket(self):
        ticket = self.issue()
        _, command = self.bind(ticket)
        self.pending(command, "main")
        # The same exact command in the child is not proof that Stop called that child.
        self.wire.write_text("".join(json.dumps(r) + "\n" for r in Journal().finish().rows))
        self.pending(command, "agent-42", skills=(ENTRY, USING, VERIFY), include_stop=False)
        result = ia.check_ticket(ticket, "check", self.root, self.home, wait_seconds=0)
        self.assertEqual(result["formal_status"], "UNVERIFIED")

    def test_new_user_turn_cannot_recheck_an_old_ticket(self):
        ticket = self.issue()
        _, command = self.bind(ticket)
        j, _ = self.pending(command)
        ia.check_ticket(ticket, "check", self.root, self.home, wait_seconds=0)
        j.event({"type": "tool.result", "toolCallId": "audit-call", "result": {"output": "report"}})
        j.rows.extend([{"type": "agent.turn.ended", "turnId": 0, "outcome": "done"},
            {"type": "turn.prompt", "turnId": 1}, {"type": "agent.turn.started", "turnId": 1}])
        j.skill(VERIFY)
        j.call("Bash", {"command": "node --test"})
        _, command = self.bind(ticket, "recheck", "recheck-call")
        j.event({"type": "tool.call", "toolCallId": "recheck-call", "name": "Bash", "args": {"command": command}, "turnId": 1})
        self.write(j)
        result = ia.check_ticket(ticket, "recheck", self.root, self.home, wait_seconds=0)
        self.assertEqual(result["formal_status"], "UNVERIFIED")
        self.assertNotEqual(result.get("recovery_status"), "FORMAL_RECOVERED_LATE")

    def test_steering_after_stop_invalidates_the_checkpoint(self):
        for event in ({"type": "prompt.steered"}, {"type": "turn.steer"},
                {"type": "context.append_message", "message": {"origin": {"kind": "user", "inTurn": True}, "content": []}}):
            ticket = self.issue()
            _, command = self.bind(ticket)
            j, _ = self.pending(command, skills=(ENTRY, USING, VERIFY))
            j.rows.insert(-1, event)
            self.write(j)
            self.assertEqual(ia.check_ticket(ticket, "check", self.root, self.home, wait_seconds=0)["formal_status"], "UNVERIFIED")

    def test_past_skills_do_not_prove_current_written_code_was_verified(self):
        ticket = self.issue()
        _, command = self.bind(ticket)
        j = Journal()
        for skill in (ENTRY, USING, VERIFY, "superpowers:executing-plans", "superpowers:test-driven-development"):
            j.skill(skill)
        j.rows.extend([{"type": "agent.turn.ended", "turnId": 0, "outcome": "done"},
            {"type": "turn.prompt", "turnId": 1}, {"type": "agent.turn.started", "turnId": 1}])
        j.call("Write", {"path": "new.mjs", "content": "new task"})
        j.rows.append({"type": "context.append_message", "message": {"origin": {"kind": "system_trigger", "name": "stop_hook"},
            "role": "user", "content": [{"type": "text", "text": command}]}})
        j.event({"type": "tool.call", "toolCallId": "audit-call", "name": "Bash", "args": {"command": command}, "turnId": 1})
        self.write(j)
        report = ia.check_ticket(ticket, "check", self.root, self.home, wait_seconds=0)
        self.assertNotEqual(report["formal_status"], "PASS")

    def test_factual_new_turn_does_not_inherit_old_work_eligibility(self):
        j = Journal()
        j.call("Write", {"path": "old.mjs", "content": "old"})
        j.finish()
        j.rows.extend([{"type": "turn.prompt", "turnId": 1}, {"type": "agent.turn.started", "turnId": 1}])
        self.write(j)
        self.assertEqual(ia.stop_hook(self.payload, self.root, self.home), {})

    def test_recovery_rejects_mixed_unknown_and_nonskill_evidence(self):
        j = Journal()
        j.call("Bash", {"command": "first check"})
        j.skill(ENTRY)
        for skill in ("superpowers:executing-plans", "superpowers:test-driven-development"):
            j.call("Read", {"path": "ordinary.txt", "skill": skill})
        j.call("Bash", {"command": "node --test"})
        self.write(j)
        original = {"formal_status": "FAIL"}
        for findings in ([{"rule": "entry_first", "status": "FAIL"}, {"rule": "child_missing", "status": "UNVERIFIED"}],
                [{"rule": "execution_before_code", "status": "FAIL"}, {"rule": "tdd_before_code", "status": "FAIL"}]):
            current = {"formal_status": "FAIL", "findings": findings}
            self.assertEqual(ia._recovery(original, current, self.wire, "tool_1"), "UNRESOLVED")

    def test_auditor_source_read_search_and_hash_are_unrelated_commands(self):
        script = (self.root / "tools" / "internal_audit.py").as_posix()
        for command in (f'type "{script}"', f'Get-Content "{script}"', f'rg audit "{script}"', f'python -m py_compile "{script}"'):
            self.assertEqual(ia.bind_call({"tool_name": "Bash", "tool_input": {"command": command}}, self.root, self.home), {})

    def test_stop_rejects_redirected_wire_before_opening_it(self):
        outside = Path(self.temp.name) / "outside.jsonl"
        outside.write_text('{"type":"metadata"}\n')
        try:
            os.symlink(outside, self.wire)
        except OSError as error:
            self.skipTest(str(error))
        with patch.object(ia.aw, "load_log", side_effect=AssertionError("outside read")) as reader:
            self.assertEqual(ia.stop_hook(self.payload, self.root, self.home), {})
            reader.assert_not_called()

    def test_missing_peer_gets_the_bounded_persistence_wait(self):
        ticket = self.issue()
        _, command = self.bind(ticket)
        self.pending(command, skills=(ENTRY, USING, VERIFY))
        peer = self.scope / "agents" / "agent-99" / "wire.jsonl"
        peer.parent.mkdir()
        timer = threading.Timer(0.12, lambda: peer.write_text("".join(json.dumps(r) + "\n" for r in Journal().finish().rows)))
        timer.start()
        try:
            started = time.monotonic()
            result = ia.check_ticket(ticket, "check", self.root, self.home, wait_seconds=0.7)
            self.assertEqual(result["formal_status"], "PASS")
            self.assertGreaterEqual(time.monotonic() - started, 0.1)
        finally:
            timer.join()

    def test_malformed_origin_after_real_stop_remains_unverified(self):
        for origin in (None, [], "user"):
            ticket = self.issue()
            _, command = self.bind(ticket)
            j, _ = self.pending(command, skills=(ENTRY, USING, VERIFY))
            j.rows.insert(-1, {"type": "context.append_message", "message": {"origin": origin, "content": []}})
            self.write(j)
            result = ia.check_ticket(ticket, "check", self.root, self.home, wait_seconds=0)
            self.assertEqual(result["formal_status"], "UNVERIFIED")

    def native_activation(self, journal, skill, message_id):
        """Append the observed native model-tool Skill steering sequence."""
        journal.skill(skill)
        call_row, result_row = journal.rows[-2:]
        call_row["agentId"] = result_row["agentId"] = "main"
        call_row["event"].update(uuid="call-" + message_id, turnId="0")
        result_row["event"]["parentUuid"] = "call-" + message_id
        journal.rows.insert(-1, {"type": "prompt.steered", "agentId": "main",
            "activePromptId": "active-0", "messageId": message_id,
            "promptIds": [message_id], "content": "PRIVATE_SKILL_BODY"})
        origin = {"kind": "skill_activation", "trigger": "model-tool", "inTurn": True,
                  "activationId": "activation-" + message_id, "skillName": skill,
                  "skillPath": str(self.root / "skills" / "SKILL.md"), "skillSource": "extra"}
        journal.rows.append({"type": "context.append_message", "agentId": "main", "message": {
            "role": "user", "id": message_id, "origin": dict(origin),
            "content": [{"type": "text", "text": "PRIVATE_SKILL_BODY"}]}})
        journal.rows.append({"type": "turn.steer", "agentId": "main", "turnId": 0,
            "messageId": message_id, "promptIds": [message_id], "origin": dict(origin),
            "input": "PRIVATE_SKILL_BODY"})

    def native_recovery(self, mutate=None):
        """Exercise real Stop/check/activation/recheck without an injected user turn."""
        ticket = self.issue()
        _, command = self.bind(ticket)
        journal, _ = self.pending(command, skills=())
        for row in journal.rows:
            if row.get("type") == "turn.prompt":
                row.update(agentId="main", promptId="active-0")
        self.write(journal)
        original = ia.check_ticket(ticket, "check", self.root, self.home, wait_seconds=0)
        original_bytes = ia.report_path(self.home, ticket, "check").read_bytes()
        journal.event({"type": "tool.result", "toolCallId": "audit-call", "result": {"output": "report"}})
        for number, skill in enumerate((ENTRY, USING, VERIFY)):
            self.native_activation(journal, skill, "skill-" + str(number))
        journal.call("Bash", {"command": "node --test"}, "fresh verification")
        if mutate:
            mutate(journal.rows)
        _, command = self.bind(ticket, "recheck", "recheck-call")
        journal.event({"type": "tool.call", "toolCallId": "recheck-call", "name": "Bash",
                       "args": {"command": command}, "turnId": "0"})
        self.write(journal)
        result = ia.check_ticket(ticket, "recheck", self.root, self.home, wait_seconds=0)
        self.assertEqual(ia.report_path(self.home, ticket, "check").read_bytes(), original_bytes)
        return original, result

    def test_model_tool_skill_steering_allows_bound_recovery_without_erasing_failure(self):
        original, recovered = self.native_recovery()
        self.assertEqual(original["formal_status"], "FAIL")
        self.assertEqual(recovered.get("original_formal_status"), "FAIL")
        self.assertEqual(recovered.get("recovery_status"), "FORMAL_RECOVERED_LATE")
        self.assertEqual(recovered["formal_status"], "FAIL")  # First entry is still historically late.
        self.assertNotIn("PRIVATE", json.dumps(recovered))

    def test_other_audit_command_does_not_count_as_recovery_verification(self):
        def replace_check(rows):
            for row in rows:
                event = row.get("event", {})
                if event.get("type") == "tool.call" and event.get("args", {}).get("command") == "node --test":
                    event["args"]["command"] = ia.audit_command(self.root,
                        "00000000-0000-4000-8000-000000000000", "check", self.home)
        _, recovered = self.native_recovery(replace_check)
        self.assertEqual(recovered.get("recovery_status"), "UNRESOLVED")

    def test_exact_guard_denial_is_not_an_executed_child_or_independent_review(self):
        import delegation_guard as guard
        guard._configure(self.home, self.payload["cwd"], 1800)
        args = {"subagent_type": "coder", "prompt": "PRIVATE_HANDOFF"}
        payload = {**self.payload, "hook_event_name": "PreToolUse", "tool_name": "Agent",
                   "tool_call_id": "tool_4", "tool_input": args}
        reason = guard.pretool(payload, self.home, time.time())["hookSpecificOutput"]["permissionDecisionReason"]
        j = Journal()
        for skill in (ENTRY, USING, VERIFY):
            j.skill(skill)
        j.call("Agent", args, reason, True)
        j.rows[-2]["event"]["uuid"] = "exact-native-call"
        j.rows[-1]["event"]["parentUuid"] = "exact-native-call"
        self.write(j.finish())
        report = ia.aw.audit(ia.aw.load_log(self.wire), "delegator", guard_home=self.home, guard_session=self.session)
        by_rule = {f["rule"]: f["status"] for f in report["findings"]}
        self.assertEqual(by_rule["delegation_refused_before_execution"], "PASS")
        self.assertNotIn("child_result", by_rule)
        self.assertEqual(by_rule["independent_child"], "FAIL")
        j.rows[-3]["event"]["parentUuid"] = "foreign-call"
        self.write(j)
        foreign = ia.aw.audit(ia.aw.load_log(self.wire), "delegator", guard_home=self.home, guard_session=self.session)
        self.assertIn("child_result", {f["rule"] for f in foreign["findings"]})
        j.rows[-3]["event"]["parentUuid"] = "exact-native-call"
        args["prompt"] = "CHANGED_PRIVATE_HANDOFF"
        j.rows[-4]["event"]["args"] = args
        self.write(j)
        changed = ia.aw.audit(ia.aw.load_log(self.wire), "delegator", guard_home=self.home, guard_session=self.session)
        self.assertIn("child_result", {f["rule"] for f in changed["findings"]})

    def test_unsupported_skill_steering_cannot_bypass_user_or_identity_guards(self):
        def change(rows, field, value):
            for row in rows:
                if row.get("type") == "prompt.steered":
                    row[field] = value
                    return

        def change_origin(rows):
            for row in rows:
                origin = row.get("origin") or row.get("message", {}).get("origin")
                if isinstance(origin, dict) and origin.get("kind") == "skill_activation":
                    origin["trigger"] = "user-slash"

        def remove_context(rows):
            for number, row in enumerate(rows):
                if row.get("message", {}).get("origin", {}).get("kind") == "skill_activation":
                    rows.pop(number)
                    return

        def wrong_parent(rows):
            for row in rows:
                event = row.get("event", {})
                if "parentUuid" in event:
                    event["parentUuid"] = "unrelated-call"
                    return

        def duplicate_activation(rows):
            for row in rows:
                origin = row.get("origin") or row.get("message", {}).get("origin")
                if isinstance(origin, dict) and origin.get("kind") == "skill_activation":
                    origin["activationId"] = "same-activation"

        def wrong_kernel(rows):
            next(row for row in rows if row.get("type") == "turn.steer")["turnId"] = 1

        def malformed_result_turn(rows):
            next(row["event"] for row in rows if "parentUuid" in row.get("event", {}))["turnId"] = True

        def prompt_agent(rows, agent):
            for row in rows:
                if row.get("type") == "turn.prompt":
                    if agent is None:
                        row.pop("agentId", None)
                    else:
                        row["agentId"] = agent

        def bad_origin_metadata(rows, key, value):
            for row in rows:
                origin = row.get("origin") or row.get("message", {}).get("origin")
                if isinstance(origin, dict) and origin.get("kind") == "skill_activation":
                    origin[key] = value

        for label, mutate in (("wrong_prompt", lambda rows: change(rows, "activePromptId", "another-task")),
                ("mixed_inputs", lambda rows: change(rows, "promptIds", ["skill-0", "user-input"])),
                ("user_slash", change_origin), ("missing_context", remove_context),
                ("unrelated_result", wrong_parent), ("duplicate_activation", duplicate_activation),
                ("different_kernel", wrong_kernel),
                ("malformed_result_turn", malformed_result_turn),
                ("foreign_prompt_agent", lambda rows: prompt_agent(rows, "agent-foreign")),
                ("missing_prompt_agent", lambda rows: prompt_agent(rows, None)),
                ("unknown_origin_field", lambda rows: bad_origin_metadata(rows, "future", True)),
                ("malformed_origin_path", lambda rows: bad_origin_metadata(rows, "skillPath", []))):
            with self.subTest(label=label):
                _, result = self.native_recovery(mutate)
                self.assertEqual(result["formal_status"], "UNVERIFIED")
                self.assertNotEqual(result.get("recovery_status"), "FORMAL_RECOVERED_LATE")

    def test_native_stop_reentry_uses_kernel_prompt_not_actor_turn(self):
        ticket = self.issue()
        _, command = self.bind(ticket)
        j, _ = self.pending(command, skills=(ENTRY, USING, VERIFY))
        j.rows.insert(-2, {"type": "agent.turn.ended", "turnId": 0, "outcome": "done"})
        j.rows.insert(-1, {"type": "agent.turn.started", "turnId": 1})
        j.rows[-1]["event"]["turnId"] = "0"
        self.write(j)
        result = ia.check_ticket(ticket, "check", self.root, self.home, wait_seconds=0)
        self.assertEqual(result["formal_status"], "PASS")
        self.assertEqual(result["scope"]["turn_id"], 0)

    def test_stop_eligibility_survives_actor_end_before_kernel_completion(self):
        j = Journal()
        j.rows.extend([{"type": "turn.prompt", "turnId": 0}, {"type": "agent.turn.started", "turnId": 0}])
        j.call("Write", {"path": "port.mjs", "content": "source"})
        j.rows.append({"type": "agent.turn.ended", "turnId": 0, "outcome": "done"})
        self.write(j)
        self.assertEqual(ia.stop_hook(self.payload, self.root, self.home)["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_kernel_end_and_noncanonical_event_ids_invalidate_ticket(self):
        for event_id in ("00", "1", True):
            ticket = self.issue()
            _, command = self.bind(ticket)
            j, _ = self.pending(command, skills=(ENTRY, USING, VERIFY))
            j.rows[-1]["event"]["turnId"] = event_id
            self.write(j)
            self.assertEqual(ia.check_ticket(ticket, "check", self.root, self.home, wait_seconds=0)["formal_status"], "UNVERIFIED")


if __name__ == "__main__":
    unittest.main()
