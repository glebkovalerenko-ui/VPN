# Proxy Aggregation Project - Stage 7 (Runtime Speed Test)

## Что делает Stage 7
Stage 7 добавляет прикладной speed test поверх уже существующего Stage 5/6 runtime path:
- после успешного connect и получения `exit_ip` запускается HTTP speed test;
- speed test идет через тот же локальный sing-box inbound, который использовался для `exit_ip`;
- в `proxy_checks` сохраняются:
  - `first_byte_ms`
  - `download_mbps`
- если speed test не удался, connect-успех не ломается:
  - `connect_ok` остается `true`;
  - `first_byte_ms` и `download_mbps` остаются `NULL`;
  - ошибка speed test только логируется.

## Что уже покрыто в Stage 0-7
- Postgres + миграции
- fetcher -> `source_snapshots`
- parser -> `proxy_candidates`
- prober (sing-box runtime) -> `connect_ok`, `connect_ms`, `exit_ip`
- geo layer -> `exit_country`, `geo_match`
- speed layer -> `first_byte_ms`, `download_mbps`

## Что НЕ входит в Stage 7
- scorer
- `proxy_state` updates
- exporter
- HTTP API
- scheduler / очереди

## Требования
- Docker + Docker Compose v2
- Python 3.11+
- установленный бинарь `sing-box` (через PATH или явный путь в `SINGBOX_BINARY`)

## Переменные окружения
### Prober + Geo
- `PROBE_BATCH_SIZE`
- `CONNECT_TIMEOUT_SECONDS`
- `DOWNLOAD_TIMEOUT_SECONDS`
- `SINGBOX_BINARY`
- `PROBER_LOCAL_BIND_HOST`
- `PROBER_BASE_LOCAL_PORT`
- `PROBER_PROCESS_START_TIMEOUT_SECONDS`
- `PROBER_EXIT_IP_URL`
- `GEO_PROVIDER_PRIMARY`
- `GEO_PROVIDER_FALLBACK`
- `GEO_REQUEST_TIMEOUT_SECONDS`
- `GEO_IP_API_BASE_URL`
- `GEO_IPWHOIS_BASE_URL`

### Speed Test (Stage 7)
- `SPEED_TEST_URL`
- `SPEED_TEST_MAX_BYTES`
- `SPEED_TEST_CHUNK_SIZE`

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

## Полный цикл Stage 7
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

7. Запустить prober (connect + geo + speed):
```bash
python -m app.prober.main
```

## SQL-проверки Stage 7
### 1) Count записей, где `first_byte_ms IS NOT NULL`
```bash
docker compose exec -T db sh -lc 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "SELECT COUNT(*) AS checks_with_first_byte FROM proxy_checks WHERE first_byte_ms IS NOT NULL;"'
```

### 2) Count записей, где `download_mbps IS NOT NULL`
```bash
docker compose exec -T db sh -lc 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "SELECT COUNT(*) AS checks_with_download_mbps FROM proxy_checks WHERE download_mbps IS NOT NULL;"'
```

### 3) Последние 20 успешных speed test
```bash
docker compose exec -T db sh -lc 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "SELECT checked_at, candidate_id, connect_ms, first_byte_ms, download_mbps, exit_ip, exit_country FROM proxy_checks WHERE connect_ok = TRUE AND first_byte_ms IS NOT NULL AND download_mbps IS NOT NULL ORDER BY checked_at DESC LIMIT 20;"'
```

### 4) Top 20 по `download_mbps`
```bash
docker compose exec -T db sh -lc 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "SELECT checked_at, candidate_id, connect_ms, first_byte_ms, download_mbps, exit_ip, exit_country FROM proxy_checks WHERE download_mbps IS NOT NULL ORDER BY download_mbps DESC, checked_at DESC LIMIT 20;"'
```

### 5) Успешный connect без speed metrics
```bash
docker compose exec -T db sh -lc 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "SELECT checked_at, candidate_id, connect_ms, first_byte_ms, download_mbps, exit_ip, exit_country, error_code FROM proxy_checks WHERE connect_ok = TRUE AND (first_byte_ms IS NULL OR download_mbps IS NULL) ORDER BY checked_at DESC LIMIT 50;"'
```
