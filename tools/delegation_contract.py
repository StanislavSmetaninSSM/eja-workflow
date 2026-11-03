"""Structural handoff validation; packet claims never establish actual readiness."""
import json
import math
from pathlib import Path

MAX_PROMPT_BYTES = 1024 * 1024
MAX_ENVELOPE_BYTES = 64 * 1024
EXECUTION_SKILLS = {"superpowers:executing-plans", "superpowers:subagent-driven-development"}


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def strict_json(raw):
    """Decode JSON with duplicate keys and nonfinite constants rejected."""
    def reject(_):
        raise ValueError("nonfinite JSON constant")
    try:
        value = json.loads(raw, object_pairs_hook=_object, parse_constant=reject)
        pending = [value]
        while pending:
            item = pending.pop()
            if isinstance(item, float) and not math.isfinite(item):
                raise ValueError("nonfinite JSON number")
            if isinstance(item, str):
                item.encode("utf8")
            elif isinstance(item, dict):
                pending.extend(item.keys())
                pending.extend(item.values())
            elif isinstance(item, list):
                pending.extend(item)
        return value
    except (RecursionError, UnicodeError) as error:
        raise ValueError("invalid JSON") from error


def _text(value):
    if not isinstance(value, str) or not value.strip() or "\0" in value:
        raise ValueError("nonempty text required")
    try:
        value.encode("utf8")
    except UnicodeError as error:
        raise ValueError("invalid Unicode") from error
    return value


def _texts(value):
    if not isinstance(value, list) or not value:
        raise ValueError("nonempty string list required")
    for item in value:
        _text(item)
    return value


def absolute_path(value):
    """Require a nonempty native absolute path without resolving or touching it."""
    _text(value)
    if not Path(value).is_absolute():
        raise ValueError("absolute path required")
    return value


def lexical_path(value):
    """Validate a native absolute lexical path without filesystem access."""
    absolute_path(value)
    path = Path(value)
    if (".." in path.parts or any(ord(c) < 32 for c in value)
            or any(":" in part for part in path.parts[1:])):
        raise ValueError("ambiguous path")
    return path


def _review_delivery(packet):
    delivery = packet.get("result_delivery")
    if delivery == "inline":
        if (packet.get("write_roots") != [] or packet.get("result_artifact", "missing") is not None
                or packet.get("artifact_unavailable_reason") != "user_forbids_writes"):
            raise ValueError("inline review requires explicit no-write contract")
    elif delivery == "artifact":
        path = lexical_path(packet.get("result_artifact"))
        roots = [lexical_path(root) for root in _texts(packet.get("write_roots"))]
        if (path.suffix.lower() != ".md" or ".work" not in [p.lower() for p in path.parts]
                or not any(path != root and path.is_relative_to(root) for root in roots)):
            raise ValueError("review artifact must be a markdown file in permitted .work scratch")
    else:
        raise ValueError("review result delivery required")


def parse_packet(prompt: str) -> dict:
    """Return a validated v1 envelope, or raise ValueError for structural failure."""
    try:
        _text(prompt)
        if len(prompt.encode("utf8")) > MAX_PROMPT_BYTES:
            raise ValueError("prompt too large")
        lines = prompt.splitlines(keepends=True)
        if not lines or lines[0].rstrip("\r\n") != "EJA_HANDOFF_V1":
            raise ValueError("handoff start required")
        end = next((i for i in range(1, len(lines)) if lines[i].rstrip("\r\n") == "END_EJA_HANDOFF"), None)
        if end is None:
            raise ValueError("handoff end required")
        raw = "".join(lines[1:end])
        if len(raw.encode("utf8")) > MAX_ENVELOPE_BYTES:
            raise ValueError("handoff envelope too large")
        packet = strict_json(raw)
        if not isinstance(packet, dict) or type(packet.get("schema_version")) is not int or packet["schema_version"] != 1:
            raise ValueError("unsupported handoff version")
        role = packet.get("role")
        if role not in ("implementation", "review", "investigation"):
            raise ValueError("unsupported handoff role")
        for field in ("original_request", "approved_contract", "approval_source"):
            _text(packet.get(field))
        for field in ("canonical_artifacts", "verification"):
            _texts(packet.get(field))
        if role == "review":
            _review_delivery(packet)
        else:
            _texts(packet.get("write_roots"))
        for root in packet["write_roots"]:
            absolute_path(root)
        methodology, serena = packet.get("methodology"), packet.get("serena")
        if not isinstance(methodology, dict) or not isinstance(serena, dict):
            raise ValueError("methodology and Serena objects required")
        if methodology.get("entry") != "eja-workflow:eja-workflow" or methodology.get("using") != "superpowers:using-superpowers":
            raise ValueError("methodology bootstrap required")
        absolute_path(methodology.get("child_guide"))
        phases = _texts(methodology.get("phase_skills"))
        if role in ("review", "implementation") and "superpowers:verification-before-completion" not in phases:
            raise ValueError("verification phase required")
        if role == "implementation" and (not EXECUTION_SKILLS.intersection(phases) or "superpowers:test-driven-development" not in phases):
            raise ValueError("execution and TDD phases required")
        if serena.get("status") not in ("ready", "unavailable"):
            raise ValueError("Serena status required")
        absolute_path(serena.get("project"))
        _text(serena.get("server"))
        _texts(serena.get("languages"))
        _text(serena.get("evidence"))
        if role == "review":
            _text(packet.get("contract_review"))
        return packet
    except (TypeError, KeyError, UnicodeError, RecursionError) as error:
        raise ValueError("invalid handoff") from error
