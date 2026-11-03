"""Read-only audit of observed Kimi Work actions; never certifies full compliance."""

import argparse
from dataclasses import dataclass, field
import json
from pathlib import Path
import re


ENTRY = "eja-workflow:eja-workflow"
USING = "superpowers:using-superpowers"
VERIFY = "superpowers:verification-before-completion"
EXECUTION = {"superpowers:executing-plans", "superpowers:subagent-driven-development"}
IMPLEMENTATION = {
    "bridge": "spec-kit-superpowers-bridge",
    "specify": "speckit-specify",
    "writing_plans": "superpowers:writing-plans",
    "plan": "speckit-plan",
    "tasks": "speckit-tasks",
    "analyze": "speckit-analyze",
    "implement": "speckit-implement",
    "tdd": "superpowers:test-driven-development",
    "finishing": "superpowers:finishing-a-development-branch",
}
KNOWN_SKILLS = {ENTRY, USING, VERIFY, *EXECUTION, *IMPLEMENTATION.values(),
                "superpowers:requesting-code-review", "superpowers:receiving-code-review"}
CODE_SUFFIXES = {".py", ".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx", ".cs"}
MAX_LINE = 8 * 1024 * 1024
IGNORED_TYPES = {
    "metadata", "environment.set_binding", "mcp.tools_discovered", "profile.bind",
    "changeNotifier.snapshot", "permission.set_mode", "tools.set_active_tools",
    "tools.register_user_tool", "config.update", "turn.prompt", "agent.message.appended",
    "agent.turn.started", "llm.tools_snapshot", "llm.request", "usage.record",
    "token_counting.measured", "prompt.steered", "turn.steer", "tools.update_store",
    "file_history.tracked", "subagent.spawned", "subagent.started", "subagent.completed",
    "turn.ended", "token_counting.turn_recorded", "prompt.completed", "file_history.checkpoint",
}
POLICY_PATH = Path(__file__).resolve().parents[1] / "skills/eja-workflow/references/contract-review.md"


def canonical_turn_id(value):
    """Normalize the protocol's numeric prompt id and decimal-string loop id."""
    if isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= 9007199254740991:
        return value
    if isinstance(value, str) and len(value) <= 16 and re.fullmatch(r"0|[1-9][0-9]*", value):
        parsed = int(value)
        if parsed <= 9007199254740991:
            return parsed
    raise ValueError("unsupported_turn_id")


def _policy():
    try:
        return POLICY_PATH.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _contains_policy(text):
    policy = _policy()
    stripped = re.sub(r"(?m)^\d+\t", "", text.replace("\r\n", "\n"))
    return bool(policy and policy in stripped)


@dataclass
class Trace:
    """Only projected public evidence; payloads are discarded during parsing."""

    path: Path
    calls: list = field(default_factory=list)
    results: dict = field(default_factory=dict)
    issues: list = field(default_factory=list)
    has_metadata: bool = False
    has_prompt: bool = False
    ended: bool = False
    kernel_ended: bool = False
    kernel_turn_id: int | None = None
    kernel_open: bool = False

    @property
    def complete(self):
        """Whether the supported record stream has start/end and no parse gaps."""
        return self.has_metadata and self.has_prompt and self.ended and self.kernel_ended and not self.issues


def _result_projection(result):
    if not isinstance(result, dict) or not isinstance(result.get("output"), str):
        return None
    output = result["output"]
    match = re.fullmatch(r'Skill "([^"]+)" loaded inline\. Follow its instructions\.', output.strip())
    skill = match.group(1) if match else None
    agent = re.match(r"\Aagent_id: (agent-[0-9]+)\nactual_subagent_type: ([\w-]+)\nstatus: (\w+)\n",
                     output.replace("\r\n", "\n"))
    return {"loaded_skill": skill if skill in KNOWN_SKILLS else None,
            "error": bool(result.get("isError") or result.get("error")),
            "agent_id": agent.group(1) if agent else None,
            "custom_role": agent.group(2) == "eja-methodology" if agent else False,
            "completed_agent": agent.group(3) == "completed" if agent else False,
            "has_audit": _contains_policy(output)}


