# Proxy Aggregation Project - Stage 10 (Minimal HTTP API)

## What Stage 10 adds
Stage 10 introduces a minimal read-only HTTP API on top of already prepared data and export artifacts.

API behavior:
- reads state from `proxy_state` + `proxy_candidates`;
- reads manifest and TXT files from `output/`;
- does not recalculate quality;
- does not trigger fetcher/parser/prober/scorer/exporter;
- has no auth/tokens/user cabinet in this stage.

## Stage 0-9 coverage
- Postgres + migrations
- fetcher -> `source_snapshots`
- parser -> `proxy_candidates`
- prober -> `proxy_checks`
- scorer -> `proxy_state`
- exporter -> `output/*.txt` + `output/export_manifest.json`

## Out of scope for Stage 10
- scheduler/queues
- subscription server
- authentication/authorization
- automatic pipeline execution from API

## Install dependencies
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

## Full pipeline through Stage 9 (data preparation)
1. Prepare `.env`:
```bash
cp .env.example .env
```

Windows PowerShell:
```powershell
Copy-Item .env.example .env
```

2. Start Postgres:
```bash
docker compose up -d db
```

3. Run migrations:
```bash
alembic -c alembic.ini upgrade head
```

4. Seed sources:
```bash
docker compose exec -T db sh -lc 'psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB"' < sql/seeds/001_sources.sql
```

Windows PowerShell:
```powershell
Get-Content sql/seeds/001_sources.sql | docker compose exec -T db sh -lc 'psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB"'
```

5. Run fetcher:
```bash
python -m app.fetcher.main
```

6. Run parser:
```bash
python -m app.parser.main
```

7. Run prober:
```bash
python -m app.prober.main
```

8. Run scorer:
```bash
python -m app.scorer.main
```

9. Run exporter:
```bash
python -m app.exporter.main
```

## Run Stage 10 API
```bash
uvicorn app.api.main:app --host 0.0.0.0 --port 8000
```

Alternative:
```bash
python -m app.api.main
```

OpenAPI docs (local):
- `http://127.0.0.1:8000/docs`
- `http://127.0.0.1:8000/redoc`

## API endpoints
- `GET /health`
- `GET /state/top`
- `GET /state/candidates`
- `GET /exports/manifest`
- `GET /exports/files/{file_name}`

### `GET /health`
Returns service and dependency readiness:
```json
{
  "status": "ok",
  "db": "ok",
  "output_dir_exists": true,
  "manifest_exists": true,
  "db_error": null
}
```

### `GET /state/top`
Top ranked rows from `proxy_state` (joined with `proxy_candidates`).

Query params:
- `limit` (default `50`, max `500`)
- `status` (default `active`)
- `family` (`black`, `white_cidr`, `white_sni`)
- `country` (ISO country code, case-insensitive input)
- `only_positive_score` (default `true`)

### `GET /state/candidates`
Filtered/paginated candidate list.

Query params:
- `limit` (default `100`, max `1000`)
- `offset` (default `0`)
- `status` (`active`, `degraded`, `dead`, `unknown`)
- `family` (`black`, `white_cidr`, `white_sni`)
- `protocol` (exact protocol match)
- `country` (ISO country code, case-insensitive input)
- `enabled_only` (`true`/`false`)
- `min_final_score` (decimal threshold)
- `only_positive_score` (`true`/`false`)

### `GET /exports/manifest`
Returns `output/export_manifest.json` as JSON. If manifest is missing, endpoint returns `404`.

### `GET /exports/files/{file_name}`
Downloads one TXT export file from `output/`.

Examples:
- `/exports/files/BLACK-ETALON.txt`
- `/exports/files/WHITE-CIDR-ETALON.txt`
- `/exports/files/WHITE-SNI-ETALON.txt`
- `/exports/files/ALL-ETALON.txt`

Only `.txt` files are allowed on this endpoint.

## API checks (curl)
Health:
```bash
curl -s http://127.0.0.1:8000/health
```

Top active proxies:
```bash
curl -s "http://127.0.0.1:8000/state/top?limit=20&status=active"
```

Filtered candidates:
```bash
curl -s "http://127.0.0.1:8000/state/candidates?family=white_sni&enabled_only=true&limit=30"
```

Manifest:
```bash
curl -s http://127.0.0.1:8000/exports/manifest
```

Download TXT:
```bash
curl -fL "http://127.0.0.1:8000/exports/files/ALL-ETALON.txt" -o ALL-ETALON.txt
```

## API checks (Windows PowerShell)
Health:
```powershell
Invoke-RestMethod http://127.0.0.1:8000/health
```

Top active proxies:
```powershell
Invoke-RestMethod "http://127.0.0.1:8000/state/top?limit=20&status=active"
```

Filtered candidates:
```powershell
Invoke-RestMethod "http://127.0.0.1:8000/state/candidates?family=white_sni&enabled_only=true&limit=30"
```

Manifest:
```powershell
Invoke-RestMethod http://127.0.0.1:8000/exports/manifest
```

Download TXT:
```powershell
Invoke-WebRequest "http://127.0.0.1:8000/exports/files/ALL-ETALON.txt" -OutFile "ALL-ETALON.txt"
```

## Output checks (optional)
List output files:
```bash
ls -la output
```

Windows PowerShell:
```powershell
Get-ChildItem output
```

Inspect manifest:
```bash
cat output/export_manifest.json
```

Windows PowerShell:
```powershell
Get-Content output/export_manifest.json
```
