# Пакет делегирования EJA_HANDOFF_V1

Передай каждый новый блок через Agent с subagent_type `eja-methodology`. Строка prompt начинается с `EJA_HANDOFF_V1`, затем JSON-объект, затем отдельная строка `END_EJA_HANDOFF`, затем конкретное поручение. JSON экранируется обычным json.dumps; не вставляй кодовые ограждения вокруг конверта. Поля не создают разрешений: записывай фактическое пользовательское согласование и явно открытые вопросы.

Пример формы: подставь существующий рабочий проект, абсолютные корни/артефакты, фактический сервер и результаты Serena. Пути примера условны.

```text
EJA_HANDOFF_V1
{
  "schema_version": 1,
  "role": "implementation",
  "original_request": "Дословный исходный запрос пользователя",
  "approved_contract": "Результат, границы и сохраняемые гарантии",
  "approval_source": "Конкретное предложение и ответ пользователя либо его прямой запрос",
  "canonical_artifacts": ["C:/work/project/specs/001-feature/spec.md", "C:/work/project/specs/001-feature/plan.md", "C:/work/project/specs/001-feature/tasks.md"],
  "write_roots": ["C:/work/project/src/module-a", "C:/work/project/.work/module-a"],
  "verification": ["Команда тестов и ожидаемые гарантии"],
  "methodology": {
    "entry": "eja-workflow:eja-workflow",
    "using": "superpowers:using-superpowers",
    "child_guide": "C:/installed-plugin/skills/eja-workflow/references/child-agents.md",
    "phase_skills": ["spec-kit-superpowers-bridge", "superpowers:executing-plans", "superpowers:test-driven-development", "superpowers:verification-before-completion"]
  },
  "serena": {
    "status": "ready",
    "project": "C:/work/project",
    "server": "Фактический сервер",
    "languages": ["typescript"],
    "evidence": "Файл/символ/идентификатор успешного запроса и кто использует общий проект"
  }
}
END_EJA_HANDOFF
Выполни только назначенные task ID; сохрани прочие модули. Верни diff, проверки и пропуски.
```

Роли: implementation, review, investigation. У implementation обязательно укажи execution и TDD; ребёнок вызывает их сам до реализации, включая записи через Serena/Bash. Родительская execution не считается дочерней. У review обязательны phase_skills с verification-before-completion и contract_review_ref: прочитай установленный references/contract-review.ref.json и скопируй его короткий объект version/sha256 без изменений. Полный протокол детерминированно включён в собственный профиль; ребёнок ещё читает актуальный contract-review.md через Read до проверяемых файлов/команд. Ссылка сама по себе не является чтением. Legacy contract_review с полным текущим текстом поддерживается как альтернативное поле, не вместе с reference. Для read-only без Spec Kit документов canonical_artifacts содержит явную причину неприменимости. Ревьюер применяет роль ревью, не запускает реализацию ради записи scratch.

Все списки непусты, кроме явно запрещённых корней записи review inline; строки содержательны; корни записи, проект Serena и child_guide абсолютные. Неизвестную готовность не обозначай ready: status unavailable требует точной ошибки и зависимой фазы в evidence, сама недоступность не разрешает пропуск работы с кодом. У ребёнка всё равно нужен собственный успешный find_symbol в настоящем файле. JSON-пакет доказывает полноту формы, не истинность утверждений и не соблюдение процесса.

Для нового AgentSwarm используй тот же профиль и prompt_template с {{item}}; после подстановки каждого items должен получаться полный валидный конверт, с раздельными корнями и задачами. Требуется 2–128 различных элементов; core-режим AgentSwarm включается штатным способом отдельно. Выбранный UI Swarm может запускать обычные параллельные Agent — этот формат применим к каждому из них.

Guard необязателен и не меняет параметры вызова. В включённой папке он возвращает отказ до запуска; следующий исправленный вызов получает новый tool-call ID. Отказ не считается выполненным ребёнком или независимым ревью. Resume и fork:true в строгой области не поддерживаются; не добавляй пустые resume/resume_agent_ids. Вне включённой области инструменты работают штатно.

## Доставка полного ревью (0.11.1)

Для review обязательны result_delivery и result_artifact. Обычный случай: result_delivery="artifact", result_artifact — абсолютный путь к отдельному report.md внутри уже разрешённого write_roots и .work. Например, C:/work/project/.work/review-a/report.md при write_roots=["C:/work/project/.work/review-a"]. Путь не содержит .. или alternate streams; разные параллельные ревью имеют разные пути. До первого завершения ребёнок пишет полный отчёт через Write по этому абсолютному пути; после изменений отчёта обновляет файл. Родитель читает весь файл через Read до приёмки.

Если пользователь прямо запретил записи, используй result_delivery="inline", result_artifact=null, write_roots=[] и artifact_unavailable_reason="user_forbids_writes". Фактический запрет отражается в approval_source/approved_contract; это утверждение проверяется вручную. Размер задачи и удобство не являются исключением. Последнее сообщение должно содержать полный самостоятельный отчёт.

После внутреннего Stop финальный ответ сохраняет находки, доказательства, решения и путь результата: родителю доставляется только последнее сообщение. Автоаудит связывает роль/назначение только с текущим native turn.prompt дочернего агента; успешная запись точного пути с парой call UUID/result parentUuid — свидетельство действия, а не полноты текста или прочтения родителем. Старый файл и предыдущая задача не дают кредит. Новый формат обязателен для review; implementation/investigation сохраняют прежнюю форму.

Минимальное native-объявление implementation устанавливает только намерение роли, даже если phase_skills неполон: оно не создаёт готовность, согласие или права. Аудитор потребует действительные собственные execution/TDD/bridge до наблюдаемой записи либо первого возврата. Для review актуальное успешное полное Read с совпадающими actor/turn/call UUID/result parentUuid предшествует наблюдаемой работе. Содержательность проверок и разрешений остаётся независимой проверкой.
