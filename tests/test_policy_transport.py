"""Policy references must deliver real current instructions, not just a hash."""
import copy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from test_delegation_guard import make_packet
from test_workflow_audit import Journal

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import audit_workflow as aw
import delegation_guard as guard
from delegation_contract import parse_packet

POLICY = ROOT / "skills/eja-workflow/references/contract-review.md"


def envelope(packet):
    return "EJA_HANDOFF_V1\n" + json.dumps(packet, ensure_ascii=False) + "\nEND_EJA_HANDOFF\nReview."


class PolicyTransportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.wire = self.root / "agents/agent-12/wire.jsonl"
        self.wire.parent.mkdir(parents=True)
        self.policy = POLICY.read_text(encoding="utf8").replace("\r\n", "\n").strip()
        self.ref = {"version": 1, "sha256": hashlib.sha256(self.policy.encode("utf8")).hexdigest()}
        self.packet = parse_packet(make_packet(
            write_roots=[str(self.root / ".work/review")],
            result_artifact=str(self.root / ".work/review/report.md")))
        self.packet["methodology"]["child_guide"] = str(POLICY.with_name("child-agents.md"))

    def reference_packet(self):
        packet = copy.deepcopy(self.packet)
        packet.pop("contract_review")
        packet["contract_review_ref"] = copy.deepcopy(self.ref)
        return packet

    def journal(self, packet=None):
        j = Journal()
        j.rows[2].update(agentId="agent-12", origin={"kind": "system_trigger", "name": "subagent"},
                         input=[{"type": "text", "text": envelope(packet or self.packet)}])
        return j

    def call(self, j, name, args, output="ok", error=False):
        j.counter += 1
        cid = "native-" + str(j.counter)
        j.event({"type": "tool.call", "toolCallId": cid, "uuid": cid + "-uuid", "turnId": "0",
                 "name": name, "args": args})
        j.rows[-1]["agentId"] = "agent-12"
        j.event({"type": "tool.result", "toolCallId": cid, "parentUuid": cid + "-uuid",
                 "result": {"output": output, "isError": error}})
        j.rows[-1]["agentId"] = "agent-12"

    def policy_read(self, j, output=None, path=None, error=False):
        numbered = "\n".join(str(n) + "\t" + line for n, line in enumerate(self.policy.splitlines(), 1))
        self.call(j, "Read", {"path": str(path or POLICY)}, numbered if output is None else output, error)

    def findings(self, j):
        j.finish()
        self.wire.write_text("".join(json.dumps(r) + "\n" for r in j.rows), encoding="utf8")
        trace = aw.load_log(self.wire)
        self.assertNotIn("Авторитет требований", repr(trace))
        return {f["rule"]: f["status"] for f in aw.phase_evidence(trace)}

    def test_current_reference_admitted_without_verbatim_policy_copy(self):
        packet = self.reference_packet()
        try:
            parsed = parse_packet(envelope(packet))
        except ValueError as error:
            self.fail("Current exact policy reference was refused: " + str(error))
        self.assertEqual(parsed["contract_review_ref"], self.ref)
        state = self.root / "home/eja-workflow-guard"
        state.mkdir(parents=True)
        (state / "target.json").write_text(json.dumps({"schema_version": 1, "enabled": True,
            "workspace_root": str(self.root), "expires_at": 2000}), encoding="utf8")
        result = guard.pretool({"session_id": "owned", "tool_call_id": "review", "cwd": str(self.root),
            "tool_name": "Agent", "tool_input": {"subagent_type": "eja-methodology", "prompt": envelope(packet)}},
            self.root / "home", 1000)
        self.assertEqual(result, {}, "The optional guard must accept the same current reference")
        self.assertFalse((state / "denials").exists())

    def test_legacy_packet_stays_valid_and_bad_or_ambiguous_reference_is_refused(self):
        self.assertEqual(parse_packet(envelope(self.packet))["role"], "review")
        bad_refs = [{"version": True, "sha256": self.ref["sha256"]},
                    {"version": 2, "sha256": self.ref["sha256"]},
                    {"version": 1, "sha256": "0" * 64},
                    {**self.ref, "extra": "unrecognized"}]
        for ref in bad_refs:
            packet = self.reference_packet()
            packet["contract_review_ref"] = ref
            with self.subTest(ref=ref), self.assertRaises(ValueError):
                parse_packet(envelope(packet))
        packet = copy.deepcopy(self.packet)
        packet["contract_review_ref"] = self.ref
        with self.assertRaises(ValueError):
            parse_packet(envelope(packet))

    def test_reference_alone_is_not_receipt_and_full_early_read_is_credited(self):
        for packet in (self.packet, self.reference_packet()):
            with self.subTest(mode="reference" if "contract_review_ref" in packet else "legacy"):
                self.assertEqual(self.findings(self.journal(packet)).get("review_policy_received"), "FAIL")
                j = self.journal(packet)
                self.policy_read(j)
                self.call(j, "Read", {"path": str(self.root / "module.mjs")}, "PRIVATE_SOURCE")
                findings = self.findings(j)
                self.assertEqual(findings.get("review_policy_received"), "PASS")
                self.assertEqual(findings.get("review_policy_before_work"), "PASS")

    def test_failed_partial_wrong_path_or_foreign_result_cannot_prove_receipt(self):
        for kind in ("failed", "partial", "path", "actor", "parent", "turn"):
            j = self.journal()
            self.policy_read(j, output=self.policy[:50] if kind == "partial" else None,
                             path=self.root / "copy.md" if kind == "path" else None,
                             error=kind == "failed")
            if kind == "actor":
                j.rows[-1]["agentId"] = "agent-13"
            elif kind == "parent":
                j.rows[-1]["event"]["parentUuid"] = "other-uuid"
            elif kind == "turn":
                j.rows[-1]["event"]["turnId"] = "88"
            with self.subTest(kind=kind):
                self.assertEqual(self.findings(j).get("review_policy_received"), "FAIL")

    def test_late_read_does_not_rewrite_order_and_bootstrap_reads_are_allowed(self):
        for path, want in ((self.root / "README.md", "FAIL"),
                           (ROOT / "README.md", "FAIL"),
                           (self.root / "module.mjs", "FAIL"),
                           (POLICY.with_name("child-agents.md"), "PASS"),
                           (self.root / "specs/001-feature/spec.md", "PASS")):
            j = self.journal()
            self.call(j, "Read", {"path": str(path)})
            self.policy_read(j)
            findings = self.findings(j)
            with self.subTest(path=path):
                self.assertEqual(findings.get("review_policy_received"), "PASS")
                self.assertEqual(findings.get("review_policy_before_work"), want)

    def test_old_task_policy_read_is_not_current_assignment_receipt(self):
        j = self.journal()
        self.policy_read(j)
        j.rows.append({**j.rows[2], "turnId": 1})
        self.wire.write_text("".join(json.dumps(r) + "\n" for r in j.rows), encoding="utf8")
        trace = aw.load_log(self.wire)
        findings = {f["rule"]: f["status"] for f in aw.phase_evidence(trace)}
        self.assertNotEqual(findings.get("review_policy_received"), "PASS")

    def test_generator_is_deterministic_and_check_never_writes(self):
        script = ROOT / "tools/sync_profile_policy.py"
        self.assertTrue(script.is_file(), "The own-profile policy generator is not implemented")
        package = self.root / "package"
        policy = package / "skills/eja-workflow/references/contract-review.md"
        profile = package / "agents/eja-methodology.md"
        policy.parent.mkdir(parents=True)
        profile.parent.mkdir(parents=True)
        policy.write_bytes("# Canonical\r\n\r\nExact ёж policy.\r\n".encode("utf8"))
        profile.write_text("# Child\n\nOriginal instructions.\n", encoding="utf8")
        before = profile.read_bytes()
        def run(*args):
            return subprocess.run([sys.executable, "-B", str(script), "--root", str(package), *args],
                                  capture_output=True, text=True, encoding="utf8")
        self.assertNotEqual(run("--check").returncode, 0)
        self.assertEqual(profile.read_bytes(), before)
        ref_path = policy.with_name("contract-review.ref.json")
        self.assertFalse(ref_path.exists())
        result = run()
        self.assertEqual(result.returncode, 0, result.stderr)
        rendered = profile.read_text(encoding="utf8")
        self.assertTrue(rendered.startswith(before.decode("utf8").replace("\r\n", "\n")))
        self.assertIn("# Canonical\n\nExact ёж policy.", rendered)
        expected_ref = {"version": 1, "sha256": hashlib.sha256(
            "# Canonical\n\nExact ёж policy.".encode("utf8")).hexdigest()}
        self.assertEqual(json.loads(ref_path.read_text(encoding="utf8")), expected_ref)
        snapshot = (profile.read_bytes(), ref_path.read_bytes())
        self.assertEqual(run("--check").returncode, 0)
        self.assertEqual(run().returncode, 0)
        self.assertEqual((profile.read_bytes(), ref_path.read_bytes()), snapshot)
        profile.write_text(rendered.replace("Exact ёж policy.", "Altered"), encoding="utf8")
        tampered = profile.read_bytes()
        self.assertNotEqual(run("--check").returncode, 0)
        self.assertEqual(profile.read_bytes(), tampered)

    def test_generator_refuses_interpolation_before_any_mutation(self):
        script = ROOT / "tools/sync_profile_policy.py"
        self.assertTrue(script.is_file(), "The own-profile policy generator is not implemented")
        package = self.root / "unsafe"
        policy = package / "skills/eja-workflow/references/contract-review.md"
        profile = package / "agents/eja-methodology.md"
        policy.parent.mkdir(parents=True)
        profile.parent.mkdir(parents=True)
        policy.write_text("# Policy\n${KIMI_PLUGIN_ROOT}\n", encoding="utf8")
        profile.write_text("# Original child\n", encoding="utf8")
        before = profile.read_bytes()
        result = subprocess.run([sys.executable, "-B", str(script), "--root", str(package)],
                                capture_output=True, text=True, encoding="utf8")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(profile.read_bytes(), before)
        self.assertFalse(policy.with_name("contract-review.ref.json").exists())


if __name__ == "__main__":
    unittest.main()
