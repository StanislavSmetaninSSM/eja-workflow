# Independent local audit

The auditor evaluates **observed successful tool calls**, separately from the agent's final account. It is a local Python standard-library command, not a native Work blocker or background service. Python 3.10+ is required; tested with Python 3.14 and Work 3.2.24.27 native journal protocol `1.5`.

```powershell
python -B tools/audit_workflow.py --wire "C:\path\to\conversation\agents\main\wire.jsonl" --role delegator
```

Choose the exact conversation yourself. Work stores task conversations beneath `%APPDATA%/kimi-desktop/daimon-share/daimon/runtime/kimi-code/home/sessions/`; the tool does not scan other tasks or guess the latest session. Run after the parent finishes. The command reads the journal and its explicitly referenced child journals in the same native `agents/` tree. Exported logs without that layout retain UNVERIFIED child coverage.

Roles:

- `implementation`: a new development workflow, including planning skill calls, execution/TDD skills before observed direct Write/Edit code or test writes, review, verification and finishing. Use `--review-agent agent-N` (repeatable) to identify the actual independent reviewer when children have mixed assignments. The tool does not infer a child's assigned phase from prose.
- `delegator`: a bounded task assigning independent review to fresh children; implementation and planning are not demanded again.
- `reviewer`: the child's own entry, using-superpowers and verification calls. This does not certify the contents of the review.

To produce JSON or create a report:

```powershell
python -B tools/audit_workflow.py --wire "C:\path\to\conversation\agents\main\wire.jsonl" --role implementation --review-agent agent-13 --json --output "C:\reports\audit.json"
```

The output parent directory must already exist. Reports are created exclusively: existing files, aliases and destinations inside the journal tree are refused. Without `--output`, only stdout is written. No network, embedded-command execution, installation, autostart, vendor change or tool veto occurs.

Status and exit codes:

| Exit | Formal status | Meaning |
|---|---|---|
| 0 | PASS | The implemented observable rules passed within the named role and supported evidence. |
| 1 | FAIL | At least one observable rule was violated. |
| 2 | UNVERIFIED | Evidence/input/output was incomplete, unsupported or unavailable, with no established formal violation. |

An established FAIL can coexist with UNVERIFIED findings. **`workflow_status` is always `NOT_PROVEN`**, including when formal checks pass. PASS does not prove that merely invoked skills were applied correctly.

The tool correlates actual call/result ids, does not credit failed or late loads, checks actual returned child type and the child's own calls, and checks full canonical audit transfer and receipt reread before subsequent direct code corrections. Completion requires `turn.ended` with `reason: completed` for the current kernel task as well as a completed actor run. `agent.turn.ended` alone is insufficient: native Stop can continue the same kernel task in another actor run. Source line references point to the original journal; payload bodies are not copied into the report. Profile/system prompts, reasoning, self-reports and skill catalogs do not establish successful invocation.

Manual coverage remains explicit: human approval and contract meaning, code correctness, native Spec Kit script/artifact steps, actual TDD RED/GREEN, natural-language checkpoints, decision-table content/order, role applicability and child phase assignments, journal authenticity/completeness, and mutations through Bash or other channels. The command does not certify write isolation or all phase transitions. Unknown protocol/record forms, missing children, partial records and unfinished sessions cannot establish full success.

The root entry skill does not automatically invoke this standalone completion auditor. A human or independent supervising process runs it and evaluates the report. The local 0.10.1 candidate separately tests a native [pre-completion Stop audit](internal-audit.md); it does not force model compliance or make this standalone command an autostart service.

Run synthetic tests without live user journals:

```powershell
python -B -m unittest discover -s tests -v
```

Keep real conversation fixtures and reports local; they are not part of this repository.

Windows alternate data stream destinations are refused, including streams through external hard-link aliases. An unchanged primary-stream hash alone does not establish input preservation; the regression suite checks that no input ADS is created.
