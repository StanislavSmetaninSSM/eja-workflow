# Optional delegation guard (experimental0.11.0)

Default: OFF. Native PreToolUse matchers cover only Agent and AgentSwarm. No user-message creation/rewrite, service, general Bash/write broker, permission changes or parameter replacement. A refusal is an ordinary native tool error; assistant/tool activity may be visible.

The installation owner can enable one exact workspace for up to3600seconds. Resolve the actual installed plugin and Kimi runtime-home paths from the local installation; these variables are supplied automatically to hook processes, not guaranteed in arbitrary model Bash. In a shell with KIMI_CODE_HOME explicitly set to the correct runtime home:

```text
python -B -X utf8 <absolute-installed-plugin>/tools/delegation_guard.py configure --workspace <absolute-owned-workspace> --ttl 3600
python -B -X utf8 <absolute-installed-plugin>/tools/delegation_guard.py off
```

Configuration writes only KIMI_CODE_HOME/eja-workflow-guard/target.json. Missing, disabled, foreign, malformed or expired scope allows unchanged calls; an unreadable outer hook payload cannot establish an opted-in scope. This is a bounded opt-in control, not fail-closed global enforcement. Configure/off require the user's authority; the plugin never enables itself. No native preferences panel is claimed.

In active scope, new calls require eja-methodology plus the [complete structured handoff](../skills/eja-workflow/references/delegation-packet.md). Every expanded new AgentSwarm item is checked. A review packet must carry the full current installed contract-review protocol. Structural fields do not prove truthful approval/readiness or actual skill/Serena execution. Wrong calls return a specific refusal and can be retried by the model with a new call ID within the original task. Fork:false allows a new call; fork:true and all explicit resume/resume_agent_ids fields are currently unsupported, even empty ones. A resumption must not be presented as a fresh independent review.

Denial receipts contain only version, session/call IDs, input SHA-256, reason code and random marker. Files publish atomically without replacement. Exact repeats retain the marker; conflicting identities cannot overwrite it. Only an exact correlated native error plus receipt and call/result UUID pair can be credited by the internal auditor as a prevented delegation; generic errors remain unknown. A prevented call is not a completed child or independent review. Core AgentSwarm lifecycle auditing remains separately unverified.

Symlink/reparse and bounded-file checks protect ordinary accidental redirection. This is not a hostile-filesystem security sandbox. The native hook can veto attempted calls; it cannot force an omitted call or transform arguments. Ordinary Work's inspected system instruction requires explicitly requested delegation, while the tested fresh UI Swarm composition supplies a parallel-delegation mandate. Enabling this guard does not alter either rule.
