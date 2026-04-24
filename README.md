# Proxy Aggregation Project - Stage 2

## Что добавляет Stage 2
Stage 2 добавляет общий foundation layer в `app/common`:
- централизованные настройки из env (`settings.py`);
- единое подключение к PostgreSQL через SQLAlchemy 2.x (`db.py`);
- базовые enum и typed DTO-модели (`enums.py`, `models.py`);
- единый structured logging helper (`logging.py`);
- smoke-check CLI команды (`cli_show_settings.py`, `cli_check_db.py`).

На этом этапе **не** реализуются fetcher/parser/prober/scorer/exporter/API/scheduler и pipeline-бизнес-логика.

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

## 5. Smoke-check Stage 2
### Проверка settings
```bash
python -m app.common.cli_show_settings
```

### Проверка подключения к БД
```bash
python -m app.common.cli_check_db
```

## 6. Быстрые проверки результата
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
