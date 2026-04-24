# Proxy Aggregation Project - Stage 6 (Geo Provider Layer)

## Что делает Stage 6
Stage 6 расширяет `prober` гео-обогащением по реальному `exit_ip`:
- после успешного connect и получения `exit_ip` выполняется geo lookup;
- страна выхода сохраняется в `proxy_checks.exit_country`;
- `proxy_checks.geo_match` вычисляется сравнением `proxy_candidates.source_country_tag` и фактической страны выхода;
- используется primary/fallback стратегия geo providers через `.env`.

Важно: реальная страна определяется только по `exit_ip`. `source_country_tag` остается эвристической меткой из parser.

## Что уже покрыто в Stage 0-6
- Postgres + миграции
- `sources`, `source_snapshots`, `proxy_candidates`, `proxy_checks`, `proxy_state`
- fetcher -> `source_snapshots`
- parser -> `proxy_candidates`
- prober (sing-box) -> `connect_ok`, `connect_ms`, `exit_ip`
- geo layer -> `exit_country`, `geo_match`

## Что НЕ входит в Stage 6
- speed test (`first_byte_ms`, `download_mbps`)
- scorer / обновление `proxy_state`
- exporter
- HTTP API
- scheduler / очереди / Redis / Celery

## Поддерживаемые geo providers
- `ip-api`
- `ipwhois` (совместимый endpoint, по умолчанию `ipwho.is`)

## Требования
- Docker + Docker Compose v2
- Python 3.11+
- установленный бинарь `sing-box` (доступен через PATH или явный путь в `SINGBOX_BINARY`)

## Переменные окружения
### Базовые
- `PROBE_BATCH_SIZE`
- `CONNECT_TIMEOUT_SECONDS`
- `DOWNLOAD_TIMEOUT_SECONDS`
- `SINGBOX_BINARY`
- `PROBER_LOCAL_BIND_HOST`
- `PROBER_BASE_LOCAL_PORT`
- `PROBER_PROCESS_START_TIMEOUT_SECONDS`
- `PROBER_EXIT_IP_URL`

### Geo stage
- `GEO_PROVIDER_PRIMARY` (например `ip-api`)
- `GEO_PROVIDER_FALLBACK` (например `ipwhois`)
- `GEO_REQUEST_TIMEOUT_SECONDS`
- `GEO_IP_API_BASE_URL` (по умолчанию `http://ip-api.com/json`)
- `GEO_IPWHOIS_BASE_URL` (по умолчанию `https://ipwho.is`)

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

## Полный цикл Stage 6
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

3. Применить миграции:
```bash
alembic -c alembic.ini upgrade head
```

4. Применить seed:
```bash
docker compose exec -T db sh -lc 'psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB"' < sql/seeds/001_sources.sql
```

Windows PowerShell:
```powershell
Get-Content sql/seeds/001_sources.sql | docker compose exec -T db sh -lc 'psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB"'
```

5. Запустить fetcher:
```bash
python -m app.fetcher.main
```

6. Запустить parser:
```bash
python -m app.parser.main
```

7. Запустить prober (с geo enrichment):
```bash
python -m app.prober.main
```

## SQL-проверки Stage 6
### 1) Count записей с непустым `exit_country`
```bash
docker compose exec -T db sh -lc 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "SELECT COUNT(*) AS checks_with_exit_country FROM proxy_checks WHERE exit_country IS NOT NULL;"'
```

### 2) Последние 20 успешных проверок с `exit_country`
```bash
docker compose exec -T db sh -lc 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "SELECT checked_at, candidate_id, connect_ms, exit_ip, exit_country, geo_match FROM proxy_checks WHERE connect_ok = TRUE AND exit_country IS NOT NULL ORDER BY checked_at DESC LIMIT 20;"'
```

### 3) Распределение по странам (`GROUP BY exit_country`)
```bash
docker compose exec -T db sh -lc 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "SELECT exit_country, COUNT(*) AS checks_count FROM proxy_checks WHERE exit_country IS NOT NULL GROUP BY exit_country ORDER BY checks_count DESC, exit_country ASC;"'
```

### 4) Mismatch check (`geo_match = false`)
```bash
docker compose exec -T db sh -lc 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "SELECT pc.checked_at, pc.candidate_id, c.source_country_tag, pc.exit_country, pc.geo_match, pc.connect_ms, pc.exit_ip FROM proxy_checks pc JOIN proxy_candidates c ON c.id = pc.candidate_id WHERE pc.geo_match = FALSE ORDER BY pc.checked_at DESC LIMIT 50;"'
```

## Логика `geo_match`
- `NULL`, если `source_country_tag` отсутствует
- `TRUE`, если `source_country_tag` совпадает с `exit_country`
- `FALSE`, если `source_country_tag` есть, но не совпадает

Сравнение выполняется в нормализованном uppercase виде.