def _valid_mcp_health(row):
    """Recognize bounded native health metadata without granting task evidence."""
    kind = row.get("type")
    if kind not in ("mcp.server_failed", "mcp.server_failure_resolved"):
        return False
    required = {"type", "agentId", "serverName", "transport", "time"}
    allowed = required | ({"error"} if kind == "mcp.server_failed" else {"resolution"})
    if kind == "mcp.server_failure_resolved":
        required = required | {"resolution"}
    if not required <= row.keys() or row.keys() - allowed:
        return False
    if not all(isinstance(row[key], str) for key in ("agentId", "serverName")):
        return False
    if row["transport"] not in ("stdio", "http", "sse"):
        return False
    timestamp = row["time"]
    if type(timestamp) not in (int, float) or not 0 <= timestamp <= 9007199254740991:
        return False
    if kind == "mcp.server_failed":
        return "error" not in row or isinstance(row["error"], str)
    return row["resolution"] in ("recovered", "needs-auth", "disabled", "removed")


def load_log(path):
    """Stream the current native JSONL envelope without retaining private content."""
    path = Path(path)
    trace = Trace(path)
    seen = set()
    first = True
    with path.open("rb") as stream:
        number = 0
        while raw := stream.readline(MAX_LINE + 1):
            number += 1
            if len(raw) > MAX_LINE:
                trace.issues.append("oversized_record")
                while raw and not raw.endswith(b"\n"):
                    raw = stream.readline(MAX_LINE + 1)
                continue
            if not raw.strip():
                continue
            try:
                row = json.loads(raw.decode("utf-8"))
            except (ValueError, UnicodeError, RecursionError):
                trace.issues.append("invalid_record")
                continue
            if not isinstance(row, dict):
                trace.issues.append("invalid_envelope")
                continue
            kind = row.get("type")
            if first:
                trace.has_metadata = kind == "metadata"
                first = False
                if row.get("protocol_version") != "1.5":
                    trace.issues.append("unsupported_protocol")
            if kind in ("mcp.server_failed", "mcp.server_failure_resolved"):
                if not _valid_mcp_health(row):
                    trace.issues.append("unsupported_envelope")
                continue
            if kind == "turn.prompt":
                trace.has_prompt = True
                trace.ended = False
                trace.kernel_ended = False
                try:
                    trace.kernel_turn_id = canonical_turn_id(row.get("turnId"))
                    trace.kernel_open = True
                except ValueError:
                    trace.kernel_turn_id = None
                    trace.kernel_open = False
                    trace.issues.append("unsupported_turn_boundary")
            if kind == "turn.ended":
                try:
                    tid = canonical_turn_id(row.get("turnId"))
                    matches = trace.kernel_open and trace.kernel_turn_id == tid
                    trace.kernel_ended = row.get("reason") == "completed" and matches
                    if not matches:
                        trace.issues.append("unmatched_turn_end")
                    trace.kernel_open = False
                except ValueError:
                    trace.kernel_ended = False
                    trace.issues.append("unsupported_turn_boundary")
                continue
            if kind == "agent.turn.started":
                trace.ended = False
                if not trace.kernel_open:
                    trace.kernel_turn_id = None
                trace.kernel_ended = False
                continue
            if kind == "context.append_message":
                message = row.get("message")
                if not isinstance(message, dict):
                    trace.issues.append("unsupported_message")
                elif not isinstance(message.get("origin"), dict):
                    trace.issues.append("unsupported_origin")
                elif message["origin"].get("kind") in ("user", "system_trigger"):
                    # Child task delivery establishes stream start, never human consent.
                    trace.has_prompt = True
                    trace.ended = False
                    if not trace.kernel_open:
                        trace.kernel_turn_id = None
                    trace.kernel_ended = False
                continue
            if kind == "agent.turn.ended":
                trace.ended = row.get("outcome") == "done"
                continue
            if kind != "context.append_loop_event":
                if not isinstance(kind, str) or kind not in IGNORED_TYPES:
                    trace.issues.append("unsupported_envelope")
                continue  # Includes profile.bind: its content is never inspected.
            event = row.get("event")
            if not isinstance(event, dict):
                trace.issues.append("unsupported_event")
                continue
            event_kind = event.get("type")
            if event_kind not in ("tool.call", "tool.result"):
                if event_kind not in ("step.begin", "step.end", "content.part"):
                    trace.issues.append("unsupported_inner_event")
                continue  # Text/reasoning/catalog activation cannot establish skill use.
            trace.ended = False
            trace.kernel_ended = False
            cid = event.get("toolCallId")
            if not isinstance(cid, str) or not cid:
                trace.issues.append("missing_call_id")
                continue
            if event_kind == "tool.call":
                args = event.get("args")
                name = event.get("name")
                if cid in seen or not isinstance(args, dict) or not isinstance(name, str):
                    trace.issues.append("invalid_call")
                    continue
                seen.add(cid)
                skill = args.get("skill")
                if not isinstance(skill, str):
                    if name == "Skill" or skill is not None:
                        trace.issues.append("unsupported_skill")
                    skill = None
                file_path = args.get("path", args.get("file_path", args.get("relative_path", "")))
                if name in ("Read", "Write", "Edit") and (not isinstance(file_path, str) or not file_path):
                    trace.issues.append("unsupported_file_path")
                prompt = args.get("prompt", "")
                if not isinstance(prompt, str):
                    prompt = ""
                trace.calls.append({"id": cid, "line": number, "name": name,
                                    "skill": skill if skill in KNOWN_SKILLS else None,
                                    "guide_read": name == "Read" and isinstance(file_path, str)
                                    and file_path.replace("\\", "/").endswith("references/child-agents.md"),
                                    "audit_read": name == "Read" and isinstance(file_path, str)
                                    and file_path.replace("\\", "/").endswith("references/contract-review.md"),
                                    "packet_audit": _contains_policy(prompt) if name == "Agent" else False,
                                    "packet_entry": ENTRY in prompt and USING in prompt if name == "Agent" else False,
                                    "resume": bool(args.get("resume")),
                                    "fork": bool(args.get("fork")),
                                    "code": name in ("Write", "Edit") and isinstance(file_path, str)
                                    and Path(file_path).suffix.lower() in CODE_SUFFIXES})
            else:
                result = _result_projection(event.get("result"))
                if cid not in seen or cid in trace.results or result is None:
                    trace.issues.append("unsupported_result")
                    continue
                result["error"] = bool(result["error"] or event.get("isError") or event.get("error"))
                call = next((c for c in trace.calls if c["id"] == cid), None)
                if (call and call["name"] == "Skill" and call["skill"] in KNOWN_SKILLS
                        and not result["error"] and result["loaded_skill"] != call["skill"]):
                    trace.issues.append("unsupported_skill_result")
                trace.results[cid] = {**result, "line": number}
    if seen != set(trace.results):
        trace.issues.append("unmatched_calls")
    return trace


