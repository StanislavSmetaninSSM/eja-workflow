<p align="center"><img src="icon.webp" width="144" alt="Eja Workflow hedgehog / ёж"></p>

# Eja Workflow

**Русский** · [English](#english)

## По-русски

**Eja Workflow — плагин для настольного Kimi Work, который помогает выполнять рабочие задачи по согласованной методологии: от обсуждения идеи и спецификации до реализации, проверки и независимого ревью.** Он связывает Superpowers, Spec Kit, мост между ними и Serena. Предназначен для Kimi Work, а не Kimi Code.

### Для чего нужен

- Уточнить идею и требования до написания кода.
- Создать или продолжить одну feature с каноническими <code>spec.md</code>, <code>plan.md</code> и <code>tasks.md</code>, сохранив согласованный объём и существующие контракты.
- Реализовать изменение или исправление с подходящими фазовыми скиллами, тестами и проверкой результата.
- Передать ограниченную задачу дочернему агенту, провести независимое ревью и вернуть полный результат родителю.

Плагин содержит стартовый скилл, контрольные точки этапов, профиль дочернего агента <code>eja-methodology</code> и локальный аудитор. Для рабочего задания сначала вызывается <code>eja-workflow:eja-workflow</code>, затем фазовые скиллы загружаются по мере необходимости. Простой фактический вопрос не требует полного цикла разработки. Обычные ответы посвящены результату и существенным ограничениям; диагностика процесса остаётся внутренней, если пользователь её не запросил.

### Установка и зависимости

1. Скачайте и распакуйте ZIP из [последнего релиза](https://github.com/StanislavSmetaninSSM/eja-workflow/releases/latest) либо клонируйте репозиторий.
2. В Kimi Work попросите штатный **Plugin Builder** проверить и зарегистрировать локальную папку с <code>kimi.plugin.json</code> как личный плагин. Например: «Зарегистрируй Eja Workflow из этой папки в личных плагинах; сохрани работающие зависимости».
3. Откройте **Плагины → Личные** и установите или обновите **Eja Workflow**. Регистрация сама по себе ещё не установка.
4. В новой рабочей сессии явно выберите/вызовите <code>eja-workflow:eja-workflow</code>. Установленный плагин не подтверждает фактического выполнения методологии.

Отдельно нужны [Superpowers](https://github.com/obra/superpowers), [Spec Kit](https://github.com/github/spec-kit), [Spec Kit–Superpowers Bridge](https://github.com/StanislavSmetaninSSM/spec-kit-superpowers-bridge) и MCP-сервер [Serena](https://github.com/oraios/serena). Они **не включены** в архив. Стартовый скилл объясняет поиск существующих установок и установку недостающего в пределах разрешённого пользователем объёма. Рабочие установки сохраняются. См. [зависимости](skills/eja-workflow/references/dependencies.md), [Serena в Work](skills/eja-workflow/references/serena-work-plugin.md), [дочерние агенты](skills/eja-workflow/references/child-agents.md). Пути из примеров адаптируйте к своей машине.

### Проверено и ограничения

Версия **0.12.5** исправляет рецепт установки Serena: проверенный новый маршрут 2.1.0, доступный manual, явные предпосылки и ограниченные повторы. Рецепт сначала обнаруживает и повторно использует работающую установку Serena; автоматическая миграция не предусмотрена. [Проверка в чистом профиле Windows](docs/serena-clean-install.md) описывает фактические результаты и границы. Код аудитора и хуков сохранён с 0.12.2; лицензия MIT. Проверенная среда: **Windows, Kimi Work 3.2.24.27**. Команды хуков используют Windows-синтаксис; поддержка других ОС не заявлена.

В живых проверках работали внутренний аудит, профиль дочернего агента, передача отчёта и два параллельных вызова <code>Agent</code> при включённом в интерфейсе Swarm. Отдельный пакетный API <code>AgentSwarm</code> не прошёл живую проверку. Это подтверждает отдельные механизмы, а не строгое соблюдение всех этапов: наблюдались пропуски скиллов и методологические ошибки. Процент успешности не установлен; гарантии 80% или 100% нет.

Хук <code>Stop</code> может запросить ограниченный внутренний аудит и исправление пропущенных шагов. Он использует поддерживаемые журналы и не охватывает произвольные побочные эффекты команд или все виды задач. При неподдерживаемых критичных данных аудит становится недоступным без цикла автоматических исправлений продукта. Необязательный [контроль делегирования](docs/delegation-guard.md) выключен по умолчанию, включается для явно выбранной папки максимум на час и проверяет известные параметры передачи задания. Это не общий запрет записи.

Плагин не дописывает служебный текст к сообщениям пользователя, не рассылает отчёты в чат, не требует блока в проектном <code>AGENTS.md</code> и не меняет автоматический режим Work. В проверенной версии Work обычный стартовый/system-prompt вклад плагина для основного агента отключён движком; автоматический старт на каждой задаче не гарантируется. Явный вызов стартового скилла остаётся надёжным способом входа. Открытые диалоги могут сохранять старые инструкции.

Совместимость с обновлениями проверяется по возможностям и структуре данных без жёсткой привязки к номеру приложения. Безвредные дополнительные поля допускаются; изменение обязательного публичного интерфейса Work может потребовать обновления плагина. После обновления Work проверьте доступность скилла и хуков. Плагин не обновляет приложение принудительно.

Подробности: [внутренний аудит](docs/internal-audit.md), [самостоятельный аудитор](docs/audit.md), [контроль делегирования](docs/delegation-guard.md).

### Изменения и лицензия

Изменения в <code>main</code> принимаются через PR с одобрением владельца **@StanislavSmetaninSSM** для всех файлов. Владелец может обходить это правило для собственных изменений. См. [CONTRIBUTING.md](CONTRIBUTING.md).

Лицензия: **[MIT](LICENSE)**. Автор: StanislavSmetaninSSM (Eja), 2026. Разрешены использование, изменение и распространение, включая коммерческое, при сохранении уведомления об авторстве и текста лицензии. ПО предоставляется без гарантий. Правила PR относятся к этому репозиторию и не ограничивают отдельные форки в рамках MIT. Отдельно устанавливаемые зависимости сохраняют собственные лицензии. Личные журналы тестов, состояние приложения, ключи и локальные резервные копии в пакет не входят.

---

## English

**Eja Workflow is a plugin for Kimi Work desktop that helps carry out work through an agreed methodology: idea discussion, specification, implementation, verification and independent review.** It connects Superpowers, Spec Kit, their bridge and Serena. It targets Kimi Work, not Kimi Code.

### What it is for

- Clarify an idea and requirements before writing code.
- Create or resume one feature with canonical <code>spec.md</code>, <code>plan.md</code> and <code>tasks.md</code>, preserving approved scope and existing contracts.
- Implement a change or fix with relevant phase skills, tests and result verification.
- Delegate a bounded task, obtain independent review and deliver the full result to the parent.

The plugin contains an entry skill, phase checkpoints, the <code>eja-methodology</code> child profile and a local auditor. A work task starts with <code>eja-workflow:eja-workflow</code>; phase skills load when needed. A simple factual question does not need the full development cycle. Ordinary replies focus on the result and material limitations; process diagnostics stay internal unless requested.

### Installation and dependencies

1. Download and extract the ZIP from the [latest release](https://github.com/StanislavSmetaninSSM/eja-workflow/releases/latest), or clone this repository.
2. In Kimi Work, ask the built-in **Plugin Builder** to validate and register the local folder containing <code>kimi.plugin.json</code> as a personal plugin. For example: “Register Eja Workflow from this folder in Personal plugins; preserve working dependencies.”
3. Open **Plugins → Personal** and install or update **Eja Workflow**. Registration alone does not install it.
4. In a fresh work session, explicitly select/invoke <code>eja-workflow:eja-workflow</code>. Being installed does not prove actual methodology use.

[Superpowers](https://github.com/obra/superpowers), [Spec Kit](https://github.com/github/spec-kit), [Spec Kit–Superpowers Bridge](https://github.com/StanislavSmetaninSSM/spec-kit-superpowers-bridge) and the [Serena](https://github.com/oraios/serena) MCP server are separate dependencies, **not bundled**. The entry skill explains discovery and installation within the user's authorized scope. Working installations are preserved. See [dependencies](skills/eja-workflow/references/dependencies.md), [Serena in Work](skills/eja-workflow/references/serena-work-plugin.md) and [child agents](skills/eja-workflow/references/child-agents.md). Adapt example paths to your machine.

### Tested behavior and limits

Version **0.12.5** corrects the Serena installation recipe: a tested fresh 2.1.0 path, available manual, explicit prerequisites and bounded retries. The recipe first discovers and reuses a working Serena installation; it does not request automatic migration. [Clean Windows profile validation](docs/serena-clean-install.md) records actual results and limits. Auditor and hook code is unchanged from 0.12.2; the license is MIT. The tested environment is **Windows, Kimi Work 3.2.24.27**. Hook commands use Windows syntax; other operating systems are not claimed as supported.

Live checks exercised internal audits, the child profile, full report delivery and two concurrent <code>Agent</code> calls with UI Swarm enabled. The separate batch <code>AgentSwarm</code> API has not been live-validated. These establish individual mechanisms, not strict end-to-end methodology compliance: skipped skills and workflow failures were observed. No success percentage has been established; neither 80% nor 100% is guaranteed.

A <code>Stop</code> hook can request a bounded internal audit and correction of missed steps. It reads supported journals and cannot cover arbitrary command side effects or every task type. Unsupported critical evidence makes the audit unavailable without an automatic product-repair loop. The optional [delegation guard](docs/delegation-guard.md) is off by default, applies to an explicitly selected folder for at most one hour, and checks known delegation parameters. It is not a general write gate.

The plugin does not append service text to user messages, send reports into the user chat, require a project <code>AGENTS.md</code> block, or change Work auto mode. In the tested Work build, the engine disables normal plugin startup/system-prompt contributions to the main agent; automatic entry on every task is not guaranteed. Explicit entry-skill invocation remains the dependable way to start. Existing conversations may retain older instructions.

Update resilience uses capability and data-shape checks rather than a fixed application version. Harmless extra fields are tolerated; breaking changes to Work's required public interface may need a plugin update. Check skill and hook availability after updating Work. The plugin does not force application updates.

Technical details: [internal audit](docs/internal-audit.md), [standalone auditor](docs/audit.md), [delegation guard](docs/delegation-guard.md).

### Contributions and license

Changes to <code>main</code> require a PR approved by owner **@StanislavSmetaninSSM** for all files. The owner may bypass this rule for their own changes. See [CONTRIBUTING.md](CONTRIBUTING.md).

License: **[MIT](LICENSE)**. Copyright 2026 StanislavSmetaninSSM (Eja). Use, modification and redistribution, including commercial use, are permitted while retaining the copyright and license notice. The software is provided without warranty. PR rules govern this repository and do not restrict separate forks under MIT. Separately installed dependencies retain their own licenses. Private test journals, application state, credentials and local backups are excluded from the package.
