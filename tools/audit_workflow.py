"""Read-only audit of observed Kimi Work actions; never certifies full compliance."""

import argparse
import hashlib
from dataclasses import dataclass, field
import json
from pathlib import Path
import re

from delegation_contract import parse_packet, lexical_path, implementation_intent
from delegation_guard import verify_receipt


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
                "eja-workflow:workflow-checkpoint",
                "superpowers:brainstorming", "superpowers:systematic-debugging",
                "superpowers:dispatching-parallel-agents", "superpowers:using-git-worktrees",
                "superpowers:diagnosing-superpowers", "superpowers:writing-skills",
                "speckit-clarify", "speckit-checklist", "speckit-constitution", "speckit-converge",
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
    from review_policy import policy_text
    return policy_text()


def _contains_policy(text):
    from review_policy import contains_policy
    return contains_policy(text)


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
    task_start_line: int = 0
    first_return_line: int = 0
    assignment: dict | None = None

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
    background = re.match(r"\Atask_id: [^\r\n]+\nstatus: running\nagent_id: agent-[0-9]+\n"
                          r"actual_subagent_type: [\w-]+\nautomatic_notification: true(?:\n|\Z)",
                          output.replace("\r\n", "\n"))
    from review_policy import complete_policy_read
    return {"loaded_skill": skill if skill in KNOWN_SKILLS else None,
            "error": bool(result.get("isError") or result.get("error")),
            "agent_id": agent.group(1) if agent else None,
            "custom_role": agent.group(2) == "eja-methodology" if agent else False,
            "completed_agent": agent.group(3) == "completed" if agent else False,
            "agent_envelope": bool(background or (agent and agent.group(3) in {"completed", "failed"})),
            "has_audit": _contains_policy(output), "policy_full": complete_policy_read(output)}


def _valid_mcp_health(row):
    """Recognize bounded native health metadata without granting task evidence."""
    kind = row.get("type")
    if kind not in ("mcp.server_failed", "mcp.server_failure_resolved"):
        return False
    required = {"type", "agentId", "serverName", "transport", "time"}
    if kind == "mcp.server_failure_resolved":
        required = required | {"resolution"}
    if not required <= row.keys():
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


def _symbol_result(output, expected_path):
    """Recognize nonempty native find_symbol evidence without retaining source bodies."""
    if not expected_path:
        return False
    try:
        primary, separator, extra = output.partition("\n<mcp-result-extras>\n")
        if separator:
            if not extra.endswith("\n</mcp-result-extras>"):
                return False
            metadata = json.loads(extra[:-len("\n</mcp-result-extras>")])
            if not isinstance(metadata, dict) or metadata.get("isError"):
                return False
        symbols = json.loads(primary)
        if not isinstance(symbols, list) or not symbols:
            return False
        for symbol in symbols:
            if not isinstance(symbol, dict):
                return False
            location = symbol.get("body_location")
            if (not isinstance(symbol.get("name_path"), str) or not symbol["name_path"].strip()
                    or not isinstance(symbol.get("kind"), str)
                    or symbol.get("relative_path") != expected_path
                    or not isinstance(location, dict)
                    or type(location.get("start_line")) is not int
                    or type(location.get("end_line")) is not int
                    or not 0 <= location["start_line"] <= location["end_line"]):
                return False
        return True
    except (ValueError, TypeError, RecursionError):
        return False


