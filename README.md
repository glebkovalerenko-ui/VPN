# Proxy Aggregation Project - Stage 4

## Что добавляет Stage 4
Stage 4 добавляет parser для upstream snapshot-данных:
- чтение только последних (`latest`) snapshot по каждому активному `source`;
- построчный разбор TXT;
- пропуск пустых, header-like и мусорных строк;
- распознавание proxy-конфигов по схемам:
  - `vless://`
  - `vmess://`
  - `trojan://`
  - `ss://`
  - `hysteria2://` и `hy2://`
  - `tuic://`
  - `socks://`
  - `http://` (только как proxy line, не как обычный URL);
- нормализацию полей и запись в `proxy_candidates`;
- дедупликацию по детерминированному `fingerprint` (SHA-256);
- идемпотентный повторный запуск:
  - без создания дублей;
  - с обновлением `last_seen_at` для уже известных записей;
  - с сохранением `first_seen_at` только при первом появлении.

На этом этапе **не** реализуются `prober`, `scorer`, `exporter`, HTTP API, scheduler и бизнес-логика выбора лучших конфигов.

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
    __init__.py
    main.py
    service.py
    parsers.py
    fingerprint.py
    utils.py
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

## 1) Установка зависимостей
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

## 2) Запуск PostgreSQL
1. Создать локальный `.env`:
   ```bash
   cp .env.example .env
   ```
   Для Windows PowerShell:
   ```powershell
   Copy-Item .env.example .env
   ```
2. Поднять БД:
   ```bash
   docker compose up -d db
   ```
3. Проверить health:
   ```bash
   docker inspect --format='{{.State.Health.Status}}' "$(docker compose ps -q db)"
   ```

## 3) Миграции
```bash
alembic -c alembic.ini upgrade head
```

## 4) Seed данных (`sources`)
```bash
docker compose exec -T db sh -lc 'psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB"' < sql/seeds/001_sources.sql
```

Для Windows PowerShell:
```powershell
Get-Content sql/seeds/001_sources.sql | docker compose exec -T db sh -lc 'psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB"'
```

## 5) Запуск fetcher (Stage 3)
```bash
python -m app.fetcher.main
```

## 6) Запуск parser (Stage 4)
```bash
python -m app.parser.main
```

В логах parser выводит итоговую статистику:
- `sources_seen`
- `snapshots_seen`
- `lines_total`
- `lines_skipped`
- `candidates_inserted`
- `candidates_updated`
- `parse_errors`

## 7) SQL-проверки Stage 4
Проверки выполняются после `fetcher` и `parser`.

### 7.1 Количество записей в `proxy_candidates`
```bash
docker compose exec -T db sh -lc 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "SELECT COUNT(*) AS proxy_candidates_count FROM proxy_candidates;"'
```

### 7.2 Примеры распарсенных записей
```bash
docker compose exec -T db sh -lc 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "SELECT protocol, host, port, sni, family, source_country_tag, left(fingerprint, 16) AS fp_prefix, first_seen_at, last_seen_at FROM proxy_candidates ORDER BY last_seen_at DESC LIMIT 20;"'
```

### 7.3 Проверка отсутствия дублей по `fingerprint`
```bash
docker compose exec -T db sh -lc 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "SELECT fingerprint, COUNT(*) AS cnt FROM proxy_candidates GROUP BY fingerprint HAVING COUNT(*) > 1;"'
```

Ожидаемо: 0 строк.

### 7.4 Проверка идемпотентности parser
1. Повторно запустить parser:
   ```bash
   python -m app.parser.main
   ```
2. Снова проверить count:
   ```bash
   docker compose exec -T db sh -lc 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "SELECT COUNT(*) AS proxy_candidates_count FROM proxy_candidates;"'
   ```

Ожидаемо:
- `proxy_candidates_count` не растет на одном и том же наборе `latest snapshots`;
- в логах parser увеличивается `candidates_updated` и не растут искусственные дубликаты.

## Полный прогон (PowerShell)
```powershell
# 1) Зависимости
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt

# 2) БД
Copy-Item .env.example .env -ErrorAction SilentlyContinue
docker compose up -d db

# 3) Миграции
alembic -c alembic.ini upgrade head

# 4) Seed
Get-Content sql/seeds/001_sources.sql | docker compose exec -T db sh -lc 'psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB"'

# 5) Fetch snapshots
python -m app.fetcher.main

# 6) Parse snapshots into proxy_candidates
python -m app.parser.main

# 7) Count candidates
docker compose exec -T db sh -lc 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "SELECT COUNT(*) AS proxy_candidates_count FROM proxy_candidates;"'

# 8) Sample rows
docker compose exec -T db sh -lc 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "SELECT protocol, host, port, sni, family, source_country_tag, left(fingerprint, 16) AS fp_prefix, first_seen_at, last_seen_at FROM proxy_candidates ORDER BY last_seen_at DESC LIMIT 20;"'

# 9) Duplicate fingerprints check
docker compose exec -T db sh -lc 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "SELECT fingerprint, COUNT(*) AS cnt FROM proxy_candidates GROUP BY fingerprint HAVING COUNT(*) > 1;"'
```
