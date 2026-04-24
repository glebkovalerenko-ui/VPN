# Proxy Aggregation Project - Stage 5 (Sing-Box Runtime Prober)

## Что делает Stage 5
Stage 5 реализует `prober`, который:
- детерминированно выбирает кандидатов из `proxy_candidates`;
- запускает реальную проверку через `sing-box` subprocess;
- получает реальный `exit_ip` через поднятый локальный inbound;
- сохраняет результат в `proxy_checks`.

В Stage 5 не реализуются:
- `first_byte_ms`, `download_mbps`;
- `exit_country`, `geo_match`;
- `scorer`, обновление `proxy_state`;
- `exporter`, HTTP API, scheduler.

## Поддержка протоколов в backend
Через sing-box runtime сейчас покрыты:
- `vless`
- `vmess`
- `trojan`
- `ss`

Непокрытые схемы (например `hysteria2`, `tuic`) пишутся как controlled failure (`unsupported_protocol`).

## Требования
- Docker + Docker Compose v2
- Python 3.11+
- установленный бинарь `sing-box` (доступен через PATH или явный путь в `SINGBOX_BINARY`)

## Переменные окружения prober
В `.env` доступны:
- `SINGBOX_BINARY` (по умолчанию `sing-box`)
- `PROBER_LOCAL_BIND_HOST` (по умолчанию `127.0.0.1`)
- `PROBER_BASE_LOCAL_PORT` (по умолчанию `39000`, при занятости выбирается следующий)
- `PROBER_PROCESS_START_TIMEOUT_SECONDS` (по умолчанию `8`)
- `PROBER_EXIT_IP_URL` (по умолчанию `https://api.ipify.org?format=json`)
- `PROBER_TEMP_DIR` (опционально, директория для временных sing-box config)

Пример явного пути к бинарю:
```bash
SINGBOX_BINARY=/usr/local/bin/sing-box
```

Windows PowerShell:
```powershell
$env:SINGBOX_BINARY = 'C:\tools\sing-box\sing-box.exe'
```

## Установка зависимостей
```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Windows PowerShell:
```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

## Запуск DB
1. Подготовить `.env`:
```bash
cp .env.example .env
```

Windows PowerShell:
```powershell
Copy-Item .env.example .env
```

2. Поднять Postgres:
```bash
docker compose up -d db
```

## Миграции
```bash
alembic -c alembic.ini upgrade head
```

## Seed
```bash
docker compose exec -T db sh -lc 'psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB"' < sql/seeds/001_sources.sql
```

Windows PowerShell:
```powershell
Get-Content sql/seeds/001_sources.sql | docker compose exec -T db sh -lc 'psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB"'
```

## Запуск fetcher
```bash
python -m app.fetcher.main
```

## Запуск parser
```bash
python -m app.parser.main
```

## Запуск prober
```bash
python -m app.prober.main
```

## SQL-проверки `proxy_checks`
### Count
```bash
docker compose exec -T db sh -lc 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "SELECT COUNT(*) AS proxy_checks_count FROM proxy_checks;"'
```

### Последние проверки
```bash
docker compose exec -T db sh -lc 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "SELECT checked_at, candidate_id, connect_ok, connect_ms, exit_ip, error_code FROM proxy_checks ORDER BY checked_at DESC LIMIT 20;"'
```

### Успешные проверки с `exit_ip`
```bash
docker compose exec -T db sh -lc 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "SELECT checked_at, candidate_id, connect_ms, exit_ip FROM proxy_checks WHERE connect_ok = TRUE AND exit_ip IS NOT NULL ORDER BY checked_at DESC LIMIT 50;"'
```

### Ошибки
```bash
docker compose exec -T db sh -lc 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "SELECT checked_at, candidate_id, error_code, left(coalesce(error_text, ''''), 180) AS error_text_preview FROM proxy_checks WHERE connect_ok = FALSE ORDER BY checked_at DESC LIMIT 50;"'
```
