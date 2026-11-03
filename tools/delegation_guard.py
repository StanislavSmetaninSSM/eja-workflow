"""Opt-in exact-workspace native delegation guard and private denial receipts."""
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import sys
import tempfile
import time
import uuid

from delegation_contract import MAX_PROMPT_BYTES, absolute_path, parse_packet, strict_json

NAMESPACE = "eja-workflow-guard"
STATE_LIMIT = 16384
REASON_CODES = frozenset({"invalid_input", "unsupported_resume_or_fork", "profile_required", "invalid_packet", "invalid_swarm"})
RECEIPT_FIELDS = {"schema_version", "session_id", "tool_call_id", "input_sha256", "reason_code", "marker"}


def _safe_path(path):
    """Reject symlinks and Windows reparse points in every existing component."""
    path = Path(os.path.abspath(path))
    for part in (path, *path.parents):
        try:
            info = part.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400):
            raise ValueError("redirected state")
    resolved = str(path.resolve())
    # Windows may retain the extended-length prefix when a concurrent atomic
    # publication makes a previously absent component appear during resolve().
    if os.name == "nt" and resolved.startswith("\\\\?\\UNC\\"):
        resolved = "\\\\" + resolved[8:]
    elif os.name == "nt" and resolved.startswith("\\\\?\\"):
        resolved = resolved[4:]
    if Path(resolved) != path:
        raise ValueError("redirected state")
    return path


def _read(path):
    path = _safe_path(path)
    info = path.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_size > STATE_LIMIT:
        raise ValueError("invalid state size or kind")
    with path.open("rb") as stream:
        before = os.fstat(stream.fileno())
        if (before.st_dev, before.st_ino) != (info.st_dev, info.st_ino):
            raise ValueError("state changed")
        raw = stream.read(STATE_LIMIT + 1)
    _safe_path(path)
    if len(raw) > STATE_LIMIT:
        raise ValueError("state too large")
    value = strict_json(raw.decode("utf8"))
    if not isinstance(value, dict):
        raise ValueError("state object required")
    return value


def _scope(payload, home, now):
    try:
        root = _safe_path(Path(home) / NAMESPACE)
        config = _read(root / "target.json")
        expiry = config.get("expires_at")
        if (set(config) != {"schema_version", "enabled", "workspace_root", "expires_at"}
                or type(config.get("schema_version")) is not int or config["schema_version"] != 1
                or config.get("enabled") is not True or type(expiry) not in (int, float)
                or not math.isfinite(expiry) or type(now) not in (int, float) or not math.isfinite(now)
                or not now < expiry <= now + 3600):
            return None
        target = _safe_path(absolute_path(config.get("workspace_root")))
        cwd = _safe_path(absolute_path(payload.get("cwd")))
        return root if target == cwd else None
    except (OSError, ValueError, TypeError, RuntimeError, OverflowError):
        return None


def _identifier(value):
    if not isinstance(value, str) or not 0 < len(value) <= 512 or "\0" in value:
        raise ValueError("caller unavailable")
    value.encode("utf8")
    return value


