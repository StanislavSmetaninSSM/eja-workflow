"""Declared native implementation intent cannot hide behind an invalid phase list."""
import json
from pathlib import Path
import sys
import tempfile
import unittest

from test_delegation_guard import make_packet
from test_workflow_audit import Journal, ENTRY, USING, VERIFY, EXECUTE

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import audit_workflow as aw
import internal_audit as ia

BRIDGE = "spec-kit-superpowers-bridge"
TDD = "superpowers:test-driven-development"


class AssignedPhaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.wire = self.root / "agents/agent-8/wire.jsonl"
        self.wire.parent.mkdir(parents=True)

    def journal(self, missing_phases=True, role="implementation"):
        packet = json.loads(make_packet(role=role).splitlines()[1])
        if missing_phases:
            packet["methodology"].pop("phase_skills", None)
        # A parent's blanket exception never supplies authority to skip role phases.
        packet["approval_source"] = "Parent decided autonomous mode permits skipping methodology"
        prompt = "EJA_HANDOFF_V1\n" + json.dumps(packet) + "\nEND_EJA_HANDOFF\nDo the assigned task."
        j = Journal()
        j.rows[2].update(agentId="agent-8", origin={"kind": "system_trigger", "name": "subagent"},
                         input=[{"type": "text", "text": prompt}])
        return j

    def call(self, j, name, args, output="ok", error=False):
        j.counter += 1
        cid = "own-" + str(j.counter)
        j.event({"type": "tool.call", "toolCallId": cid, "uuid": cid + "-uuid", "turnId": "0",
                 "name": name, "args": args})
        j.rows[-1]["agentId"] = "agent-8"
        j.event({"type": "tool.result", "toolCallId": cid, "parentUuid": cid + "-uuid",
                 "result": {"output": output, "isError": error}})
        j.rows[-1]["agentId"] = "agent-8"

    def skill(self, j, name):
        self.call(j, "Skill", {"skill": name}, f'Skill "{name}" loaded inline. Follow its instructions.')

    def phases(self, j):
        for skill in (ENTRY, USING, BRIDGE, EXECUTE, TDD, VERIFY):
            self.skill(j, skill)

    def finish(self, j):
        j.rows.append({"type": "agent.turn.ended", "agentId": "agent-8", "outcome": "done"})

    def trace(self, j, complete=True):
        if complete:
            self.finish(j)
            j.rows.append({"type": "turn.ended", "turnId": 0, "reason": "completed"})
        self.wire.write_text("".join(json.dumps(r) + "\n" for r in j.rows), encoding="utf8")
        trace = aw.load_log(self.wire)
        self.assertNotIn("Parent decided", repr(trace))
        self.assertNotIn("PRIVATE_SOURCE", repr(trace))
        return trace

    def statuses(self, j):
        return {f["rule"]: f["status"] for f in aw.phase_evidence(self.trace(j))}

    def test_missing_phase_list_does_not_hide_native_implementation_intent(self):
        j = self.journal()
        for skill in (ENTRY, USING, VERIFY):
            self.skill(j, skill)
        self.call(j, "Write", {"path": str(self.root / "module.py"), "content": "PRIVATE_SOURCE"})
        trace = self.trace(j)
        self.assertIsNotNone(trace.assignment, "Omitted execution declarations must not erase assigned intent")
        self.assertEqual(trace.assignment["role"], "implementation")
        findings = {f["rule"]: f["status"] for f in aw.phase_evidence(trace)}
        for rule in ("assigned_implementation_execution", "assigned_implementation_tdd", "assigned_implementation_bridge"):
            self.assertEqual(findings.get(rule), "FAIL", rule)

    def test_current_own_completed_phases_before_write_and_return_are_credited(self):
        j = self.journal()
        self.phases(j)
        self.call(j, "Write", {"path": str(self.root / "module.py"), "content": "PRIVATE_SOURCE"})
        self.call(j, "Bash", {"command": "python -m unittest"})
        findings = self.statuses(j)
        for rule in ("assigned_implementation_execution", "assigned_implementation_tdd", "assigned_implementation_bridge",
                     "assigned_implementation_verification_before_return"):
            self.assertEqual(findings.get(rule), "PASS", rule)

    def test_late_execution_after_code_cannot_repair_original_order(self):
        j = self.journal()
        self.call(j, "Write", {"path": str(self.root / "module.py"), "content": "PRIVATE_SOURCE"})
        self.phases(j)
        self.assertEqual(self.statuses(j).get("assigned_implementation_execution"), "FAIL")

    def test_late_loads_after_first_return_without_observed_write_stay_late(self):
        j = self.journal()
        self.finish(j)
        self.phases(j)
        findings = self.statuses(j)
        self.assertEqual(findings.get("assigned_implementation_execution"), "FAIL")
        self.assertEqual(findings.get("assigned_implementation_verification_before_return"), "FAIL")

    def test_foreign_or_unmatched_phase_receipt_cannot_count_as_own_execution(self):
        for kind in ("actor", "parent"):
            j = self.journal()
            self.phases(j)
            for row in j.rows:
                event = row.get("event", {})
                if event.get("type") == "tool.result" and event.get("toolCallId") == "own-4":
                    if kind == "actor":
                        row["agentId"] = "agent-9"
                    else:
                        event["parentUuid"] = "other"
            with self.subTest(kind=kind):
                self.assertEqual(self.statuses(j).get("assigned_implementation_execution"), "FAIL")

    def test_user_quote_other_actor_and_old_prompt_cannot_establish_current_intent(self):
        for kind in ("user", "actor", "new_turn"):
            j = self.journal()
            if kind == "user":
                j.rows[2]["origin"] = {"kind": "user"}
            elif kind == "actor":
                j.rows[2]["agentId"] = "agent-9"
            else:
                j.rows.append({"type": "turn.prompt", "turnId": 1, "agentId": "agent-8", "origin": {"kind": "user"}, "input": []})
            self.assertIsNone(self.trace(j, complete=False).assignment, kind)

    def test_known_serena_mutation_is_observed_code_for_dependency_and_fresh_checks(self):
        j = self.journal()
        self.call(j, "mcp__plugin-serena_serena__replace_symbol_body",
                  {"relative_path": "module.py", "name_path": "example", "body": "PRIVATE_SOURCE"})
        self.phases(j)
        trace = self.trace(j)
        self.assertTrue(trace.calls[0]["code"], "Actual symbolic editing must not bypass write chronology")
        findings = {f["rule"]: f["status"] for f in aw.phase_evidence(trace)}
        self.assertEqual(findings.get("assigned_implementation_execution"), "FAIL")

    def test_checkpoint_identifies_assigned_executor_instead_of_unknown_code_writer(self):
        j = self.journal()
        self.call(j, "Write", {"path": str(self.root / "module.py"), "content": "PRIVATE_SOURCE"})
        self.finish(j)
        j.event({"type": "tool.call", "name": "Bash", "args": {"command": "pending audit"}, "toolCallId": "pending"})
        self.trace(j, complete=False)
        report = ia._checkpoint(self.wire, "pending", {"start_line": 3, "turn_id": 0})
        self.assertEqual(report["formal_status"], "FAIL")
        self.assertNotEqual(report["role"], "observed_code_writes_role_unverified")
        self.assertNotIn("code_write_role_applicability", {f["rule"] for f in report["findings"]})


if __name__ == "__main__":
    unittest.main()