def audit(trace, role, child_root=None, review_agents=()):
    """Evaluate bounded formal rules, keeping semantic and shell coverage explicit."""
    findings = []

    def finding(rule, passed, refs=()):
        status = "PASS" if passed else "FAIL" if trace.complete else "UNVERIFIED"
        findings.append({"rule": rule, "status": status,
                         "evidence": [{"source": "provided/wire.jsonl", "line": n} for n in refs]})

    def loaded(skill, before=float("inf")):
        matches = []
        for call in trace.calls:
            result = trace.results.get(call["id"], {})
            if (call["name"] == "Skill" and call["skill"] == skill
                    and result.get("loaded_skill") == skill and not result.get("error")
                    and result["line"] < before):
                matches.append(result["line"])
        return matches

    findings.append({"rule": "evidence_complete", "status": "PASS" if trace.complete else "UNVERIFIED",
                     "evidence": []})
    first = trace.calls[0] if trace.calls else None
    first_result = trace.results.get(first["id"], {}) if first else {}
    next_call = trace.calls[1]["line"] if len(trace.calls) > 1 else float("inf")
    entry = bool(first and first["name"] == "Skill" and first["skill"] == ENTRY
                 and first_result.get("loaded_skill") == ENTRY and not first_result.get("error")
                 and first_result["line"] < next_call)
    entry_refs = [first["line"]] if first else []
    if first_result:
        entry_refs.append(first_result["line"])
    finding("entry_first", entry, entry_refs)
    finding("using_superpowers", bool(loaded(USING)), loaded(USING))
    code_lines = [c["line"] for c in trace.calls if c["code"]]
    before_code = min(code_lines, default=float("inf"))
    if role == "implementation":
        execution = [line for skill in EXECUTION for line in loaded(skill, before_code)]
        finding("execution_before_code", bool(execution), execution + code_lines[:1])
        for rule, skill in IMPLEMENTATION.items():
            limit = before_code if rule != "finishing" else float("inf")
            finding(rule, bool(loaded(skill, limit)), loaded(skill, limit))
        ordered = ["speckit-specify", "superpowers:writing-plans", "speckit-plan",
                   "speckit-tasks", "speckit-analyze", "speckit-implement"]
        positions = [min(loaded(s, before_code)) for s in ordered if loaded(s, before_code)]
        finding("planning_skill_order", len(positions) >= 2 and positions == sorted(positions), positions)
    finding("verification", bool(loaded(VERIFY)), loaded(VERIFY))
    if role in ("implementation", "delegator"):
        agent_calls = [c for c in trace.calls if c["name"] == "Agent"]
        for call in agent_calls:
            result = trace.results.get(call["id"], {})
            aid = result.get("agent_id")
            if not aid or not result.get("completed_agent") or result.get("error"):
                findings.append({"rule": "child_result", "status": "UNVERIFIED", "evidence": []})
                continue
            prefix = "child_" + aid + "_"
            finding(prefix + "custom_role", result["custom_role"], [result["line"]])
            finding(prefix + "entry_packet", call["packet_entry"], [call["line"]])
            guides = [c["line"] for c in trace.calls if c["guide_read"] and c["line"] < call["line"]
                      and c["id"] in trace.results and not trace.results[c["id"]]["error"]
                      and trace.results[c["id"]]["line"] < call["line"]]
            finding(prefix + "guide_before_dispatch", bool(guides), guides)
            if call["resume"] or call["fork"]:
                findings.append({"rule": prefix + "fresh_context", "status": "UNVERIFIED", "evidence": []})
            else:
                finding(prefix + "fresh_context", True, [call["line"]])
            root = Path(child_root) if child_root else trace.path.parent.parent
            child_path = root / aid / "wire.jsonl"
            try:
                if child_root is None and (root.name != "agents" or trace.path.parent.name != "main"):
                    raise ValueError("unsupported child layout")
                if (child_path.resolve().parent.parent != root.resolve()
                        or child_path.resolve().parent.name != aid):
                    raise ValueError("child scope")
                child_report = audit(load_log(child_path), "reviewer")
                for child_finding in child_report["findings"]:
                    projected = {**child_finding, "rule": prefix + child_finding["rule"],
                                 "evidence": [{**ref, "source": aid + "/wire.jsonl"}
                                              for ref in child_finding["evidence"]]}
                    findings.append(projected)
            except (OSError, ValueError):
                findings.append({"rule": prefix + "entry_first", "status": "UNVERIFIED", "evidence": []})
            if role == "delegator" or aid in review_agents:
                request = loaded("superpowers:requesting-code-review", call["line"])
                finding("review_request", bool(request), request)
                reads = [c for c in trace.calls if c["audit_read"] and c["id"] in trace.results
                         and trace.results[c["id"]]["has_audit"] and not trace.results[c["id"]]["error"]]
                dispatch_reads = [c["line"] for c in reads if trace.results[c["id"]]["line"] < call["line"]]
                finding("review_dispatch_audit", bool(dispatch_reads) and call["packet_audit"], dispatch_reads)
                next_code = min((n for n in code_lines if n > result["line"]), default=float("inf"))
                receiving = [n for n in loaded("superpowers:receiving-code-review", next_code)
                             if n > result["line"]]
                finding("review_receiving", bool(receiving), receiving)
                rereads = [c["line"] for c in reads if c["line"] > result["line"]
                           and trace.results[c["id"]]["line"] < next_code]
                finding("review_receipt_reread", bool(rereads), rereads)
        if not agent_calls:
            finding("independent_child", False)
        if role == "implementation":
            actual_ids = {trace.results.get(c["id"], {}).get("agent_id") for c in agent_calls}
            if not review_agents or not set(review_agents).issubset(actual_ids):
                findings.append({"rule": "review_agent_selection", "status": "UNVERIFIED", "evidence": []})
        if any(c["name"] == "AgentSwarm" for c in trace.calls):
            findings.append({"rule": "swarm_coverage", "status": "UNVERIFIED", "evidence": []})
    statuses = {f["status"] for f in findings}
    status = "FAIL" if "FAIL" in statuses else "UNVERIFIED" if "UNVERIFIED" in statuses else "PASS"
    return {"schema_version": 1, "role": role, "formal_status": status,
            "workflow_status": "NOT_PROVEN", "findings": findings,
            "manual_coverage": ["consent_semantics", "code_correctness", "shell_and_other_mutations",
                                "natural_language_handoffs", "decision_table_meaning_and_order",
                                "native_spec_kit_artifact_steps", "tdd_red_green", "child_applicable_phases",
                                "source_authenticity_and_history_completeness"]}