def _fingerprint(args):
    return hashlib.sha256(json.dumps(args, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf8")).hexdigest()


def _receipt_path(home, session, call):
    _identifier(session)
    _identifier(call)
    key = hashlib.sha256((session + "\0" + call).encode("utf8")).hexdigest()
    return _safe_path(Path(home) / NAMESPACE / "denials" / (key + ".json"))


def _valid_receipt(value):
    if (set(value) != RECEIPT_FIELDS or type(value.get("schema_version")) is not int or value["schema_version"] != 1
            or value.get("reason_code") not in REASON_CODES):
        raise ValueError("invalid receipt")
    _identifier(value.get("session_id"))
    _identifier(value.get("tool_call_id"))
    if not isinstance(value.get("input_sha256"), str) or not re.fullmatch("[0-9a-f]{64}", value["input_sha256"]):
        raise ValueError("invalid input hash")
    marker = value.get("marker")
    if not isinstance(marker, str) or str(uuid.UUID(marker)) != marker or uuid.UUID(marker).version != 4:
        raise ValueError("invalid receipt marker")


def verify_receipt(home, session_id, call_id, input_sha256, marker) -> bool:
    """Read only an exact safe receipt; never create or repair verification state."""
    try:
        value = _read(_receipt_path(home, session_id, call_id))
        _valid_receipt(value)
        return (value["session_id"] == session_id and value["tool_call_id"] == call_id
                and value["input_sha256"] == input_sha256 and value["marker"] == marker)
    except (OSError, ValueError, TypeError, UnicodeError, RuntimeError, OverflowError):
        return False


def _receipt(root, payload, code):
    session, call = payload.get("session_id"), payload.get("tool_call_id")
    path = _receipt_path(root.parent, session, call)
    fixed = {"schema_version": 1, "session_id": session, "tool_call_id": call,
             "input_sha256": _fingerprint(payload.get("tool_input")), "reason_code": code}
    value = {**fixed, "marker": str(uuid.uuid4())}
    folder = _safe_path(path.parent)
    folder.mkdir(exist_ok=True)
    # Publish complete bytes atomically, without replacement. Parallel callers cannot
    # read a half-written receipt or overwrite the winning identity.
    fd, temporary = tempfile.mkstemp(prefix=".receipt-", dir=folder)
    try:
        with os.fdopen(fd, "w", encoding="utf8") as stream:
            json.dump(value, stream, ensure_ascii=False, sort_keys=True)
            stream.write("\n")
        _safe_path(path)
        try:
            os.link(temporary, path)
        except FileExistsError:
            previous = _read(path)
            _valid_receipt(previous)
            if any(previous[key] != expected for key, expected in fixed.items()):
                return str(uuid.uuid4()), "identity_conflict"
            value = previous
        return value["marker"], code
    finally:
        os.unlink(temporary)


def _denied(marker, code):
    reason = ("EJA_DELEGATION_GUARD:" + marker + "\n" + code + ": delegation refused before execution. "
              "Retry the same authorized task as a new eja-methodology child with a complete EJA_HANDOFF_V1 packet "
              "and original contract, canonical artifacts, allowed write roots, methodology and actual Serena status. "
              "Resume and fork are unsupported in this scope. This refusal grants no new approval or permissions.")
    return {"hookSpecificOutput": {"permissionDecision": "deny", "permissionDecisionReason": reason}}


def _validate_prompt(prompt):
    packet = parse_packet(prompt)
    if packet["role"] == "review":
        policy = _safe_path(Path(__file__).resolve().parents[1] / "skills" / "eja-workflow" / "references" / "contract-review.md")
        with policy.open("rb") as stream:
            raw = stream.read(65537)
        if len(raw) > 65536:
            raise ValueError("review protocol unavailable")
        expected = raw.decode("utf8").replace("\r\n", "\n").strip()
        actual = packet["contract_review"].replace("\r\n", "\n")
        if not expected or expected not in actual:
            raise ValueError("full installed review protocol required")


def _reason(tool, args):
    if not isinstance(args, dict):
        return "invalid_input"
    # False explicitly describes a new context. Resume fields are conservatively
    # unsupported even when empty, rather than guessing native fallback semantics.
    if ("resume" in args or "resume_agent_ids" in args
            or ("fork" in args and args["fork"] is not False)):
        return "unsupported_resume_or_fork"
    if args.get("subagent_type") != "eja-methodology":
        return "profile_required"
    if tool == "Agent":
        try:
            _validate_prompt(args.get("prompt"))
        except (ValueError, OSError):
            return "invalid_packet"
    else:
        items, template = args.get("items"), args.get("prompt_template")
        if (not isinstance(items, list) or not 2 <= len(items) <= 128
                or not all(isinstance(item, str) and item.strip() for item in items)
                or len(set(items)) != len(items) or not isinstance(template, str)
                or "{{item}}" not in template or len(template.encode("utf8")) > MAX_PROMPT_BYTES):
            return "invalid_swarm"
        try:
            for item in items:
                _validate_prompt(template.replace("{{item}}", item))
        except (ValueError, OSError):
            return "invalid_packet"
    return None


def pretool(payload, home: Path, now: float) -> dict:
    """Allow unchanged outside opt-in scope; refuse structurally invalid delegations."""
    if (not isinstance(payload, dict) or payload.get("hook_event_name") != "PreToolUse"
            or payload.get("tool_name") not in ("Agent", "AgentSwarm")):
        return {}
    root = _scope(payload, home, now)
    if root is None:
        return {}
    try:
        fingerprint = _fingerprint(payload.get("tool_input"))
        code = _reason(payload["tool_name"], payload.get("tool_input"))
        if code is None:
            # A corrected retry needs a new native call ID. A denied identity
            # cannot be repurposed to launch different input under old evidence.
            try:
                previous = _read(_receipt_path(home, payload.get("session_id"), payload.get("tool_call_id")))
            except FileNotFoundError:
                return {}
            _valid_receipt(previous)
            if previous["input_sha256"] != fingerprint:
                return _denied(str(uuid.uuid4()), "identity_conflict")
            return _denied(previous["marker"], previous["reason_code"])
        marker, code = _receipt(root, payload, code)
    except (OSError, ValueError, TypeError, UnicodeError, RecursionError, RuntimeError, OverflowError):
        marker, code = str(uuid.uuid4()), "receipt_unavailable"
    return _denied(marker, code)


def _configure(home, workspace, ttl):
    absolute_path(workspace)
    if not 0 < ttl <= 3600 or not math.isfinite(ttl):
        raise ValueError("ttl must be positive and at most 3600 seconds")
    root = _safe_path(Path(home) / NAMESPACE)
    root.mkdir(parents=True, exist_ok=True)
    target = _safe_path(root / "target.json")
    state = {"schema_version": 1, "enabled": True, "workspace_root": str(_safe_path(workspace)), "expires_at": time.time() + ttl}
    fd, temporary = tempfile.mkstemp(prefix=".target-", dir=root)
    try:
        with os.fdopen(fd, "w", encoding="utf8") as stream:
            json.dump(state, stream, ensure_ascii=False, sort_keys=True)
        _safe_path(target)
        os.replace(temporary, target)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("pretool")
    configure = commands.add_parser("configure")
    configure.add_argument("--workspace", required=True)
    configure.add_argument("--ttl", type=float, default=3600)
    commands.add_parser("off")
    args = parser.parse_args()
    try:
        home = Path(os.environ["KIMI_CODE_HOME"])
        if args.command == "configure":
            _configure(home, args.workspace, args.ttl)
        elif args.command == "off":
            target = _safe_path(home / NAMESPACE / "target.json")
            target.unlink(missing_ok=True)
        else:
            raw = sys.stdin.buffer.read(2 * MAX_PROMPT_BYTES + 1)
            if len(raw) > 2 * MAX_PROMPT_BYTES:
                raise ValueError("hook payload too large")
            print(json.dumps(pretool(strict_json(raw.decode("utf8")), home, time.time()), ensure_ascii=True))
    except (OSError, ValueError, TypeError, KeyError, RuntimeError, RecursionError, OverflowError):
        if args.command == "pretool":
            # An unreadable outer payload cannot establish the exact opt-in scope.
            print("{}")
        else:
            parser.exit(2, "Guard configuration rejected: invalid or unsafe runtime state.\n")


if __name__ == "__main__":
    main()
