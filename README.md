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
PUBLISH_AUTH_MODE=auto
GITHUB_TOKEN=ghp_xxx
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

## Speed measurement
The prober keeps connect/exit-IP success separate from throughput quality. A proxy can be `connect_ok=true` even when speed measurement is unavailable.

Speed testing is deterministic and bounded:
- `SPEED_TEST_URLS` is a comma-separated primary + fallback endpoint list.
- `SPEED_TEST_URL` is still supported as a legacy fallback.
- `SPEED_TEST_ATTEMPTS` controls total bounded attempts; every configured endpoint is tried at least once until up to three successful samples are collected.
- successful samples are aggregated with median `first_byte_ms` and median `download_mbps`.
- `SPEED_TEST_CONNECT_TIMEOUT_SECONDS`, `SPEED_TEST_READ_TIMEOUT_SECONDS`, `SPEED_TEST_MAX_BYTES`, and `SPEED_TEST_CHUNK_SIZE` keep each candidate probe bounded.

Default endpoints:
```dotenv
SPEED_TEST_URLS=http://cachefly.cachefly.net/1mb.test,https://speed.cloudflare.com/__down?bytes=1048576,https://proof.ovh.net/files/1Mb.dat
SPEED_TEST_URL=http://cachefly.cachefly.net/1mb.test
```

When throughput cannot be measured, `proxy_checks` stores speed diagnostics without changing `connect_ok`:
- `speed_error_code`: aggregate state such as `speed_all_endpoints_failed`;
- `speed_failure_reason`: dominant underlying reason such as `speed_timeout`, `speed_tls_error`, `speed_http_error`, `speed_empty_body`, `speed_invalid_response`, or `speed_unexpected_error`;
- `speed_error_text`: bounded per-attempt explanation;
- `speed_endpoint_url`, `speed_attempts`, `speed_successes`.

`download_mbps=null` now means: connect may have worked, but no valid throughput sample was produced. Check `speed_failure_reason` and `speed_error_text` to distinguish a broken endpoint, TLS/cert failure, timeout, empty body, invalid response, or another technical measurement failure.

Unexpected speed-layer exceptions are normalized as speed diagnostics instead of disappearing into empty latest rows. Fresh checks that reached speed measurement should not have `connect_ok=true`, `download_mbps=null`, `speed_attempts=0`, `speed_error_code=null`, and `speed_failure_reason=null` at the same time.

Older rows created before speed diagnostics existed can still have that shape. Treat them as legacy/uninstrumented latest checks until a controlled re-probe refreshes them.

Quick speed diagnostics:
```bash
docker compose exec pipeline-runner python -m app.common.cli_speed_diagnostics

docker compose exec -T api sh -lc 'PGPASSWORD="$POSTGRES_PASSWORD" psql -h "$POSTGRES_HOST" -p "$POSTGRES_PORT" -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "
WITH latest_checks AS (
  SELECT DISTINCT ON (candidate_id)
    candidate_id, connect_ok, download_mbps, speed_attempts, speed_error_code, speed_failure_reason
  FROM proxy_checks
  ORDER BY candidate_id, checked_at DESC, id DESC
)
SELECT
  count(*) AS latest_checks,
  count(*) FILTER (WHERE connect_ok) AS connect_ok,
  count(*) FILTER (
    WHERE connect_ok
      AND download_mbps IS NULL
      AND coalesce(speed_attempts, 0) = 0
      AND speed_error_code IS NULL
      AND speed_failure_reason IS NULL
  ) AS empty_speed_null_without_reason,
  count(*) FILTER (
    WHERE connect_ok
      AND (
        download_mbps IS NOT NULL
        OR coalesce(speed_attempts, 0) > 0
        OR speed_error_code IS NOT NULL
        OR speed_failure_reason IS NOT NULL
      )
  ) AS new_speed_semantics,
  count(*) FILTER (WHERE connect_ok AND download_mbps IS NOT NULL) AS speed_measured,
  count(*) FILTER (WHERE connect_ok AND download_mbps IS NULL) AS speed_unavailable
FROM latest_checks;"'

docker compose exec -T api sh -lc 'PGPASSWORD="$POSTGRES_PASSWORD" psql -h "$POSTGRES_HOST" -p "$POSTGRES_PORT" -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "
WITH latest_checks AS (
  SELECT DISTINCT ON (candidate_id)
    candidate_id, connect_ok, download_mbps, speed_error_code, speed_failure_reason
  FROM proxy_checks
  ORDER BY candidate_id, checked_at DESC, id DESC
)
SELECT
  coalesce(speed_error_code, 'speed_error_code_null') AS speed_error_code,
  count(*) AS count
FROM latest_checks
GROUP BY 1
ORDER BY count DESC, speed_error_code;"'
```

Debug JSON and `export_manifest.json` include a `speed_quality` summary with measured/unavailable counts, `legacy_empty_speed_diagnostics`, `speed_new_format`, speed semantics, and speed error-code breakdowns.

### Aggregated state vs latest check
Exporter selection and ranking still use `proxy_state`. Debug JSON is explicit about the two different sources:
- `state_download_mbps`, `state_latency_ms`, `state_freshness_score`, `state_geo_confidence` come from aggregated `proxy_state`;
- `latest_check_checked_at`, `latest_check_download_mbps`, `latest_check_first_byte_ms`, `latest_check_speed_attempts`, `latest_check_speed_successes`, `latest_check_speed_error_code`, `latest_check_speed_failure_reason`, `latest_check_speed_error_text`, and `latest_check_speed_endpoint_url` come from the most recent `proxy_checks` row;
- `latest_check_speed_semantics=legacy_no_speed_diagnostics` means the latest row predates, or otherwise lacks, modern speed diagnostics and should be refreshed by re-probe before being interpreted as a current speed failure.

