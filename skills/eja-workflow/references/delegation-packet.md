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
    "phase_skills": ["superpowers:executing-plans", "superpowers:test-driven-development", "superpowers:verification-before-completion"]
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

Роли: implementation, review, investigation. У review обязательны phase_skills с verification-before-completion и поле contract_review с **полным текущим текстом** установленного references/contract-review.md, прочитанным через Read; упоминание пути или пересказ не подходит. Для read-only без Spec Kit документов canonical_artifacts содержит явную причину неприменимости. Ревьюер применяет роль ревью, не запускает реализацию ради записи scratch.

Все списки непусты, строки содержательны; корни записи, проект Serena и child_guide абсолютные. Неизвестную готовность не обозначай ready: status unavailable требует точной ошибки и зависимой фазы в evidence, сама недоступность не разрешает пропуск работы с кодом. У ребёнка всё равно нужен собственный успешный find_symbol в настоящем файле. JSON-пакет доказывает полноту формы, не истинность утверждений и не соблюдение процесса.

Для нового AgentSwarm используй тот же профиль и prompt_template с {{item}}; после подстановки каждого items должен получаться полный валидный конверт, с раздельными корнями и задачами. Требуется 2–128 различных элементов; core-режим AgentSwarm включается штатным способом отдельно. Выбранный UI Swarm может запускать обычные параллельные Agent — этот формат применим к каждому из них.

Guard необязателен и не меняет параметры вызова. В включённой папке он возвращает отказ до запуска; следующий исправленный вызов получает новый tool-call ID. Отказ не считается выполненным ребёнком или независимым ревью. Resume и fork:true в строгой области не поддерживаются; не добавляй пустые resume/resume_agent_ids. Вне включённой области инструменты работают штатно.
