# Участие в проекте / Contributing

## По-русски

Предлагайте изменения через pull request в `main`. Для всех файлов, включая `.github/CODEOWNERS`, требуется одобрение владельца @StanislavSmetaninSSM. После новых коммитов прежнее одобрение сбрасывается. Обход правил настроен только для аккаунта владельца; владелец может объединять собственные PR без самоодобрения.

Опишите проблему, ожидаемое поведение и проверку. Сохраняйте согласованный контракт, границы разрешённой записи, тихие пользовательские ответы и исходные результаты задачи. Не добавляйте служебные сообщения в пользовательский чат, секреты, журналы приложения или локальные резервные копии.

Перед PR выполните из корня репозитория:

```text
python -B tools/sync_profile_policy.py --check
python -B -m unittest discover -s tests -v
```

Проверьте пакет актуальным штатным Plugin Builder в Kimi Work. Зелёные тесты не подтверждают соблюдение методологии моделью; существенные ограничения описывайте честно. Проект распространяется по MIT: сохраняйте уведомление об авторстве и текст лицензии в копиях или существенных частях кода.

## English

Propose changes through a pull request to `main`. All files, including `.github/CODEOWNERS`, require approval from owner @StanislavSmetaninSSM. New commits dismiss previous approval. Only the owner's account has a bypass; the owner may merge their own PRs without self-approval.

Describe the problem, expected behavior and verification. Preserve approved contracts, authorized write boundaries, quiet public replies and the original task result. Do not add service messages to the user chat, secrets, application journals or local backups.

Before opening a PR, run from the repository root:

```text
python -B tools/sync_profile_policy.py --check
python -B -m unittest discover -s tests -v
```

Validate the package with Kimi Work's current built-in Plugin Builder. Passing tests do not establish actual model compliance; report material limitations accurately. This project is MIT-licensed: retain the copyright and license notice in copies or substantial portions of the software.
