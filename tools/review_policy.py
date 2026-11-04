"""Canonical policy content and exact versioned references, without agent claims."""
import hashlib
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
POLICY_RELATIVE = Path("skills/eja-workflow/references/contract-review.md")
MAX_POLICY_BYTES = 65536


def policy_text(root=None):
    """Read bounded UTF-8 policy, normalized to LF and no outer whitespace."""
    try:
        with ((Path(root) if root is not None else ROOT) / POLICY_RELATIVE).open("rb") as stream:
            raw = stream.read(MAX_POLICY_BYTES + 1)
        if len(raw) > MAX_POLICY_BYTES:
            return ""
        return raw.decode("utf8").replace("\r\n", "\n").strip()
    except (OSError, UnicodeError):
        return ""


def policy_reference(root=None):
    """Return the exact current reference; an absent policy is not a reference."""
    text = policy_text(root)
    if not text:
        raise ValueError("canonical review policy unavailable")
    return {"version": 1, "sha256": hashlib.sha256(text.encode("utf8")).hexdigest()}


def valid_policy_reference(value, root=None):
    """Require exactly the current version/content, with no bool or extra keys."""
    try:
        return (isinstance(value, dict) and set(value) == {"version", "sha256"}
                and type(value["version"]) is int and value["version"] == 1
                and isinstance(value["sha256"], str)
                and re.fullmatch(r"[0-9a-f]{64}", value["sha256"]) is not None
                and value == policy_reference(root))
    except ValueError:
        return False


def unnumber(text):
    """Remove only the native Read tool's decimal-line TAB prefixes."""
    return re.sub(r"(?m)^\d+\t", "", text.replace("\r\n", "\n"))


def contains_policy(output, root=None):
    """Recognize complete contiguous policy in a legacy packet/tool wrapper."""
    text = policy_text(root)
    return bool(text and isinstance(output, str) and text in unnumber(output))


def complete_policy_read(output, root=None):
    """Recognize exactly the complete canonical Read body, never a fragment."""
    text = policy_text(root)
    return bool(text and isinstance(output, str) and unnumber(output).strip() == text)
