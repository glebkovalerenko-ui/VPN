# Proxy Aggregation Project - Stage 8 (Scorer + proxy_state)

## Что делает Stage 8
Stage 8 добавляет агрегированный слой `proxy_state` поверх истории `proxy_checks`:
- scorer читает `proxy_candidates` и последние проверки из `proxy_checks`;
- вычисляет текущее состояние кандидата (status/latency/speed/stability/freshness/final_score);
- делает upsert в `proxy_state`;
- не пишет обратно в `proxy_checks` (история read-only для scorer);
- пересчёт идемпотентный и полностью воспроизводимый;
- полностью пересчитывает ранги:
  - `rank_global`
  - `rank_in_family`
  - `rank_in_country`

## Что уже покрыто Stage 0-7
- Postgres + миграции
- fetcher -> `source_snapshots`
- parser -> `proxy_candidates`
- prober (sing-box runtime) -> `proxy_checks`:
  - `connect_ok`
  - `connect_ms`
  - `first_byte_ms`
  - `download_mbps`
  - `exit_ip`
  - `exit_country`
  - `geo_match`

## Что не входит в Stage 8
- exporter
- HTTP API
- scheduler/очереди

## Переменные окружения для scorer
Минимально важные ручки Stage 8:
- `SCORER_RECENT_CHECKS_LIMIT`
- `SCORER_MIN_ACTIVE_STABILITY`
- `SCORER_MIN_DEGRADED_STABILITY`
- `SCORER_LATENCY_GOOD_MS`
- `SCORER_LATENCY_BAD_MS`
- `SCORER_SPEED_GOOD_MBPS`
- `SCORER_SPEED_BAD_MBPS`
- `SCORER_GEO_NEUTRAL_SCORE`
- `SCORER_MIN_ACTIVE_FRESHNESS`
- `SCORER_DEAD_FRESHNESS_MAX`
- `SCORER_FRESHNESS_PENALTY_WEIGHT`
- `SCORER_MISSING_SPEED_PENALTY`

Scorer также использует общие параметры свежести:
- `CHECK_FRESHNESS_MINUTES`
- `MAX_SELECTION_AGE_MINUTES`

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

## Полный цикл Stage 8
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

7. Запустить prober:
```bash
python -m app.prober.main
```

8. Запустить scorer:
```bash
python -m app.scorer.main
```

## SQL-проверки Stage 8
### 1) Count записей в `proxy_state`
```bash
docker compose exec -T db sh -lc 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "SELECT COUNT(*) AS proxy_state_rows FROM proxy_state;"'
```

### 2) Distribution по `status`
```bash
docker compose exec -T db sh -lc 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "SELECT status, COUNT(*) AS cnt FROM proxy_state GROUP BY status ORDER BY status;"'
```

### 3) Sample rows из `proxy_state`
```bash
docker compose exec -T db sh -lc 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "SELECT candidate_id, status, current_country, latency_ms, download_mbps, stability_ratio, geo_confidence, freshness_score, final_score FROM proxy_state ORDER BY updated_at DESC LIMIT 20;"'
```

### 4) Top 20 global
```bash
docker compose exec -T db sh -lc 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "SELECT candidate_id, status, final_score, rank_global FROM proxy_state WHERE rank_global IS NOT NULL ORDER BY rank_global ASC LIMIT 20;"'
```

### 5) Top 20 by family
```bash
docker compose exec -T db sh -lc 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "SELECT ps.candidate_id, pc.family, ps.status, ps.final_score, ps.rank_in_family FROM proxy_state ps JOIN proxy_candidates pc ON pc.id = ps.candidate_id WHERE ps.rank_in_family IS NOT NULL ORDER BY pc.family ASC, ps.rank_in_family ASC LIMIT 20;"'
```

### 6) Top 20 by country
```bash
docker compose exec -T db sh -lc 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "SELECT candidate_id, current_country, status, final_score, rank_in_country FROM proxy_state WHERE current_country IS NOT NULL AND rank_in_country IS NOT NULL ORDER BY current_country ASC, rank_in_country ASC LIMIT 20;"'
```
