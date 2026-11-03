"""Scoped internal Stop audit. No network, user-message delivery or product writes."""
import argparse
from dataclasses import replace
import json
import os
from pathlib import Path
import re
import sys
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
        if phase == "recheck" and not report_path(home, ticket, "check").exists():
            raise ValueError("original_missing")
        binding = ticket_path(home, ticket).with_suffix("." + phase + ".binding.json")
        _write_new(binding, {"tool_call_id": payload["tool_call_id"], "session_id": value["session_id"]})
        return {}
    except (OSError, ValueError, KeyError, TypeError):
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


def _scope(wire, call_id, stop_command):
    """Bind to a kernel task; Stop can start another actor run inside that task."""
    active, start, stops, found = None, 0, [], None
    for number, row in _records(wire):
        if not isinstance(row, dict):
            raise ValueError("boundary_unsupported")
        kind = row.get("type")
        if kind in {"turn.prompt", "turn.ended"}:
            if found:
                raise ValueError("work_unit_changed")
            active = aw.canonical_turn_id(row.get("turnId")) if kind == "turn.prompt" else None
            start, stops = number, []
        message = row.get("message")
        origin = message.get("origin", {}) if isinstance(message, dict) else {}
        if not isinstance(origin, dict):
            raise ValueError("origin_unsupported")
        if stops and (kind in {"prompt.steered", "turn.steer"} or origin.get("kind") == "user"):
            raise ValueError("work_unit_steered")
        if (kind == "context.append_message" and isinstance(message, dict) and message.get("role") == "user"
                and origin == {"kind": "system_trigger", "name": "stop_hook"}
                and isinstance(message.get("content"), list)
                and any(isinstance(p, dict) and p.get("type") == "text" and isinstance(p.get("text"), str)
                    and stop_command in p["text"] for p in message["content"])):
            stops.append(number)
        event = row.get("event")
        if kind == "context.append_loop_event" and isinstance(event, dict) and event.get("type") == "tool.call" and event.get("toolCallId") == call_id:
            if (not isinstance(active, int) or isinstance(active, bool) or len(stops) != 1
                    or ("turnId" in event and aw.canonical_turn_id(event["turnId"]) != active)):
                raise ValueError("stop_origin_unavailable")
            found = {"turn_id": active, "start_line": start, "stop_line": stops[0]}
    if found is None:
        raise ValueError("stop_origin_unavailable")
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


def _unknown(reason):
    return {"schema_version": 1, "formal_status": "UNVERIFIED", "workflow_status": "NOT_PROVEN",
        "task_completion": "PENDING", "boundary": "pre_completion_tool", "findings": [
            {"rule": reason, "status": "UNVERIFIED", "evidence": []}], "manual_coverage": ["identity_or_prefix_unavailable"]}


def _checkpoint(wire, call_id, scope):
    trace = aw.load_log(wire)
    pending = {c["id"] for c in trace.calls} - set(trace.results)
    if (not trace.has_metadata or not trace.has_prompt or trace.ended or pending != {call_id}
            or not trace.calls or trace.calls[-1]["id"] != call_id
            or any(i != "unmatched_calls" for i in trace.issues)):
        return _unknown("checkpoint_prefix_unavailable")
    view = replace(trace, calls=trace.calls[:-1], issues=[])
    current_calls = [c for c in view.calls if c["line"] > scope["start_line"]]
    report = aw.audit(view, "reviewer")
    report["role"] = "observed_executor" if any(c["code"] for c in current_calls) else "observed_review_or_coordination"
    for finding in report["findings"]:
        if finding["rule"] == "evidence_complete":
            finding["rule"], finding["status"] = "checkpoint_prefix", "PASS"
        elif finding["status"] == "UNVERIFIED":
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
        for rule, ok in [("execution_before_code", any(loaded(s, min(code)) for s in aw.EXECUTION)),
                         ("tdd_before_code", loaded(aw.IMPLEMENTATION["tdd"], min(code)))]:
            report["findings"].append({"rule": rule, "status": "PASS" if ok else "FAIL", "evidence": []})
        fresh = any(c["name"] == "Bash" and c["line"] > max(code) and c["id"] in trace.results
            and not trace.results[c["id"]]["error"] for c in current_calls)
        report["findings"].append({"rule": "fresh_verification_evidence", "status": "PASS" if fresh else "FAIL", "evidence": []})
    if wire.parent.name == "main" and any(c["name"] == "Agent" for c in current_calls):
        child_view = replace(view, calls=[c for c in view.calls if c["name"] != "Agent" or c in current_calls])
        children = aw.audit(child_view, "implementation")
        report["findings"].extend(f for f in children["findings"] if f["rule"].startswith("child_"))
    report["scope"] = scope
    report["task_completion"] = "PENDING"
    report["boundary"] = "pre_completion_tool"
    report["manual_coverage"].extend(["full_phase_applicability", "final_turn_completion", "actual_verification_commands"])
    statuses = {f["status"] for f in report["findings"]}
    report["formal_status"] = "FAIL" if "FAIL" in statuses else "UNVERIFIED" if "UNVERIFIED" in statuses else "PASS"
    return report


def _recovery(original, current, wire, first_call):
    if original["formal_status"] != "FAIL":
        return "NOT_APPLICABLE"
    if any(f["status"] == "UNVERIFIED" for f in current["findings"]):
        return "UNRESOLVED"
    trace = aw.load_log(wire)
    boundary = next((c["line"] for c in trace.calls if c["id"] == first_call), float("inf"))
    fresh_check = any(c["name"] == "Bash" and c["line"] > boundary and c["id"] in trace.results
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
        report = _checkpoint(wire, binding["tool_call_id"], work_scope)
        report["agent_id"] = agent
        if phase == "recheck":
            original = _read(report_path(home, ticket, "check"))
            first = _read(ticket_path(home, ticket).with_suffix(".check.binding.json"))
            if original.get("agent_id") != agent or original.get("scope") != work_scope:
                raise ValueError("recheck_caller_changed")
            report["original_formal_status"] = original["formal_status"]
            report["recovery_status"] = _recovery(original, report, wire, first["tool_call_id"])
        else:
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
            if task_open and any(c["code"] or c["name"] in {"Agent", "Bash"}
                    or c["skill"] in set(aw.IMPLEMENTATION.values()) | aw.EXECUTION for c in trace.calls if c["line"] > start):
                active_work = True
        if not active_work:
            return {}
        ticket = issue_ticket(payload, root, home)
        command = audit_command(root, ticket, "check", home)
        return _deny("Внутренний контроль Eja Workflow; это не сообщение/согласие пользователя. "
            "Если текущая задача — обычный разговор или ожидает решения пользователя, заверши без новой реплики и без инженерных фаз. "
            "Для завершённой рабочей задачи вызови сейчас Bash ровно с командой ниже (сам, без делегирования/обёртки). "
            "Отчёт вернётся тебе как результат инструмента. Исправь применимые пропуски только в исходном согласованном объёме; "
            "не выдумывай согласие, RED или новый план, не переписывай продукт ради истории. "
            "При FAIL/UNVERIFIED обработай причину и выполни один next_action recheck из отчёта. "
            "Поздние исправления сохраняют исходный FAIL; необратимый порядок не пересоздавай. "
            "Не публикуй отдельное сообщение аудита в чате и не используй conversations.send; обычный итог задачи достаточен.\n" + command)
    except (OSError, ValueError, KeyError, TypeError):
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