def phase_evidence(trace, calls=None):
    """Check current observed code access and checks against completed dependencies."""
    calls = [c for c in trace.calls if c["line"] > trace.task_start_line] if calls is None else calls
    accesses = [c for c in calls if c.get("code") or c.get("code_read") or c.get("symbol_path")]
    checks = [c for c in calls if c["name"] == "Bash" and not c.get("own_audit")]
    findings = []
    def finding(rule, ok, refs):
        findings.append({"rule": rule, "status": "PASS" if ok else "FAIL" if trace.complete else "UNVERIFIED",
                         "evidence": [{"source": "provided/wire.jsonl", "line": n} for n in refs]})
    if trace.assignment and trace.assignment["role"] == "implementation":
        first_code = min((c["line"] for c in calls if c.get("code")), default=float("inf"))
        first_return = trace.first_return_line or float("inf")
        for label, skills, limit in (
                ("execution", EXECUTION, min(first_code, first_return)),
                ("tdd", {"superpowers:test-driven-development"}, min(first_code, first_return)),
                ("bridge", {"spec-kit-superpowers-bridge"}, min(first_code, first_return)),
                ("verification_before_return", {VERIFY}, first_return)):
            receipts = []
            for call in trace.calls:
                result = trace.results.get(call["id"], {})
                if (call["name"] == "Skill" and call["skill"] in skills
                        and result.get("loaded_skill") == call["skill"] and not result.get("error", True)
                        and call.get("uuid") and result.get("parent_uuid") == call["uuid"]
                        and call.get("native_task") and result.get("native_task")
                        and result["line"] < limit):
                    receipts.append(result["line"])
            rule = "assigned_implementation_" + label
            if label == "verification_before_return" and not trace.first_return_line:
                findings.append({"rule": rule, "status": "UNVERIFIED", "evidence": []})
            else:
                finding(rule, bool(receipts), receipts)
    if trace.assignment and trace.assignment["role"] == "review":
        policy_reads = []
        policy_path = trace.assignment["policy_path"]
        for call in calls:
            result = trace.results.get(call["id"], {})
            if (call["name"] == "Read" and call.get("file_path") == policy_path
                    and call.get("uuid") and result.get("parent_uuid") == call["uuid"]
                    and call.get("native_task") and result.get("native_task")
                    and result.get("policy_full") and not result.get("error", True)):
                policy_reads.append(result["line"])
        finding("review_policy_received", bool(policy_reads), policy_reads)
        first_work = min((c["line"] for c in calls if c.get("review_work")), default=float("inf"))
        finding("review_policy_before_work", any(n < first_work for n in policy_reads), policy_reads)
        if trace.assignment["result_delivery"] == "inline":
            findings.append({"rule": "review_inline_delivery", "status": "UNVERIFIED", "evidence": []})
        else:
            persisted = []
            for call in calls:
                result = trace.results.get(call["id"], {})
                if (call.get("file_path") == trace.assignment["result_artifact"]
                        and call["name"] in ("Write", "Edit") and call.get("uuid")
                        and call.get("native_task") and result.get("native_task")
                        and result.get("parent_uuid") == call["uuid"] and not result.get("error", True)):
                    persisted.append(result["line"])
            finding("review_artifact_persisted", bool(persisted), persisted)
    phases = [c for c in calls if c["name"] == "Skill" and c["skill"] in KNOWN_SKILLS]
    if phases:
        violations = []
        for phase in phases:
            dependent = min((c["line"] for c in calls if c["line"] > phase["line"]
                             and c["name"] != "Skill" and not c.get("own_audit")), default=float("inf"))
            result = trace.results.get(phase["id"], {})
            if dependent != float("inf") and (result.get("loaded_skill") != phase["skill"]
                    or result.get("error") or result.get("line", float("inf")) >= dependent):
                violations.append(phase["line"])
        finding("phase_instruction_order", not violations, violations)
    if accesses:
        symbolic = [trace.results[c["id"]]["line"] for c in calls
                    if c.get("symbol_path") and c["id"] in trace.results
                    and trace.results[c["id"]].get("symbolic") and not trace.results[c["id"]]["error"]]
        finding("serena_symbolic_access", bool(symbolic), symbolic)
    if checks or accesses:
        writes = [c for c in calls if c.get("code")]
        last_write = max((trace.results.get(c["id"], {}).get("line", float("inf")) for c in writes), default=0)
        verified = []
        for check in checks:
            result = trace.results.get(check["id"], {})
            loaded = any(c["name"] == "Skill" and c["skill"] == VERIFY
                         and trace.results.get(c["id"], {}).get("loaded_skill") == VERIFY
                         and not trace.results[c["id"]]["error"]
                         and trace.results[c["id"]]["line"] < check["line"] for c in trace.calls)
            if result and not result["error"] and last_write < check["line"] and loaded:
                verified.append(check["line"])
        finding("verification_before_check", bool(verified), verified)
    return findings


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
                trace.task_start_line = number
                trace.first_return_line = 0
                trace.assignment = None
                trace.has_prompt = True
                trace.ended = False
                trace.kernel_ended = False
                try:
                    trace.kernel_turn_id = canonical_turn_id(row.get("turnId"))
                    trace.kernel_open = True
                    origin, inputs = row.get("origin"), row.get("input")
                    if (row.get("agentId") == path.parent.name
                            and re.fullmatch(r"agent-[0-9]+", path.parent.name)
                            and origin == {"kind": "system_trigger", "name": "subagent"}
                            and isinstance(inputs, list) and len(inputs) == 1
                            and isinstance(inputs[0], dict) and inputs[0].get("type") == "text"):
                        if implementation_intent(inputs[0].get("text")):
                            trace.assignment = {"role": "implementation"}
                        try:
                            packet = parse_packet(inputs[0].get("text"))
                            if packet["role"] == "review":
                                trace.assignment = {"role": "review", "result_delivery": packet["result_delivery"],
                                                    "policy_path": str(POLICY_PATH),
                                                    "result_artifact": str(lexical_path(packet["result_artifact"]))
                                                    if packet["result_delivery"] == "artifact" else None}
                        except ValueError:
                            pass
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
                if (trace.ended and trace.kernel_open and not trace.first_return_line
                        and row.get("agentId", path.parent.name) == path.parent.name):
                    trace.first_return_line = number
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
            native_task = row.get("agentId") == path.parent.name and trace.kernel_open
            try:
                if event_kind == "tool.call" or "turnId" in event:
                    native_task = native_task and canonical_turn_id(event.get("turnId")) == trace.kernel_turn_id
            except ValueError:
                native_task = False
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
                normalized_file = None
                if name in ("Read", "Write", "Edit"):
                    try:
                        normalized_file = str(lexical_path(file_path))
                    except (ValueError, TypeError):
                        pass
                prompt = args.get("prompt", "")
                if not isinstance(prompt, str):
                    prompt = ""
                packet = None
                if name == "Agent":
                    try:
                        packet = parse_packet(prompt)
                    except ValueError:
                        pass
                fingerprint = None
                if name in ("Agent", "AgentSwarm"):
                    try:
                        fingerprint = hashlib.sha256(json.dumps(args, ensure_ascii=False, sort_keys=True,
                            separators=(",", ":"), allow_nan=False).encode("utf8")).hexdigest()
                    except (ValueError, TypeError, UnicodeError):
                        trace.issues.append("unsupported_delegation_input")
                symbol_path = (args.get("relative_path") if name in {
                    "mcp__plugin-serena_serena__find_symbol", "mcp__serena__find_symbol"}
                    and isinstance(args.get("name_path_pattern"), str) and args["name_path_pattern"].strip()
                    and isinstance(args.get("relative_path"), str)
                    and Path(args["relative_path"]).suffix.lower() in CODE_SUFFIXES else None)
                command = args.get("command", "")
                own_audit = isinstance(command, str) and bool(re.fullmatch(
                    r'python -B -X utf8 "[^"\n]*[/\\]tools[/\\]internal_audit\.py" (?:check|recheck) '
                    r'--ticket [0-9a-f-]{36} --home "[^"\n]+"', command))
                preparation_read = False
                if name == "Read" and normalized_file:
                    read_path = Path(normalized_file)
                    parts = [p.lower() for p in read_path.parts]
                    preparation_read = (read_path.name.lower() == "agents.md"
                        or (read_path.suffix.lower() in {".md", ".json"}
                            and read_path.is_relative_to(POLICY_PATH.parents[2]))
                        or read_path in {POLICY_PATH.parents[3] / "system-prompt.md",
                                         POLICY_PATH.parents[3] / "agents/eja-methodology.md"}
                        or (read_path.name.lower() in {"spec.md", "plan.md", "tasks.md", "research.md",
                                "data-model.md", "quickstart.md", "constitution.md"}
                            and ("specs" in parts or ".specify" in parts)))
                review_work = (name in ("Write", "Edit", "Grep", "Glob")
                    or (name == "Read" and not preparation_read)
                    or (name == "Bash" and not own_audit)
                    or name.startswith(("mcp__serena__", "mcp__plugin-serena_serena__")))
                serena_write = (name.startswith(("mcp__serena__", "mcp__plugin-serena_serena__"))
                    and name.rsplit("__", 1)[-1] in {"replace_symbol_body", "insert_after_symbol", "insert_before_symbol",
                        "rename_symbol", "safe_delete_symbol", "replace_content", "create_text_file"}
                    and isinstance(file_path, str) and Path(file_path).suffix.lower() in CODE_SUFFIXES)
                trace.calls.append({"id": cid, "line": number, "name": name,
                                    "native_task": native_task,
                                    "uuid": event.get("uuid") if isinstance(event.get("uuid"), str) else None,
                                    "file_path": normalized_file,
                                    "review_work": review_work,
                                    "skill": skill if skill in KNOWN_SKILLS else None,
                                    "guide_read": name == "Read" and isinstance(file_path, str)
                                    and file_path.replace("\\", "/").endswith("references/child-agents.md"),
                                    "audit_read": name == "Read" and isinstance(file_path, str)
                                    and file_path.replace("\\", "/").endswith("references/contract-review.md"),
                                    "packet_audit": ((bool(packet.get("contract_review_ref")) or _contains_policy(packet.get("contract_review", ""))) if packet
                                                     else _contains_policy(prompt)) if name == "Agent" else False,
                                    "packet_serena": bool(packet),
                                    "input_sha256": fingerprint,
                                    "packet_entry": ENTRY in prompt and USING in prompt if name == "Agent" else False,
                                    "resume": bool(args.get("resume")),
                                    "fork": bool(args.get("fork")),
                                    "code_read": name == "Read" and isinstance(file_path, str)
                                    and Path(file_path).suffix.lower() in CODE_SUFFIXES,
                                    "symbol_path": symbol_path,
                                    "own_audit": own_audit,
                                    "code": serena_write or (name in ("Write", "Edit") and isinstance(file_path, str)
                                    and Path(file_path).suffix.lower() in CODE_SUFFIXES)})
            else:
                result = _result_projection(event.get("result"))
                if cid not in seen or cid in trace.results or result is None:
                    trace.issues.append("unsupported_result")
                    continue
                result["error"] = bool(result["error"] or event.get("isError") or event.get("error"))
                marker = re.match(r"\AEJA_DELEGATION_GUARD:([0-9a-f-]{36})\n", event["result"]["output"])
                result["guard_marker"] = marker.group(1) if marker else None
                result["parent_uuid"] = event.get("parentUuid") if isinstance(event.get("parentUuid"), str) else None
                result["native_task"] = native_task
                call = next((c for c in trace.calls if c["id"] == cid), None)
                result["symbolic"] = bool(call and call.get("symbol_path") and not result["error"]
                                          and _symbol_result(event["result"]["output"], call["symbol_path"]))
                if (call and call["name"] == "Skill" and call["skill"] in KNOWN_SKILLS
                        and not result["error"] and result["loaded_skill"] != call["skill"]):
                    trace.issues.append("unsupported_skill_result")
                if call and call["name"] == "Agent" and not result["error"] and not result["agent_envelope"]:
                    trace.issues.append("unsupported_agent_result")
                trace.results[cid] = {**result, "line": number}
    if seen != set(trace.results):
        trace.issues.append("unmatched_calls")
    return trace


