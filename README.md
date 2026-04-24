# Proxy Aggregation Project - Stage 3

## Что добавляет Stage 3
Stage 3 добавляет минимальный fetcher для upstream TXT-источников:
- чтение активных `sources` из БД;
- скачивание raw text по URL;
- вычисление SHA-256 checksum;
- сравнение с `sources.last_checksum`;
- создание записи в `source_snapshots` только при изменении содержимого;
- обновление `sources.last_fetched_at` при каждом успешном fetch;
- обновление `sources.last_checksum` при изменении;
- CLI entrypoint: `python -m app.fetcher.main`.

На этом этапе **не** реализуются parser/prober/scorer/exporter/API/scheduler и selection-бизнес-логика.

## Структура проекта
```text
app/
  api/
  common/
    __init__.py
    settings.py
    db.py
    enums.py
    models.py
    logging.py
    cli_show_settings.py
    cli_check_db.py
  exporter/
  fetcher/
    __init__.py
    main.py
    service.py
  parser/
  prober/
  scorer/
sql/
  migrations/
    env.py
    script.py.mako
    versions/
      20260424_0001_initial_schema.py
  seeds/
    001_sources.sql
data/
  logs/
  snapshots/
output/
docker-compose.yml
.env.example
alembic.ini
requirements.txt
README.md
```

## Предварительные требования
- Docker + Docker Compose v2
- Python 3.11+

## 1. Установить Python-зависимости
```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Для Windows PowerShell:
```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

## 2. Поднять PostgreSQL
1. Создать локальный `.env`:
   ```bash
   cp .env.example .env
   ```
   Для Windows PowerShell:
   ```powershell
   Copy-Item .env.example .env
   ```
2. Запустить БД:
   ```bash
   docker compose up -d db
   ```
3. Проверить health контейнера:
   ```bash
   docker inspect --format='{{.State.Health.Status}}' "$(docker compose ps -q db)"
   ```

## 3. Прогнать миграции
```bash
alembic -c alembic.ini upgrade head
```

Примечания:
- Alembic использует `DATABASE_URL`, а если переменная не задана, DSN собирается из `POSTGRES_*`.
- По умолчанию host берется как `127.0.0.1`, порт из `POSTGRES_PORT`.

## 4. Применить seed (`sources`)
```bash
docker compose exec -T db sh -lc 'psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB"' < sql/seeds/001_sources.sql
```

Для Windows PowerShell:
```powershell
Get-Content sql/seeds/001_sources.sql | docker compose exec -T db sh -lc 'psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB"'
```

## 5. Smoke-check foundation layer
### Проверка settings
```bash
python -m app.common.cli_show_settings
```

### Проверка подключения к БД
```bash
python -m app.common.cli_check_db
```

## 6. Запуск fetcher (Stage 3)
```bash
python -m app.fetcher.main
```

## 7. Проверки Stage 3
### Проверка таблиц
```bash
docker compose exec -T db sh -lc 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "\\dt"'
```

### Проверка Alembic revision
```bash
docker compose exec -T db sh -lc 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "SELECT version_num FROM alembic_version;"'
```

### Проверка seed-данных `sources`
```bash
docker compose exec -T db sh -lc 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "SELECT name, family, url, is_active FROM sources ORDER BY name;"'
```

Ожидаемо: 3 строки (`BLACK_VLESS_RUS`, `WHITE_CIDR_RU_ALL`, `WHITE_SNI_RU_ALL`).

### Проверка `last_fetched_at` и `last_checksum` в `sources`
```bash
docker compose exec -T db sh -lc 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "SELECT name, is_active, last_fetched_at, left(last_checksum, 16) AS checksum_prefix FROM sources ORDER BY name;"'
```

### Проверка записей `source_snapshots`
```bash
docker compose exec -T db sh -lc 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "SELECT source_id, fetched_at, left(checksum, 16) AS checksum_prefix, length(raw_content) AS content_len FROM source_snapshots ORDER BY fetched_at DESC LIMIT 20;"'
```

## 8. Команды Stage 3 (PowerShell, полный прогон)
```powershell
# 1) Установка зависимостей
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt

# 2) Поднять БД
Copy-Item .env.example .env -ErrorAction SilentlyContinue
docker compose up -d db

# 3) Миграции
alembic -c alembic.ini upgrade head

# 4) Seed
Get-Content sql/seeds/001_sources.sql | docker compose exec -T db sh -lc 'psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB"'

# 5) Запуск fetcher
python -m app.fetcher.main

# 6) Проверка sources
docker compose exec -T db sh -lc 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "SELECT name, last_fetched_at, last_checksum FROM sources ORDER BY name;"'

# 7) Проверка source_snapshots
docker compose exec -T db sh -lc 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "SELECT source_id, fetched_at, checksum FROM source_snapshots ORDER BY fetched_at DESC LIMIT 20;"'
```
