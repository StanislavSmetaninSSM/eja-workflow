<p align="center"><img src="icon.webp" width="144" alt="Eja Workflow hedgehog"></p>

# Eja Workflow

Personal task-methodology plugin for **Kimi Work desktop**: Superpowers, Spec Kit, their bridge, Serena and independent review. Technical plugin id: `eja-workflow`.

**Private preview.** Instructions and observed use are distinct. This plugin does not guarantee automatic compliance or technically enforce every workflow transition. The tested delivery path is Windows / Kimi Work 3.2.24.27.

## What it contains

- An entry skill and phase checkpoints, with one canonical Spec Kit feature.
- Dependency discovery and installation guidance; working dependencies are preserved. The dependency suites and Serena server are not bundled.
- A Windows UserPromptSubmit hook that supplies the policy from this plugin through PowerShell. It reads only its own files, does not process/save the user prompt and does not block tools.
- The `eja-methodology` child profile and a launch reference covering role, original proposal + approval, allowed paths, artifacts and evidence. Both delegation tools are excluded from this child; the parent owns further dispatch and independent review.
- An original hedgehog icon.

## Install / update

1. Clone or download this repository using a GitHub account with access.
2. In Kimi Work, ask Plugin Builder to validate and register the local folder as a personal plugin. For example: “Register this local Eja Workflow folder in my Personal plugins; preserve existing dependencies and report validation.”
3. Open **Plugins → Personal / Личные** and install or update **Eja Workflow**. Registration alone does not install it.
4. Start a fresh task and describe the work normally. Verify actual skill calls and artifacts; the installed badge alone does not establish compliance.

See [dependency guidance](skills/eja-workflow/references/dependencies.md), [Serena setup](skills/eja-workflow/references/serena-work-plugin.md) and [child agents](skills/eja-workflow/references/child-agents.md). User/project instructions and their permissions govern the task. Machine-specific paths in examples must be resolved for the actual installation.

## Supported and unverified behavior

In the tested Work build, the engine disables ordinary plugin sessionStart/system-prompt contributions to the main agent. Explicit plugin selection did not restore them. The Windows user-prompt hook is a separate delivery channel.

A real native Agent call confirmed the custom child system body and the exclusion of Agent/AgentSwarm. A focused read-only approval review loaded its own entry skills and identified the approved clause correctly. This does not establish all-phase adherence. Parent routing and receipt checks remain model behavior. AgentSwarm fan-out has not been live-validated for this plugin; native auto mode is preserved.

The four-test Windows transport suite and native validators pass; overall automatic-methodology acceptance remains unmet. Recheck capabilities after Work updates.

## Repository scope

This repository contains plugin source and usage guidance. Local test conversations, machine state, credentials and experiment fixtures are excluded. No public release is made by this private preview.

License status: **UNLICENSED**. A public-release license has not been selected.

---

**По-русски:** установите Eja Workflow через Plugin Builder и раздел личных плагинов, затем описывайте задачи обычным языком. Скиллы и инструменты должны вызываться фактически. Плагин помогает соблюдать методологию; проверка полного автоматического соблюдения пока не пройдена.