def evidence_availability(trace):
    """Classify adapter/evidence limits without guessing an application update."""
    issues = set(trace.issues) - {"unmatched_calls"}
    if issues & {"invalid_record", "oversized_record", "invalid_envelope", "invalid_call", "missing_call_id"}:
        return "journal_invalid"
    if issues:
        return "unsupported_format"
    if not trace.has_metadata or not trace.has_prompt:
        return "journal_unavailable"
    return None


def audit(trace, role, child_root=None, review_agents=(), guard_home=None, guard_session=None):
    """Evaluate bounded formal rules, keeping semantic and shell coverage explicit."""
    findings = []
    child_unavailable = False

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
    findings.extend(phase_evidence(trace))
    if role in ("implementation", "delegator"):
        agent_calls = [c for c in trace.calls if c["name"] == "Agent"]
        considered = []
        for call in agent_calls:
            result = trace.results.get(call["id"], {})
            if (guard_home is not None and guard_session is not None and result.get("error")
                    and result.get("guard_marker") and call.get("input_sha256")
                    and call.get("uuid") and call["uuid"] == result.get("parent_uuid")
                    and verify_receipt(guard_home, guard_session, call["id"], call["input_sha256"], result["guard_marker"])):
                finding("delegation_refused_before_execution", True, [call["line"], result["line"]])
                continue
            considered.append(call)
            aid = result.get("agent_id")
            if not aid or not result.get("completed_agent") or result.get("error"):
                findings.append({"rule": "child_result", "status": "UNVERIFIED", "evidence": []})
                continue
            prefix = "child_" + aid + "_"
            finding(prefix + "custom_role", result["custom_role"], [result["line"]])
            finding(prefix + "entry_packet", call["packet_entry"], [call["line"]])
            finding(prefix + "serena_packet", call.get("packet_serena", False), [call["line"]])
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
                child_unavailable = child_unavailable or bool(child_report.get("audit_availability"))
                for child_finding in child_report["findings"]:
                    projected = {**child_finding, "rule": prefix + child_finding["rule"],
                                 "evidence": [{**ref, "source": aid + "/wire.jsonl"}
                                              for ref in child_finding["evidence"]]}
                    findings.append(projected)
            except (OSError, ValueError):
                child_unavailable = True
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
        if not considered:
            finding("independent_child", False)
        if role == "implementation":
            actual_ids = {trace.results.get(c["id"], {}).get("agent_id") for c in agent_calls}
            if not review_agents or not set(review_agents).issubset(actual_ids):
                findings.append({"rule": "review_agent_selection", "status": "UNVERIFIED", "evidence": []})
        if any(c["name"] == "AgentSwarm" for c in trace.calls):
            findings.append({"rule": "swarm_coverage", "status": "UNVERIFIED", "evidence": []})
    statuses = {f["status"] for f in findings}
    status = "FAIL" if "FAIL" in statuses else "UNVERIFIED" if "UNVERIFIED" in statuses else "PASS"
    report = {"schema_version": 1, "role": role, "formal_status": status,
            "workflow_status": "NOT_PROVEN", "findings": findings,
            "manual_coverage": ["consent_semantics", "code_correctness", "shell_and_other_mutations",
                                "natural_language_handoffs", "decision_table_meaning_and_order",
                                "native_spec_kit_artifact_steps", "tdd_red_green", "child_applicable_phases",
                                "source_authenticity_and_history_completeness",
                                "review_report_completeness_parent_full_read_and_final_delivery"]}
    unavailable = evidence_availability(trace) or ("child_evidence_unavailable" if child_unavailable else None)
    if unavailable:
        report["audit_availability"] = {"status": "UNAVAILABLE", "reason": unavailable}
        report["formal_status"] = "UNVERIFIED"
    return report


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
