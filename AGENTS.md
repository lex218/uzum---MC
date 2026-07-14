# AGENTS.md

Гайд для AI-агентов (OpenCode и др.) по работе с этим репозиторием.

## Проект

`uzum-moysklad-sync` — синхронизация Uzum Market (FBO) → МойСклад без отчёта
комиссионера. Подробности логики — в `README.md`, маппинг полей и статусов —
в `docs/mapping.md`.

## Структура

- `main.py` — точка входа и CLI-команды.
- `config.py` — конфигурация из окружения (`.env`, см. `.env.example`).
- `db.py` — слой SQLite (`sync.sqlite3`) для идемпотентности.
- `notifier.py` — уведомления в Telegram.
- `uzum/`, `moysklad/` — клиенты соответствующих API.
- `sync/` — логика синхронизации.
- `scripts/` — вспомогательные скрипты.
- `tests/` — pytest-тесты.

## Окружение и запуск

```bash
python3.11 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env   # заполнить UZUM_TOKEN, MS_TOKEN и пр.
.venv/bin/python main.py init      # проверка доступов, доп.поля/статусы
.venv/bin/python main.py dry-run   # план без создания документов
.venv/bin/python main.py run       # рабочий цикл
```

CLI-команды: `sync-orders`, `sync-shipments`, `sync-returns`, `sync-transfers`,
`check-stock`, `align-stock`, `sync-all`, `run`; флаг `--dry-run`.

## Тесты

```bash
.venv/bin/python -m pytest
```

Всегда прогоняй тесты после изменений в логике синхронизации.

## Конвенции

- Python 3.11.
- Идемпотентность обязательна: используем SQLite + `externalCode` в МойСклад,
  повторный запуск не должен дублировать документы.
- Секреты только через `.env`; не коммить токены и `sync.sqlite3`.
- Комментарии и сообщения в логах — на русском, в стиле существующего кода.
