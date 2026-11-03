<p align="center"><img src="icon.webp" width="144" alt="Eja Workflow hedgehog"></p>

# Eja Workflow

Personal task-methodology plugin for **Kimi Work desktop**: Superpowers, Spec Kit, their bridge, Serena and independent review. Technical plugin id: `eja-workflow`.

**Local experimental 0.11.0 candidate; frozen native diagnostic acceptance pending.** Safe private main 0.9.1 remains the fallback. The previous0.10.3 Swarm review proved two custom profiles and automatic late audit correction, with ordering/Serena failures retained.

**Private preview.** Instructions and observed use are distinct. This plugin does not guarantee automatic compliance or technically enforce every workflow transition. The tested delivery path is Windows / Kimi Work 3.2.24.27.

## What it contains

- An entry skill and phase checkpoints, with one canonical Spec Kit feature.
- Dependency discovery and installation guidance; working dependencies are preserved. The dependency suites and Serena server are not bundled.
- Explicit entry through `eja-workflow:eja-workflow`. No automatic startup/prompt-rewrite hook. A separate experimental Stop continuation can request a local audit after observed work.
- The `eja-methodology` child profile and a launch reference covering role, original proposal + approval, allowed paths, artifacts and evidence. Both delegation tools are excluded from this child; the parent owns further dispatch and independent review.
- An original hedgehog icon.
- An optional [independent local process auditor](docs/audit.md), with synthetic tests. It reads supported native journals and produces redacted text/JSON findings; formal checks do not certify full methodology compliance.
- An optional, initially disabled, [delegation guard](docs/delegation-guard.md). It refuses objectively incomplete Agent/AgentSwarm calls only in an explicitly configured exact workspace for at most one hour. It does not rewrite inputs, require project AGENTS, change auto mode or send user messages.

## Install / update

1. Clone or download this repository using a GitHub account with access.
2. In Kimi Work, ask Plugin Builder to validate and register the local folder as a personal plugin. For example: “Register this local Eja Workflow folder in my Personal plugins; preserve existing dependencies and report validation.”
3. Open **Plugins → Personal / Личные** and install or update **Eja Workflow**. Registration alone does not install it.
4. For a working task, explicitly invoke `eja-workflow:eja-workflow`. Verify actual skill calls and artifacts; the installed badge alone does not establish compliance.

See [dependency guidance](skills/eja-workflow/references/dependencies.md), [Serena setup](skills/eja-workflow/references/serena-work-plugin.md) and [child agents](skills/eja-workflow/references/child-agents.md). User/project instructions and their permissions govern the task. Machine-specific paths in examples must be resolved for the actual installation.

## Supported and unverified behavior

In the tested Work build, the engine disables ordinary plugin sessionStart/system-prompt contributions to the main agent. Explicit plugin selection did not restore them. The former UserPromptSubmit delivery prepended the full protocol inside the displayed user message; version0.9.1 removes that automatic route. No project AGENTS edits or daemon-wide prompt override are performed.

A real native Agent call confirmed the custom child system body and the exclusion of Agent/AgentSwarm. A focused read-only approval review loaded its own entry skills and identified the approved clause correctly. This does not establish all-phase adherence. Parent routing and receipt checks remain model behavior. AgentSwarm fan-out has not been live-validated for this plugin; native auto mode is preserved.

The standalone auditor requires both the completed kernel task and the completed agent run. This candidate contains an experimental [internal Stop audit](docs/internal-audit.md): Stop and fixed-auditor Bash binding, plus two opt-in delegation matchers. No UserPromptSubmit, sessionStart, AGENTS setup, global override or external chat reports. Earlier native audit/correction/recheck and a no-injected-user-message outcome were observed, with methodology failures;0.11.0's new candidate still needs its own frozen tests. Eligibility can miss unsupported/unflushed work, including documentation-only or read-only tasks without qualifying calls. Verification now checks successful instruction-result order and a subsequent successful shell check after code writes; recognized nonempty find_symbol results provide bounded per-actor Serena evidence, not proof of correct active project or semantic correctness. Overall automatic-methodology acceptance remains unmet. Recheck capabilities after Work updates.

## Repository scope

This repository contains plugin source and usage guidance. Local test conversations, machine state, credentials and experiment fixtures are excluded. No public release is made by this private preview.

License status: **UNLICENSED**. A public-release license has not been selected.

---

**По-русски:** установите Eja Workflow через Plugin Builder и раздел личных плагинов. Для рабочей задачи явно вызовите `eja-workflow:eja-workflow`. В этом локальном кандидате проверяется внутренний аудит через Stop и точную привязку его команды. Стартовый UserPromptSubmit удалён: он добавлял служебный текст в видимое сообщение пользователя. Живая проверка нового кандидата ещё не пройдена. Плагин содержит методологию, профиль и аудитор; строгий автоматический режим пока не достигнут.