The older top-level debug fields `download_mbps`, `latency_ms`, `freshness_score`, `geo_confidence`, and nested `speed_diagnostics` are kept for backward compatibility, but operators should prefer the explicit `state_*` and `latest_check_*` names.

## Output fallback policy (last-good)
Exporter keeps strict `active` selection as primary source.
If current cycle has zero active candidates:
- exporter reuses previous non-empty TXT exports from `output/`;
- manifest marks `fallback_used=true` and includes `fallback_reason`.

If there is no previous non-empty output, exporter writes current (empty) selection and records reason in manifest.

## Debug export artifacts
Exporter now writes two artifact types side-by-side in `output/`:
- client-facing TXT exports: `BLACK-ETALON.txt`, `WHITE-CIDR-ETALON.txt`, `WHITE-SNI-ETALON.txt`, `ALL-ETALON.txt`;
- explainability JSON exports: `BLACK-ETALON-debug.json`, `WHITE-CIDR-ETALON-debug.json`, `WHITE-SNI-ETALON-debug.json`, `ALL-ETALON-debug.json`.

TXT stays unchanged and remains the distribution format for clients.
Debug JSON is for operator analysis and includes:
- `summary` counters (considered/selected/limits/skip reasons);
- `speed_quality` counters for latest checks;
- ordered `items` with `selection_position`, `raw_config`, grouping keys, aggregated `state_*` metrics, and concrete `latest_check_*` diagnostics.

Quick checks (host CLI):
```bash
# list generated export artifacts
ls -la output

# inspect one debug file
cat output/BLACK-ETALON-debug.json

# verify debug JSON separates aggregated state from latest speed diagnostics
python -c "import json, pathlib; data=json.loads(pathlib.Path('output/BLACK-ETALON-debug.json').read_text(encoding='utf-8')); item=(data['items'] or [{}])[0]; print({k:item.get(k) for k in ('state_download_mbps','state_latency_ms','latest_check_checked_at','latest_check_download_mbps','latest_check_speed_attempts','latest_check_speed_error_code','latest_check_speed_semantics')})"

# count debug items
python -c "import json, pathlib; p=pathlib.Path('output/BLACK-ETALON-debug.json'); print(len(json.loads(p.read_text(encoding='utf-8'))['items']))"

# compare TXT line count vs debug items count
python -c "import json, pathlib; txt=[x.strip() for x in pathlib.Path('output/BLACK-ETALON.txt').read_text(encoding='utf-8').splitlines() if x.strip()]; dbg=json.loads(pathlib.Path('output/BLACK-ETALON-debug.json').read_text(encoding='utf-8'))['items']; print({'txt_lines': len(txt), 'debug_items': len(dbg)})"

# verify 1:1 ordering and positions between TXT and debug JSON
python -c "import json, pathlib; txt=[x.strip() for x in pathlib.Path('output/BLACK-ETALON.txt').read_text(encoding='utf-8').splitlines() if x.strip()]; items=json.loads(pathlib.Path('output/BLACK-ETALON-debug.json').read_text(encoding='utf-8'))['items']; ok=len(txt)==len(items) and all(items[i]['selection_position']==i+1 and items[i]['raw_config']==txt[i] for i in range(len(items))); print('OK' if ok else 'MISMATCH')"
```

## Git publication behavior
Publication is implemented in `app/publisher/git_publish.py` and is disabled by default (`PUBLISH_ENABLED=false`).

When enabled:
- stages `output/BLACK-ETALON.txt`, `output/WHITE-CIDR-ETALON.txt`, `output/WHITE-SNI-ETALON.txt`, `output/ALL-ETALON.txt`, `output/export_manifest.json`;
- commits only when content changed;
- pushes `HEAD` to `${PUBLISH_REMOTE}:${PUBLISH_BRANCH}`.

Required for successful push:
- valid git remote access from runtime environment;
- repository credentials/token configured for non-interactive push.

## Production-like publish setup
Use non-interactive auth for `pipeline-runner`:

```dotenv
PUBLISH_ENABLED=true
PUBLISH_REMOTE=origin
PUBLISH_BRANCH=main
PUBLISH_AUTH_MODE=auto
GITHUB_TOKEN=ghp_xxx
# GH_TOKEN=ghp_xxx (alternative to GITHUB_TOKEN)
```

Publisher auth modes:
- `PUBLISH_AUTH_MODE=auto` (default): for HTTPS remotes, tries `GITHUB_TOKEN` then `GH_TOKEN`; for SSH remotes uses standard SSH environment.
- `PUBLISH_AUTH_MODE=https_token`: requires `GITHUB_TOKEN` or `GH_TOKEN`; fails fast if token is missing.
- `PUBLISH_AUTH_MODE=ssh`: do not use token auth, rely on SSH remote + mounted key + `GIT_SSH_COMMAND`.
- `PUBLISH_AUTH_MODE=none`: skip all auth helpers and use plain git environment.

The HTTPS token path uses a temporary runtime `GIT_ASKPASS` helper inside the container (not stored in repo files), so push stays non-interactive.

Smoke check for publication:
```bash
docker compose up -d --build
docker compose exec -T -e PUBLISH_ENABLED=true pipeline-runner python - <<'PY'
from app.orchestrator.service import run_pipeline_cycle
print(run_pipeline_cycle().to_log_extra())
PY
```
Then verify:
- orchestrator logs contain successful publisher step;
- `git ls-remote --heads origin main` shows updated commit;
- second publish run without changes returns `no_changes`.

## Security note: do not commit secrets
- Never commit `.env` with real tokens.
- Keep `GITHUB_TOKEN` / `GH_TOKEN` only in runtime environment or local `.env` excluded from git.
- Do not paste `docker compose config` output publicly when it contains runtime secrets.

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