def _unverified(role, rule):
    return {"schema_version": 1, "role": role, "formal_status": "UNVERIFIED",
            "workflow_status": "NOT_PROVEN", "findings": [
                {"rule": rule, "status": "UNVERIFIED", "evidence": []}],
            "manual_coverage": ["input_or_output_unavailable"]}


def _render(report, as_json):
    if as_json:
        return json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    lines = ["Formal: " + report["formal_status"], "Workflow: NOT_PROVEN"]
    for finding in report["findings"]:
        refs = ", ".join(f'{ref["source"]}:{ref["line"]}' for ref in finding["evidence"])
        lines.append(f'{finding["status"]} {finding["rule"]}' + (" — " + refs if refs else ""))
    lines.append("Manual checks: " + ", ".join(report.get("manual_coverage", [])))
    return "\n".join(lines) + "\n"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wire", type=Path, required=True)
    parser.add_argument("--role", choices=("implementation", "delegator", "reviewer"), required=True)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--review-agent", action="append", default=[],
                        help="Explicit reviewer id for an implementation with mixed child roles.")
    parser.add_argument("--output", type=Path, help="Create a new report outside the journal tree.")
    args = parser.parse_args(argv)
    try:
        report = audit(load_log(args.wire), args.role, review_agents=args.review_agent)
    except (OSError, ValueError):
        report = _unverified(args.role, "input_unreadable")
    if args.output:
        try:
            if any(":" in part for part in args.output.parts if part != args.output.anchor):
                raise ValueError("alternate stream output")
            source = args.wire.resolve()
            journal_root = source.parent.parent.parent if source.parent.parent.name == "agents" else source.parent
            if args.output.resolve().is_relative_to(journal_root):
                raise ValueError("journal output")
            # Exclusive creation also refuses existing aliases, hard links and symlinks.
            with args.output.open("x", encoding="utf-8", newline="\n") as stream:
                stream.write(_render(report, args.json))
        except (OSError, ValueError):
            report = _unverified(args.role, "output_refused")
    print(_render(report, args.json), end="")
    return {"PASS": 0, "FAIL": 1, "UNVERIFIED": 2}[report["formal_status"]]


if __name__ == "__main__":
    raise SystemExit(main())
