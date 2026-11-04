"""Synchronize own profile policy and reference metadata; never a runtime hook."""
import argparse
import json
from pathlib import Path
import sys

from review_policy import POLICY_RELATIVE, ROOT, policy_reference, policy_text

BEGIN = "<!-- EJA_REVIEW_POLICY_BEGIN -->"
END = "<!-- EJA_REVIEW_POLICY_END -->"


def synchronize(root, check=False):
    """Render deterministic content; check mode performs no filesystem mutation."""
    root = Path(root)
    policy = policy_text(root)
    if not policy or "${" in policy or BEGIN in policy or END in policy:
        raise ValueError("policy unavailable or unsafe native interpolation/section marker")
    reference = policy_reference(root)
    profile_path = root / "agents/eja-methodology.md"
    profile = profile_path.read_text(encoding="utf8")
    section = (BEGIN + "\n\n## Полный канонический протокол ревью\n\n"
               + "contract_review_ref: " + json.dumps(reference, sort_keys=True) + "\n\n"
               + policy + "\n\n" + END)
    if BEGIN in profile or END in profile:
        if profile.count(BEGIN) != 1 or profile.count(END) != 1 or profile.index(END) < profile.index(BEGIN):
            raise ValueError("ambiguous generated profile section")
        start, stop = profile.index(BEGIN), profile.index(END) + len(END)
        expected_profile = profile[:start] + section + profile[stop:]
    else:
        separator = "" if profile.endswith("\n\n") else "\n" if profile.endswith("\n") else "\n\n"
        expected_profile = profile + separator + section + "\n"
    ref_path = (root / POLICY_RELATIVE).with_name("contract-review.ref.json")
    expected_ref = json.dumps(reference, indent=2, sort_keys=True) + "\n"
    actual_ref = ref_path.read_text(encoding="utf8") if ref_path.exists() else None
    matches = profile == expected_profile and actual_ref == expected_ref
    if check:
        return matches
    if profile != expected_profile:
        profile_path.write_text(expected_profile, encoding="utf8", newline="\n")
    if actual_ref != expected_ref:
        ref_path.write_text(expected_ref, encoding="utf8", newline="\n")
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    try:
        ok = synchronize(args.root, args.check)
        print("Policy profile/reference synchronized" if ok else "Policy profile/reference out of date")
        return 0 if ok else 1
    except (OSError, ValueError, UnicodeError) as error:
        print(str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
