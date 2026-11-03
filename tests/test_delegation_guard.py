"""Behavioral contract for opt-in native delegation refusals."""
import concurrent.futures
import copy
import hashlib
import importlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))


def make_packet(role="review", **changes):
    """Build a structural handoff; its claims are not evidence of actual work."""
    packet = {"schema_version": 1, "role": role,
              "original_request": "Review PRIVATE_SOURCE", "approved_contract": "Read-only review",
              "approval_source": "User explicitly requested review", "canonical_artifacts": ["N/A: read-only"],
              "write_roots": [str(ROOT)], "verification": ["Inspect original contract"],
              "methodology": {"entry": "eja-workflow:eja-workflow", "using": "superpowers:using-superpowers",
                              "child_guide": str(ROOT / "agents" / "eja-methodology.md"),
                              "phase_skills": ["superpowers:verification-before-completion"]},
              "serena": {"status": "ready", "project": str(ROOT), "server": "serena",
                         "languages": ["python"], "evidence": "find_symbol: example.py/example"},
              "contract_review": (ROOT / "skills/eja-workflow/references/contract-review.md").read_text(encoding="utf8")}
    if role == "implementation":
        packet["methodology"]["phase_skills"] += ["superpowers:executing-plans", "superpowers:test-driven-development"]
    packet.update(changes)
    return "EJA_HANDOFF_V1\n" + json.dumps(packet, ensure_ascii=False) + "\nEND_EJA_HANDOFF\nReview the assigned module."


class DelegationGuardTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue((ROOT / "tools" / "delegation_guard.py").exists(), "delegation guard is not implemented")
        self.guard = importlib.import_module("delegation_guard")
        self.contract = importlib.import_module("delegation_contract")
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name) / "home"
        self.workspace = Path(self.temp.name) / "проект"
        self.workspace.mkdir()
        self.state = self.home / "eja-workflow-guard"
        self.state.mkdir(parents=True)
        self.now = time.time()
        self.config = {"schema_version": 1, "enabled": True, "workspace_root": str(self.workspace), "expires_at": self.now + 1800}
        self.write_config()
        self.payload = {"hook_event_name": "PreToolUse", "session_id": "session-é", "tool_call_id": "call1",
                        "cwd": str(self.workspace), "tool_name": "Agent",
                        "tool_input": {"subagent_type": "eja-methodology", "prompt": make_packet()}}

    def write_config(self):
        (self.state / "target.json").write_text(json.dumps(self.config), encoding="utf8")

    def run_guard(self, payload=None):
        return self.guard.pretool(self.payload if payload is None else payload, self.home, self.now)

    def denial(self, result):
        self.assertEqual(set(result), {"hookSpecificOutput"})
        output = result["hookSpecificOutput"]
        self.assertEqual(set(output), {"permissionDecision", "permissionDecisionReason"})
        self.assertEqual(output["permissionDecision"], "deny")
        self.assertTrue(output["permissionDecisionReason"].startswith("EJA_DELEGATION_GUARD:"))
        return output["permissionDecisionReason"]

    def test_valid_packet_and_agent_allow_without_writes(self):
        self.assertEqual(self.contract.parse_packet(make_packet())["role"], "review")
        self.assertEqual(self.run_guard(), {})
        self.assertEqual(list(self.state.iterdir()), [self.state / "target.json"])

    def test_packet_required_fields_types_and_roles(self):
        data = self.contract.parse_packet(make_packet())
        for key in data:
            changed = dict(data)
            changed.pop(key)
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.contract.parse_packet(self.envelope(changed))
        for changes in ({"schema_version": True}, {"role": "coder"}, {"write_roots": ["relative"]},
                        {"original_request": " "}, {"verification": "test"}, {"contract_review": []}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.contract.parse_packet(make_packet(**changes))

    @staticmethod
    def envelope(data):
        return "EJA_HANDOFF_V1\n" + json.dumps(data, ensure_ascii=False) + "\nEND_EJA_HANDOFF\nWork."

    def test_packet_bootstrap_serena_and_phase_requirements(self):
        for field, mutations in (("methodology", ({"entry": "wrong"}, {"child_guide": "relative"}, {"phase_skills": []})),
                                 ("serena", ({"status": "unknown"}, {"project": "relative"}, {"evidence": " "}, {"languages": [False]}))):
            for mutation in mutations:
                data = self.contract.parse_packet(make_packet())
                data[field].update(mutation)
                with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                    self.contract.parse_packet(self.envelope(data))
        data = self.contract.parse_packet(make_packet("implementation"))
        self.contract.parse_packet(self.envelope(data))
        data["methodology"]["phase_skills"].remove("superpowers:test-driven-development")
        with self.assertRaises(ValueError):
            self.contract.parse_packet(self.envelope(data))
        data = self.contract.parse_packet(make_packet())
        data["serena"]["status"] = "unavailable"
        self.contract.parse_packet(self.envelope(data))

    def test_packet_malformed_json_unicode_and_limits(self):
        for prompt in (None, {}, "missing", make_packet().replace('"schema_version": 1', '"schema_version": 1, "schema_version": 1'),
                       make_packet().replace('"schema_version": 1', '"schema_version": NaN'),
                       make_packet() + "x" * 1048576, make_packet(original_request="x" * 65536),
                       make_packet(original_request="\ud800"), make_packet(write_roots=["x\0y"])):
            with self.subTest(prompt_type=type(prompt)), self.assertRaises(ValueError):
                self.contract.parse_packet(prompt)

    def test_unknown_optional_fields_cannot_hide_nonfinite_or_invalid_unicode(self):
        for extra in ('"extra": 1e999', '"extra": [1e999]', '"extra": "\\ud800"'):
            prompt = make_packet().replace('"schema_version": 1', '"schema_version": 1, ' + extra)
            with self.subTest(extra=extra), self.assertRaises(ValueError):
                self.contract.parse_packet(prompt)

    def test_receipt_verification_rejects_tampering_and_does_not_write(self):
        self.payload["tool_input"]["subagent_type"] = "coder"
        self.run_guard()
        path = next((self.state / "denials").glob("*.json"))
        original = json.loads(path.read_text(encoding="utf8"))
        for mutation in ({"schema_version": True}, {"reason_code": "anything"}, {"extra": "x"},
                         {"marker": "00000000-0000-0000-0000-000000000000"}, {"session_id": "other"}):
            path.write_text(json.dumps({**original, **mutation}), encoding="utf8")
            before = path.read_bytes()
            self.assertFalse(self.guard.verify_receipt(self.home, "session-é", "call1", original["input_sha256"], original["marker"]))
            self.assertEqual(path.read_bytes(), before)
        missing = self.home / "missing"
        self.assertFalse(self.guard.verify_receipt(missing, "s", "c", "0" * 64, original["marker"]))
        self.assertFalse(missing.exists())

    def test_receipt_identity_nul_and_json_values_are_rejected(self):
        self.payload["tool_input"]["subagent_type"] = "coder"
        for value in ("", "s\0x", "x" * 513, [], "\ud800"):
            self.assertIn("receipt_unavailable", self.denial(self.run_guard({**self.payload, "session_id": value})))
        self.assertFalse((self.state / "denials").exists())

    def test_symlinked_config_or_receipts_never_redirect_state(self):
        outside = Path(self.temp.name) / "outside.json"
        outside.write_text(json.dumps(self.config), encoding="utf8")
        target = self.state / "target.json"
        target.unlink()
        try:
            target.symlink_to(outside)
        except OSError as error:
            self.skipTest("Symlink creation unavailable: " + str(error))
        self.payload["tool_input"]["subagent_type"] = "coder"
        self.assertEqual(self.run_guard(), {})
        target.unlink()
        self.write_config()
        redirected = Path(self.temp.name) / "outside-receipts"
        redirected.mkdir()
        (self.state / "denials").symlink_to(redirected, target_is_directory=True)
        self.assertIn("receipt_unavailable", self.denial(self.run_guard()))
        self.assertEqual(list(redirected.iterdir()), [])

    def test_concurrent_conflicting_inputs_only_verify_winning_identity(self):
        self.payload["tool_input"]["subagent_type"] = "coder"
        left = copy.deepcopy(self.payload)
        right = copy.deepcopy(self.payload)
        right["tool_input"]["prompt"] = "another PRIVATE_SOURCE"
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(self.run_guard, [left, right] * 12))
        files = list((self.state / "denials").glob("*.json"))
        self.assertEqual(len(files), 1)
        receipt = json.loads(files[0].read_text(encoding="utf8"))
        for payload, result in zip([left, right] * 12, results):
            fingerprint = hashlib.sha256(json.dumps(payload["tool_input"], ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf8")).hexdigest()
            reason = self.denial(result)
            if fingerprint == receipt["input_sha256"]:
                self.assertIn(receipt["marker"], reason)
            else:
                self.assertIn("identity_conflict", reason)

    def test_escaped_nonfinite_unknown_packet_cannot_launch_in_scope(self):
        self.payload["tool_input"]["prompt"] = make_packet().replace('"schema_version": 1', '"schema_version": 1, "extra": 1e999')
        self.assertIn("invalid_packet", self.denial(self.run_guard()))

    def test_wrong_profile_missing_packet_and_unsupported_resume_deny(self):
        variants = [{"subagent_type": "coder"}, {"prompt": "plain task"}, {"resume": "old"},
                    {"fork": True}, {"resume_agent_ids": ["old"]}, {"resume": None}, {"fork": "false"}]
        for index, change in enumerate(variants):
            payload = copy.deepcopy(self.payload)
            payload["tool_call_id"] = str(index)
            payload["tool_input"].update(change)
            self.denial(self.run_guard(payload))

    def test_explicit_false_fork_remains_a_new_valid_call(self):
        self.payload["tool_input"]["fork"] = False
        self.assertEqual(self.run_guard(), {})

    def test_full_installed_review_protocol_required_before_launch(self):
        self.payload["tool_input"]["prompt"] = make_packet(contract_review="Read original contract and exact diff.")
        self.assertIn("invalid_packet", self.denial(self.run_guard()))
        self.payload["tool_call_id"] = "fresh"
        policy = (ROOT / "skills/eja-workflow/references/contract-review.md").read_text(encoding="utf8")
        self.payload["tool_input"]["prompt"] = make_packet(contract_review="Additional review assignment.\n" + policy.replace("\n", "\r\n"))
        self.assertEqual(self.run_guard(), {})

    def test_invalid_input_denies_and_bad_payload_does_not_crash(self):
        for args in (None, [], True, {"prompt": float("nan")}, {"prompt": "\ud800"}):
            payload = {**self.payload, "tool_input": args}
            self.denial(self.run_guard(payload))
        for payload in (None, [], {}, {**self.payload, "tool_name": "Bash"}, {**self.payload, "hook_event_name": "Stop"}):
            self.assertEqual(self.run_guard(payload) if payload is not None else self.guard.pretool(None, self.home, self.now), {})

    def test_missing_off_foreign_expired_and_malformed_scope_allow(self):
        for changes in ({"enabled": False}, {"workspace_root": str(ROOT)}, {"expires_at": self.now},
                        {"expires_at": self.now + 3601}, {"expires_at": True}, {"expires_at": float("inf")},
                        {"schema_version": True}, {"workspace_root": "relative"}):
            self.config.update(changes)
            self.write_config()
            self.assertEqual(self.run_guard(), {})
            self.config = {"schema_version": 1, "enabled": True, "workspace_root": str(self.workspace), "expires_at": self.now + 1800}
        (self.state / "target.json").unlink()
        self.assertEqual(self.run_guard(), {})
        (self.state / "target.json").write_text("[]", encoding="utf8")
        self.assertEqual(self.run_guard(), {})

    def test_swarm_validates_every_distinct_expanded_item(self):
        payload = {**self.payload, "tool_name": "AgentSwarm", "tool_input": {
            "subagent_type": "eja-methodology", "items": ["alpha", "beta"],
            "prompt_template": make_packet(original_request="Review {{item}}")}}
        self.assertEqual(self.run_guard(payload), {})
        for index, change in enumerate(({"items": ["same", "same"]}, {"items": ["only"]},
                                        {"items": [str(i) for i in range(129)]}, {"items": ["valid", "\"broken"]},
                                        {"items": [1, 2]}, {"prompt_template": make_packet()}, {"resume_agent_ids": []})):
            bad = copy.deepcopy(payload)
            bad["tool_call_id"] = str(index)
            bad["tool_input"].update(change)
            self.denial(self.run_guard(bad))

    def test_receipt_private_exact_idempotent_and_verifiable(self):
        self.payload["tool_input"]["subagent_type"] = "coder"
        first = self.denial(self.run_guard())
        self.assertEqual(self.denial(self.run_guard()), first)
        files = list((self.state / "denials").glob("*.json"))
        self.assertEqual(len(files), 1)
        raw = files[0].read_text(encoding="utf8")
        self.assertNotIn("PRIVATE_SOURCE", raw)
        self.assertNotIn(str(ROOT).replace("\\", "\\\\"), raw)
        receipt = json.loads(raw)
        self.assertEqual(set(receipt), {"schema_version", "session_id", "tool_call_id", "input_sha256", "reason_code", "marker"})
        self.assertTrue(self.guard.verify_receipt(self.home, "session-é", "call1", receipt["input_sha256"], receipt["marker"]))
        for args in (("other", "call1", receipt["input_sha256"], receipt["marker"]),
                     ("session-é", "call1", "0" * 64, receipt["marker"]),
                     ("session-é", "call1", receipt["input_sha256"], "invalid")):
            self.assertFalse(self.guard.verify_receipt(self.home, *args))

    def test_conflicting_identity_never_overwrites_receipt(self):
        self.payload["tool_input"]["subagent_type"] = "coder"
        self.run_guard()
        path = next((self.state / "denials").glob("*.json"))
        old = path.read_bytes()
        self.payload["tool_input"]["prompt"] = "different"
        self.assertIn("identity_conflict", self.denial(self.run_guard()))
        self.assertEqual(path.read_bytes(), old)

    def test_previously_denied_identity_cannot_be_reused_for_valid_launch(self):
        denied = copy.deepcopy(self.payload)
        denied["tool_input"]["subagent_type"] = "coder"
        self.run_guard(denied)
        path = next((self.state / "denials").glob("*.json"))
        before = path.read_bytes()
        self.assertIn("identity_conflict", self.denial(self.run_guard()))
        self.assertEqual(path.read_bytes(), before)

    def test_concurrent_duplicates_have_one_stable_receipt(self):
        self.payload["tool_input"]["subagent_type"] = "coder"
        with concurrent.futures.ThreadPoolExecutor(max_workers=12) as pool:
            results = list(pool.map(lambda _: self.run_guard(), range(36)))
        self.assertEqual(len({self.denial(result) for result in results}), 1)

    def test_missing_identity_and_redirected_or_oversized_receipts_are_unverified(self):
        self.payload["tool_input"]["subagent_type"] = "coder"
        self.assertIn("receipt_unavailable", self.denial(self.run_guard({**self.payload, "session_id": None})))
        self.run_guard()
        path = next((self.state / "denials").glob("*.json"))
        path.write_text("x" * 20000, encoding="utf8")
        self.assertIn("receipt_unavailable", self.denial(self.run_guard()))
        self.assertFalse(self.guard.verify_receipt(self.home, "session-é", "call1", "0" * 64, "invalid"))

    def test_cli_actual_stdin_configure_off_and_private_unicode(self):
        env = {**os.environ, "KIMI_CODE_HOME": str(self.home), "PYTHONIOENCODING": "utf-8"}
        script = str(ROOT / "tools" / "delegation_guard.py")
        command = [sys.executable, script]
        configured = subprocess.run(command + ["configure", "--workspace", str(self.workspace), "--ttl", "60"], env=env, capture_output=True, text=True, encoding="utf8")
        self.assertEqual(configured.returncode, 0, configured.stderr)
        self.payload["tool_input"]["subagent_type"] = "coder"
        checked = subprocess.run(command + ["pretool"], input=json.dumps(self.payload, ensure_ascii=False), env=env, capture_output=True, text=True, encoding="utf8")
        self.assertEqual(checked.returncode, 0, checked.stderr)
        self.denial(json.loads(checked.stdout))
        self.assertNotIn("PRIVATE_SOURCE", checked.stdout)
        rejected = subprocess.run(command + ["configure", "--workspace", str(self.workspace), "--ttl", "3601"], env=env, capture_output=True)
        self.assertNotEqual(rejected.returncode, 0)
        off = subprocess.run(command + ["off"], env=env, capture_output=True)
        self.assertEqual(off.returncode, 0)
        self.assertEqual(self.run_guard(), {})


if __name__ == "__main__":
    unittest.main()
