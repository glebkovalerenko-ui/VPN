# Proxy Aggregation Project - Stage 11 (Runtime Hardening + Orchestration)

Stage 11 turns the repository into a runnable deployment pipeline:
- multi-container runtime in `docker-compose.yml`;
- automated DB init (`alembic` + source seed);
- continuous orchestration loop (`fetcher -> parser -> prober -> scorer -> exporter`);
- `sing-box` bundled inside app container image;
- shared `output_data` volume between `pipeline-runner` and `api`;
- optional auto-publication of TXT exports to Git remote.

## Runtime topology
`docker compose up -d --build` starts:
- `db` - Postgres 16
- `db-init` - one-shot migrations + seed (`sql/seeds/001_sources.sql`)
- `pipeline-runner` - Stage 11 orchestrator loop
- `api` - read-only HTTP API over DB + `output/`

## Quick start (Linux/macOS)
1. Create env file:
```bash
cp .env.example .env
```

2. Optional: enable Git publication from `pipeline-runner`:
```dotenv
PUBLISH_ENABLED=true
PUBLISH_REMOTE=origin
PUBLISH_BRANCH=main
PUBLISH_GIT_AUTHOR_NAME=proxy-mvp-bot
PUBLISH_GIT_AUTHOR_EMAIL=proxy-mvp-bot@users.noreply.github.com
```

3. Start full stack:
```bash
docker compose up -d --build
```

4. Check container status:
```bash
docker compose ps
```

5. Check orchestrator logs:
```bash
docker compose logs -f pipeline-runner
```

6. Check API health:
```bash
curl -s http://127.0.0.1:8000/health
```

7. Inspect generated output:
```bash
docker compose exec pipeline-runner ls -la /app/output
docker compose exec pipeline-runner cat /app/output/export_manifest.json
```

## Quick start (Windows PowerShell)
1. Create env file:
```powershell
Copy-Item .env.example .env
```

2. Start full stack:
```powershell
docker compose up -d --build
```

3. Check status/logs:
```powershell
docker compose ps
docker compose logs -f pipeline-runner
```

4. Check API/output:
```powershell
Invoke-RestMethod http://127.0.0.1:8000/health
docker compose exec pipeline-runner ls -la /app/output
docker compose exec pipeline-runner cat /app/output/export_manifest.json
```

## Environment notes
- `.env.example` is compose-first and sets `POSTGRES_HOST=db`.
- For host-local CLI runs (outside Docker), set `POSTGRES_HOST=127.0.0.1`.
- If local ports are occupied, override `POSTGRES_PORT` and/or `API_PORT` before `docker compose up`.
- `output/` inside containers is shared via Compose named volume `output_data`.
- `FETCH_INTERVAL_MINUTES` controls orchestrator cycle interval.
- `ORCHESTRATOR_STARTUP_DELAY_SECONDS` delays first cycle after container start.
- `ORCHESTRATOR_EXIT_ON_FAILURE=true` makes runner exit on first failed cycle.

## Output fallback policy (last-good)
Exporter keeps strict `active` selection as primary source.
If current cycle has zero active candidates:
- exporter reuses previous non-empty TXT exports from `output/`;
- manifest marks `fallback_used=true` and includes `fallback_reason`.

If there is no previous non-empty output, exporter writes current (empty) selection and records reason in manifest.

## Git publication behavior
Publication is implemented in `app/publisher/git_publish.py` and is disabled by default (`PUBLISH_ENABLED=false`).

When enabled:
- stages `output/BLACK-ETALON.txt`, `output/WHITE-CIDR-ETALON.txt`, `output/WHITE-SNI-ETALON.txt`, `output/ALL-ETALON.txt`, `output/export_manifest.json`;
- commits only when content changed;
- pushes `HEAD` to `${PUBLISH_REMOTE}:${PUBLISH_BRANCH}`.

Required for successful push:
- valid git remote access from runtime environment;
- repository credentials/token configured for non-interactive push.

## API endpoints
- `GET /health`
- `GET /state/top`
- `GET /state/candidates`
- `GET /exports/manifest`
- `GET /exports/files/{file_name}`

## Manual stage commands (debug only)
The Stage 11 default is orchestrated runtime. Individual commands remain available:
```bash
python -m app.fetcher.main
python -m app.parser.main
python -m app.prober.main
python -m app.scorer.main
python -m app.exporter.main
uvicorn app.api.main:app --host 0.0.0.0 --port 8000
```

## Typical operational checks
Database connectivity:
```bash
python -m app.common.cli_check_db
```

Settings snapshot:
```bash
python -m app.common.cli_show_settings
```

OpenAPI docs:
- `http://127.0.0.1:8000/docs`
- `http://127.0.0.1:8000/redoc`
