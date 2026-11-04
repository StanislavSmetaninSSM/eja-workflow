"""Scoped internal Stop audit. No network, user-message delivery or product writes."""
import argparse
from contextlib import contextmanager
from dataclasses import replace
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import time
import uuid

import audit_workflow as aw

TTL = 1800
MAX_FILE = 128 * 1024 * 1024
PHASES = {"check", "recheck"}


def namespace(home):
    """Keep metadata outside native journals and reject redirected state directories."""
    base = Path(home).resolve()
    root = base / "eja-workflow-audit"
    if root.resolve() != root or base == Path(base.anchor):
        raise ValueError("state_scope")
    return root


def ticket_path(home, ticket):
    """Validate the entire UUID before constructing any runtime filename."""
    if not isinstance(ticket, str) or str(uuid.UUID(ticket)) != ticket:
        raise ValueError("ticket_invalid")
    return namespace(home) / "tickets" / (ticket + ".json")


def report_path(home, ticket, phase):
    if phase not in PHASES:
        raise ValueError("phase_invalid")
    return ticket_path(home, ticket).with_suffix("." + phase + ".json")


def _write_new(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.resolve() != path:
        raise ValueError("redirected_output")
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write("\n")


def _read(path):
    if path.stat().st_size > 64 * 1024 or path.resolve() != path:
        raise ValueError("metadata_invalid")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("metadata_invalid")
    return value


def _version(root):
    return _read(Path(root).resolve() / "kimi.plugin.json")["version"]


def audit_command(root, ticket, phase, home):
    if phase not in PHASES or str(uuid.UUID(ticket)) != ticket:
        raise ValueError("command_invalid")
    script = (Path(root).resolve() / "tools" / "internal_audit.py").as_posix()
    return f'python -B -X utf8 "{script}" {phase} --ticket {ticket} --home "{Path(home).resolve().as_posix()}"'


def issue_ticket(payload, root, home):
    session = payload.get("session_id")
    cwd = payload.get("cwd")
    if not isinstance(session, str) or not re.fullmatch(r"[\w-]+", session) or not isinstance(cwd, str) or not Path(cwd).is_absolute():
        raise ValueError("hook_identity_invalid")
    ticket = str(uuid.uuid4())
    _write_new(ticket_path(home, ticket), {"schema_version": 1, "issued_at": time.time(),
        "session_id": session, "workspace": cwd, "plugin_root": str(Path(root).resolve()), "version": _version(root)})
    return ticket


def _ticket(home, root, ticket):
    value = _read(ticket_path(home, ticket))
    if (not 0 <= time.time() - value["issued_at"] <= TTL
            or value["plugin_root"] != str(Path(root).resolve()) or value["version"] != _version(root)):
        raise ValueError("ticket_stale")
    return value


def _deny(reason):
    return {"hookSpecificOutput": {"permissionDecision": "deny", "permissionDecisionReason": reason}}


@contextmanager
def _phase_guard(home, ticket, phase):
    """Serialize own binding/repair publication without waiting on other calls."""
    path = ticket_path(home, ticket).with_suffix("." + phase + ".lock")
    if path.resolve() != path:
        raise ValueError("redirected_phase_lock")
    descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(descriptor)
    try:
        yield
    finally:
        path.unlink()


def _publish_repair(path, value):
    """Publish a complete private receipt atomically, without replacing a file."""
    if path.resolve() != path:
        raise ValueError("redirected_repair")
    descriptor, temporary = tempfile.mkstemp(prefix=".repair-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf8", newline="\n") as stream:
            json.dump(value, stream, ensure_ascii=False, sort_keys=True)
            stream.write("\n")
        os.link(temporary, path)
    finally:
        Path(temporary).unlink()


def _repair_invocation(payload, root, home, command):
    """Suggest one literal repair only for an unambiguous live unused own phase."""
    identities = set(re.findall(r'\b(check|recheck) --ticket ([a-f0-9-]{36}) --home "([^"\r\n]+)"', command))
    if len(identities) != 1:
        return None
    phase, ticket, declared_home = identities.pop()
    literal = audit_command(root, ticket, phase, home)
    if command == literal or Path(declared_home).resolve() != Path(home).resolve():
        return None  # A used/foreign literal is not a formatting error.
    value = _ticket(home, root, ticket)
    call = payload.get("tool_call_id")
    cwd = payload.get("cwd")
    if (payload.get("session_id") != value["session_id"]
            or not isinstance(call, str) or not 0 < len(call) <= 512 or "\0" in call
            or not isinstance(cwd, str) or not Path(cwd).is_absolute()
            or Path(cwd).resolve() != Path(value["workspace"]).resolve()):
        return None
    if phase == "recheck":
        _original_report(home, ticket)
    with _phase_guard(home, ticket, phase):
        binding = ticket_path(home, ticket).with_suffix("." + phase + ".binding.json")
        if binding.exists() or report_path(home, ticket, phase).exists():
            return None
        receipt = ticket_path(home, ticket).with_suffix("." + phase + ".repair.json")
        expected = {"schema_version": 1, "session_id": value["session_id"], "tool_call_id": call,
                    "phase": phase, "reason": "invocation_format"}
        if receipt.exists():
            previous = _read(receipt)
            if previous != expected or type(previous.get("schema_version")) is not int:
                return None
        else:
            _publish_repair(receipt, expected)
    return _deny("EJA_AUDIT_REPAIR: неверная форма собственного вызова; команда не выполнялась. "
                 "Доступно одно исправление формы для этой фазы. Выполни ровно последнюю строку, "
                 "без перенаправления, повтора, оболочки или дополнительных команд. "
                 "Это не новое согласие и не изменение срока ticket.\n" + literal)


def bind_call(payload, root, home):
    """Bind only our fixed audit invocation. Never run or log arbitrary shell text."""
    if payload.get("tool_name") != "Bash" or not isinstance(payload.get("tool_input"), dict):
        return {}
    command = payload["tool_input"].get("command")
    if not isinstance(command, str):
        return {}
    script = (Path(root).resolve() / "tools" / "internal_audit.py").as_posix()
    invocation = f'python -B -X utf8 "{script}" '
    if not command.startswith(invocation):
        return {}
    try:
        match = re.search(r'\b(check|recheck) --ticket ([a-f0-9-]{36}) --home "[^"]+"$', command)
        if not match:
            raise ValueError("invocation_invalid")
        phase, ticket = match.groups()
        value = _ticket(home, root, ticket)
        if (command != audit_command(root, ticket, phase, home) or payload.get("session_id") != value["session_id"]
                or not isinstance(payload.get("tool_call_id"), str) or not payload["tool_call_id"]):
            raise ValueError("binding_invalid")
        if phase == "recheck":
            _original_report(home, ticket)
        with _phase_guard(home, ticket, phase):
            if report_path(home, ticket, phase).exists():
                raise ValueError("phase_consumed")
            binding = ticket_path(home, ticket).with_suffix("." + phase + ".binding.json")
            _write_new(binding, {"tool_call_id": payload["tool_call_id"], "session_id": value["session_id"]})
        return {}
    except (OSError, ValueError, KeyError, TypeError):
        try:
            repair = _repair_invocation(payload, root, home, command)
            if repair:
                return repair
        except (OSError, ValueError, KeyError, TypeError):
            pass
        return _deny("Eja Workflow: собственный вызов аудитора не связан с действующим уникальным ticket; не выполняй его повторно или через оболочку.")


def _session(home, session):
    sessions = Path(home).resolve() / "sessions"
    matches = [p / session for p in sessions.iterdir() if p.is_dir() and (p / session).is_dir()]
    if len(matches) != 1 or not matches[0].resolve().is_relative_to(sessions):
        raise ValueError("session_not_unique")
    return matches[0]


def _records(wire):
    if wire.stat().st_size > MAX_FILE:
        raise ValueError("journal_too_large")
    with wire.open("rb") as stream:
        number = 0
        while raw := stream.readline(aw.MAX_LINE + 1):
            number += 1
            if len(raw) > aw.MAX_LINE:
                raise ValueError("record_too_large")
            if raw.strip():
                yield number, json.loads(raw)


def _safe_wire(scope, agent):
    wire = scope / "agents" / agent / "wire.jsonl"
    if wire.resolve() != wire or not wire.resolve().is_relative_to(scope.resolve()):
        raise ValueError("journal_scope")
    if wire.stat().st_size > MAX_FILE:
        raise ValueError("journal_too_large")
    return wire


def _locate(scope, call_id, command):
    matches = []
    for agent in (scope / "agents").iterdir():
        if not re.fullmatch(r"main|agent-[0-9]+", agent.name) or not agent.is_dir():
            continue
        wire = _safe_wire(scope, agent.name)
        for _, row in _records(wire):
            event = row.get("event") if isinstance(row, dict) and row.get("type") == "context.append_loop_event" else None
            if isinstance(event, dict) and event.get("type") == "tool.call" and event.get("toolCallId") == call_id:
                if event.get("name") != "Bash" or not isinstance(event.get("args"), dict) or event["args"].get("command") != command:
                    raise ValueError("call_identity_mismatch")
                matches.append((agent.name, wire))
    return matches


def _skill_steering(wire, records, turn_id, prompt_id):
    """Allow only correlated native model-tool Skill activations, without payloads."""
    if not records:
        return
    agent = wire.parent.name
    trace = aw.load_log(wire)
    pending = None
    used_messages, used_activations, used_calls = set(), set(), set()
    calls = [r for r in records if r["kind"] == "tool.call"]
    results = [r for r in records if r["kind"] == "tool.result"]
    origin_keys = {"kind", "trigger", "inTurn", "activationId", "skillName"}
    optional_origin_keys = {"skillPath", "skillSource"}

    def origin_valid(origin):
        return (isinstance(origin, dict) and origin_keys <= set(origin) <= origin_keys | optional_origin_keys
            and origin["kind"] == "skill_activation" and origin["trigger"] == "model-tool"
            and origin["inTurn"] is True and isinstance(origin["activationId"], str)
            and bool(origin["activationId"]) and isinstance(origin["skillName"], str)
            and bool(origin["skillName"]) and all(isinstance(origin[key], str) and bool(origin[key])
                for key in optional_origin_keys if key in origin))

    for row in records:
        kind = row["kind"]
        if kind == "prompt.steered":
            mid = row.get("messageId")
            if (pending is not None or not isinstance(mid, str) or not mid or mid in used_messages
                    or row.get("promptIds") != [mid] or row.get("agent") != agent
                    or not isinstance(prompt_id, str) or not prompt_id
                    or row.get("activePromptId") != prompt_id):
                raise ValueError("skill_steering_ambiguous")
            pending = {"event": row, "message": None}
        elif kind == "skill.message":
            if (pending is None or pending["message"] is not None or row.get("role") != "user"
                    or row.get("messageId") != pending["event"]["messageId"]
                    or row.get("agent") != agent or not origin_valid(row.get("origin"))):
                raise ValueError("skill_steering_origin")
            pending["message"] = row
        elif kind == "turn.steer":
            message = pending["message"] if pending else None
            event = pending["event"] if pending else None
            if (message is None or row.get("agent") != agent
                    or row.get("messageId") != event["messageId"]
                    or row.get("promptIds") != [event["messageId"]]
                    or aw.canonical_turn_id(row.get("turnId")) != turn_id
                    or not origin_valid(row.get("origin")) or row["origin"] != message["origin"]):
                raise ValueError("work_unit_steered")
            activation = row["origin"]["activationId"]
            skill = row["origin"]["skillName"]
            if activation in used_activations:
                raise ValueError("skill_steering_duplicate")
            matches = []
            for call in calls:
                loaded = [c for c in trace.calls if c["id"] == call.get("id")
                          and c["name"] == "Skill" and c["skill"] == skill and c["line"] == call["line"]]
                paired = [r for r in results if r.get("id") == call.get("id")]
                if len(loaded) != 1 or len(paired) != 1:
                    continue
                result = paired[0]
                proof = trace.results.get(call["id"], {})
                if (call["line"] < event["line"] < result["line"] < message["line"] < row["line"]
                        and call.get("agent") == result.get("agent") == agent
                        and isinstance(call.get("uuid"), str) and bool(call["uuid"])
                        and result.get("parentUuid") == call["uuid"]
                        and aw.canonical_turn_id(call.get("turnId")) == turn_id
                        and ("turnId" not in result or aw.canonical_turn_id(result["turnId"]) == turn_id)
                        and proof.get("line") == result["line"] and not proof.get("error")
                        and proof.get("loaded_skill") == skill):
                    matches.append(call["id"])
            if len(matches) != 1 or matches[0] in used_calls:
                raise ValueError("skill_steering_correlation")
            used_calls.add(matches[0])
            used_messages.add(event["messageId"])
            used_activations.add(activation)
            pending = None
    if pending is not None:
        raise ValueError("skill_steering_incomplete")


def _scope(wire, call_id, stop_command):
    """Bind to a kernel task; Stop can start another actor run inside that task."""
    active, prompt_id, start, stops, found = None, None, 0, [], None
    prompt_agent = None
    steering = []
    for number, row in _records(wire):
        if not isinstance(row, dict):
            raise ValueError("boundary_unsupported")
        kind = row.get("type")
        if kind in {"turn.prompt", "turn.ended"}:
            if found:
                raise ValueError("work_unit_changed")
            active = aw.canonical_turn_id(row.get("turnId")) if kind == "turn.prompt" else None
            prompt_id = row.get("promptId") if kind == "turn.prompt" else None
            prompt_agent = row.get("agentId") if kind == "turn.prompt" else None
            if prompt_agent is not None and prompt_agent != wire.parent.name:
                raise ValueError("boundary_agent_mismatch")
            start, stops = number, []
            steering = []
        message = row.get("message")
        origin = message.get("origin", {}) if isinstance(message, dict) else {}
        if not isinstance(origin, dict):
            raise ValueError("origin_unsupported")
        if stops and origin.get("kind") == "user":
            raise ValueError("work_unit_steered")
        if (kind == "context.append_message" and isinstance(message, dict) and message.get("role") == "user"
                and origin == {"kind": "system_trigger", "name": "stop_hook"}
                and isinstance(message.get("content"), list)
                and any(isinstance(p, dict) and p.get("type") == "text" and isinstance(p.get("text"), str)
                    and stop_command in p["text"] for p in message["content"])):
            stops.append(number)
        event = row.get("event")
        if stops:
            base = {"line": number, "agent": row.get("agentId")}
            if kind in {"prompt.steered", "turn.steer"}:
                steering.append({**base, "kind": kind, **{key: row.get(key) for key in
                    ("messageId", "promptIds", "activePromptId", "turnId", "origin")}})
            elif kind == "context.append_message" and origin.get("kind") == "skill_activation":
                steering.append({**base, "kind": "skill.message", "messageId": message.get("id"),
                    "role": message.get("role"), "origin": origin})
            elif kind == "context.append_loop_event" and isinstance(event, dict):
                if event.get("type") == "tool.result" or (event.get("type") == "tool.call" and event.get("name") == "Skill"):
                    steering.append({**base, "kind": event["type"], "id": event.get("toolCallId"),
                        **{key: event[key] for key in ("uuid", "parentUuid", "turnId") if key in event}})
        if kind == "context.append_loop_event" and isinstance(event, dict) and event.get("type") == "tool.call" and event.get("toolCallId") == call_id:
            if (not isinstance(active, int) or isinstance(active, bool) or len(stops) != 1
                    or ("turnId" in event and aw.canonical_turn_id(event["turnId"]) != active)):
                raise ValueError("stop_origin_unavailable")
            found = {"turn_id": active, "start_line": start, "stop_line": stops[0]}
    if found is None or len(stops) != 1:
        raise ValueError("stop_origin_unavailable")
    if any(row["kind"] == "prompt.steered" for row in steering) and prompt_agent != wire.parent.name:
        raise ValueError("skill_steering_prompt_agent_unavailable")
    _skill_steering(wire, steering, found["turn_id"], prompt_id)
    return found


def _open_task(wire):
    active, start = None, 0
    for number, row in _records(wire):
        if not isinstance(row, dict):
            raise ValueError("boundary_unsupported")
        if row.get("type") == "turn.prompt":
            active, start = aw.canonical_turn_id(row.get("turnId")), number
        elif row.get("type") == "turn.ended":
            if active is not None and aw.canonical_turn_id(row.get("turnId")) != active:
                raise ValueError("boundary_mismatch")
            active = None
    return active is not None, start


def _audit_available(report):
    availability = report.get("audit_availability")
    return availability is None or (isinstance(availability, dict) and availability.get("status") == "AVAILABLE")


def _original_report(home, ticket):
    """Require a supported own checkpoint report before binding or repairing recheck."""
    report = _read(report_path(home, ticket, "check"))
    scope = report.get("scope")
    findings = report.get("findings")
    statuses = {"PASS", "FAIL", "UNVERIFIED"}
    if (type(report.get("schema_version")) is not int or report["schema_version"] != 1
            or report.get("formal_status") not in statuses or report.get("workflow_status") != "NOT_PROVEN"
            or not isinstance(report.get("role"), str) or not report["role"]
            or not isinstance(report.get("agent_id"), str) or not re.fullmatch(r"main|agent-[0-9]+", report["agent_id"])
            or report.get("boundary") != "pre_completion_tool" or report.get("task_completion") != "PENDING"
            or not isinstance(scope, dict) or any(type(scope.get(k)) is not int for k in ("turn_id", "start_line", "stop_line"))
            or scope["turn_id"] < 0 or not 0 < scope["start_line"] < scope["stop_line"]
            or not isinstance(findings, list) or not findings
            or any(not isinstance(f, dict) or not isinstance(f.get("rule"), str) or not f["rule"]
                   or f.get("status") not in statuses or not isinstance(f.get("evidence"), list) for f in findings)
            or not isinstance(report.get("manual_coverage"), list)
            or any(not isinstance(item, str) for item in report["manual_coverage"])
            or not _audit_available(report)):
        raise ValueError("original_unavailable")
    return report


def _unknown(reason, availability="identity_unavailable"):
    return {"schema_version": 1, "formal_status": "UNVERIFIED", "workflow_status": "NOT_PROVEN",
        "audit_availability": {"status": "UNAVAILABLE", "reason": availability},
        "task_completion": "PENDING", "boundary": "pre_completion_tool", "findings": [
            {"rule": reason, "status": "UNVERIFIED", "evidence": []}], "manual_coverage": ["identity_or_prefix_unavailable"]}


def _checkpoint(wire, call_id, scope, home=None, session=None):
    trace = aw.load_log(wire)
    unavailable = aw.evidence_availability(trace)
    if unavailable:
        return _unknown("audit_evidence_unavailable", unavailable)
    pending = {c["id"] for c in trace.calls} - set(trace.results)
    if (not trace.has_metadata or not trace.has_prompt or trace.ended or pending != {call_id}
            or not trace.calls or trace.calls[-1]["id"] != call_id
            or any(i != "unmatched_calls" for i in trace.issues)):
        return _unknown("checkpoint_prefix_unavailable")
    view = replace(trace, calls=trace.calls[:-1], issues=[])
    current_calls = [c for c in view.calls if c["line"] > scope["start_line"]]
    report = aw.audit(view, "reviewer", guard_home=home, guard_session=session)
    report["role"] = "observed_review_or_coordination"
    for finding in report["findings"]:
        if finding["rule"] == "evidence_complete":
            finding["rule"], finding["status"] = "checkpoint_prefix", "PASS"
        elif finding["status"] == "UNVERIFIED" and finding["rule"] != "review_inline_delivery":
            finding["status"] = "FAIL"
    def loaded(skill, before=float("inf")):
        return any(c["name"] == "Skill" and c["skill"] == skill and trace.results.get(c["id"], {}).get("loaded_skill") == skill
            and not trace.results[c["id"]]["error"] and trace.results[c["id"]]["line"] < before for c in view.calls)
    first = current_calls[0] if current_calls else None
    entry_limit = (current_calls[1]["line"] if len(current_calls) > 1 else trace.calls[-1]["line"]) if first and first["name"] == "Skill" and first["skill"] == aw.ENTRY else first["line"] if first else trace.calls[-1]["line"]
    for finding in report["findings"]:
        if finding["rule"] == "entry_first":
            finding["status"] = "PASS" if loaded(aw.ENTRY, entry_limit) else "FAIL"
    code = [c["line"] for c in current_calls if c["code"]]
    if code:
        markers = aw.EXECUTION | {aw.IMPLEMENTATION["implement"]}
        assigned = bool(view.assignment and view.assignment["role"] == "implementation")
        implementation = assigned or any(c["name"] == "Skill" and c["skill"] in markers
            and trace.results.get(c["id"], {}).get("loaded_skill") == c["skill"]
            and not trace.results[c["id"]]["error"] for c in current_calls)
        if implementation:
            report["role"] = "observed_assigned_executor" if assigned else "observed_executor"
            if not assigned:
                for rule, ok in [("execution_before_code", any(loaded(s, min(code)) for s in aw.EXECUTION)),
                                 ("tdd_before_code", loaded(aw.IMPLEMENTATION["tdd"], min(code))),
                                 ("bridge_before_code", loaded("spec-kit-superpowers-bridge", min(code)))]:
                    report["findings"].append({"rule": rule, "status": "PASS" if ok else "FAIL", "evidence": []})
        else:
            report["role"] = "observed_code_writes_role_unverified"
            report["findings"].append({"rule": "code_write_role_applicability", "status": "UNVERIFIED", "evidence": []})
        fresh = any(f["rule"] == "verification_before_check" and f["status"] == "PASS"
                    for f in aw.phase_evidence(view, current_calls))
        report["findings"].append({"rule": "fresh_verification_evidence", "status": "PASS" if fresh else "FAIL", "evidence": []})
    if wire.parent.name == "main" and any(c["name"] == "Agent" for c in current_calls):
        child_view = replace(view, calls=[c for c in view.calls if c["name"] != "Agent" or c in current_calls])
        children = aw.audit(child_view, "implementation", guard_home=home, guard_session=session)
        if children.get("audit_availability"):
            report["audit_availability"] = children["audit_availability"]
        report["findings"].extend(f for f in children["findings"] if f["rule"].startswith("child_"))
        report["findings"].extend(f for f in children["findings"] if f["rule"] == "delegation_refused_before_execution")
    report["scope"] = scope
    report["task_completion"] = "PENDING"
    report["boundary"] = "pre_completion_tool"
    report["manual_coverage"].extend(["full_phase_applicability", "final_turn_completion", "actual_verification_commands"])
    swarm_calls = [c for c in current_calls if c["name"] == "AgentSwarm"]
    if swarm_calls:
        report["findings"].append({"rule": "swarm_coverage", "status": "UNVERIFIED",
                                   "evidence": [{"source": "provided/wire.jsonl", "line": c["line"]} for c in swarm_calls]})
        report["manual_coverage"].append("native_agent_swarm_lifecycle")
    statuses = {f["status"] for f in report["findings"]}
    report["formal_status"] = "FAIL" if "FAIL" in statuses else "UNVERIFIED" if "UNVERIFIED" in statuses else "PASS"
    if not _audit_available(report):
        report["formal_status"] = "UNVERIFIED"
    return report


def _recovery(original, current, wire, first_call):
    if not _audit_available(original) or not _audit_available(current):
        return "UNAVAILABLE"
    if original["formal_status"] != "FAIL":
        return "NOT_APPLICABLE"
    if any(f["status"] == "UNVERIFIED" for f in current["findings"]):
        return "UNRESOLVED"
    trace = aw.load_log(wire)
    boundary = next((c["line"] for c in trace.calls if c["id"] == first_call), float("inf"))
    fresh_check = any(c["name"] == "Bash" and not c.get("own_audit")
        and c["line"] > boundary and c["id"] in trace.results
        and not trace.results[c["id"]]["error"] for c in trace.calls)
    failed = {f["rule"] for f in current["findings"] if f["status"] == "FAIL"}
    late = {"entry_first": {aw.ENTRY}, "execution_before_code": aw.EXECUTION,
            "tdd_before_code": {aw.IMPLEMENTATION["tdd"]}}
    for rule in list(failed):
        if rule in late and any(c["name"] == "Skill" and c["skill"] in late[rule] and c["id"] in trace.results
                and trace.results[c["id"]].get("loaded_skill") == c["skill"] and not trace.results[c["id"]]["error"] for c in trace.calls):
            failed.remove(rule)
    if fresh_check and not failed and current["formal_status"] != "UNVERIFIED":
        return "FORMAL_RECOVERED_LATE"
    return "UNRESOLVED"


def check_ticket(ticket, phase, root, home, wait_seconds=4):
    """Return one independent report through the invoking native tool's own stdout."""
    try:
        value = _ticket(home, root, ticket)
        binding = _read(ticket_path(home, ticket).with_suffix("." + phase + ".binding.json"))
        if binding["session_id"] != value["session_id"]:
            raise ValueError("binding_scope")
        _write_new(ticket_path(home, ticket).with_suffix("." + phase + ".claim.json"), {"claimed": True})
        scope = _session(home, value["session_id"])
        deadline = time.monotonic() + min(max(wait_seconds, 0), 5)
        while True:
            try:
                matches = _locate(scope, binding["tool_call_id"], audit_command(root, ticket, phase, home))
            except (FileNotFoundError, json.JSONDecodeError, UnicodeError):
                matches = []
            if matches or time.monotonic() >= deadline:
                break
            time.sleep(0.05)
        if len(matches) != 1:
            raise ValueError("caller_not_unique")
        agent, wire = matches[0]
        work_scope = _scope(wire, binding["tool_call_id"], audit_command(root, ticket, "check", home))
        report = _checkpoint(wire, binding["tool_call_id"], work_scope, home, value["session_id"])
        report["agent_id"] = agent
        if phase == "recheck":
            original = _original_report(home, ticket)
            first = _read(ticket_path(home, ticket).with_suffix(".check.binding.json"))
            if original.get("agent_id") != agent or original.get("scope") != work_scope:
                raise ValueError("recheck_caller_changed")
            report["original_formal_status"] = original["formal_status"]
            report["recovery_status"] = _recovery(original, report, wire, first["tool_call_id"])
        elif _audit_available(report):
            report["next_action"] = audit_command(root, ticket, "recheck", home)
        _write_new(report_path(home, ticket, phase), report)
        return report
    except (OSError, ValueError, KeyError, TypeError, RecursionError):
        return _unknown("ticket_or_caller_unavailable")


def stop_hook(payload, root, home):
    """Request one native internal continuation; never submit a conversation message."""
    try:
        scope = _session(home, payload["session_id"])
        active_work = False
        for agent in (scope / "agents").iterdir():
            if not re.fullmatch(r"main|agent-[0-9]+", agent.name):
                continue
            wire = _safe_wire(scope, agent.name)
            trace = aw.load_log(wire)
            task_open, start = _open_task(wire)
            if task_open:
                unavailable = aw.evidence_availability(trace)
                if unavailable:
                    print("Eja Workflow audit unavailable: " + unavailable, file=sys.stderr)
                    return {}
            if task_open and trace.assignment and trace.task_start_line == start:
                active_work = True
            if task_open and any(c["code"] or c.get("code_read") or c.get("symbol_path")
                    or c["name"] in {"Agent", "AgentSwarm", "Bash"}
                    or c["skill"] in set(aw.IMPLEMENTATION.values()) | aw.EXECUTION for c in trace.calls if c["line"] > start):
                active_work = True
        if not active_work:
            return {}
        ticket = issue_ticket(payload, root, home)
        command = audit_command(root, ticket, "check", home)
        return _deny("Внутренний контроль Eja Workflow; это не сообщение/согласие пользователя. "
            "Для обычного разговора верни обычный содержательный ответ. "
            "При ожидании решения пользователя последний ответ целиком содержит сам вопрос, уже предложенные варианты, "
            "контекст необходимого решения и ссылки на spec. Сохрани существенный текст вопроса в последнем ответе. "
            "Ожидание решения не требует новых инженерных фаз или noop-команды. "
            "Для завершённой рабочей задачи вызови сейчас Bash ровно с командой ниже (сам, без делегирования/обёртки). "
            "Отчёт вернётся тебе как результат инструмента. Исправь применимые пропуски только в исходном согласованном объёме; "
            "Если инструмент отклонил форму вызова с EJA_AUDIT_REPAIR, один раз выполни его точную последнюю строку. "
            "При audit_availability.status=UNAVAILABLE сохрани исходный результат и заверши без recheck, "
            "восстановления фаз, установки зависимостей или правок проекта/vendor/глобальных настроек ради аудита. "
            "не выдумывай согласие, RED или новый план, не переписывай продукт ради истории. "
            "При FAIL/UNVERIFIED обработай причину и выполни один next_action recheck из отчёта. "
            "Поздние исправления сохраняют исходный FAIL; необратимый порядок не пересоздавай. "
            "Финальный ответ должен самостоятельно доставить исходный результат задачи: находки, решения, проверки, "
            "открытые вопросы и ссылки на артефакты. Сохрани содержательность прежнего результата; фраза «см. выше» "
            "и отдельная сводка аудита его не заменяют: приложение возвращает родителю только последний ответ. "
            "Ревьюер обновляет полный result_artifact в ранее разрешённом scratch; при запрете записей возвращает полный отчёт inline. "
            "Статус ограниченной проверки процесса сопровождает результат, не доказывает всё соблюдение. "
            "Порядок последнего ответа: исходный результат/находки, проверки, открытые вопросы, затем краткая диагностика процесса. "
            "Не отправляй служебных пользовательских сообщений и не используй conversations.send.\n" + command)
    except (OSError, ValueError, KeyError, TypeError):
        print("Eja Workflow audit unavailable: journal_unavailable", file=sys.stderr)
        return {}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("stop", "pretool", "check", "recheck"))
    parser.add_argument("--ticket")
    parser.add_argument("--home", type=Path)
    args = parser.parse_args()
    try:
        if args.operation in PHASES:
            if args.home is None:
                raise ValueError("home_required")
            root, home = Path(__file__).resolve().parents[1], args.home.resolve()
            result = check_ticket(args.ticket, args.operation, root, home)
        else:
            root, home = Path(os.environ["KIMI_PLUGIN_ROOT"]).resolve(), Path(os.environ["KIMI_CODE_HOME"]).resolve()
            raw = sys.stdin.buffer.read(2 * 1024 * 1024 + 1)
            if len(raw) > 2 * 1024 * 1024:
                raise ValueError("input_too_large")
            payload = json.loads(raw)
            if not isinstance(payload, dict):
                raise ValueError("input_invalid")
            result = (stop_hook if args.operation == "stop" else bind_call)(payload, root, home)
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except (OSError, ValueError, KeyError, TypeError):
        print(json.dumps(_unknown("runtime_unavailable") if args.operation in PHASES else {}))
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
