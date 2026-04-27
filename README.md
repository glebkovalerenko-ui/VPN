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
- Curated export hardening is controlled by `EXPORT_MAX_PER_COUNTRY`, `EXPORT_MAX_PER_HOST`,
  `EXPORT_MAX_LATENCY_MS`, `EXPORT_MAX_FIRST_BYTE_MS`, `EXPORT_MIN_DOWNLOAD_MBPS`,
  `EXPORT_REQUIRE_SPEED_MEASUREMENT`, `EXPORT_REQUIRE_LATEST_CHECK_SUCCESS`,
  `EXPORT_MAX_LATEST_CHECK_AGE_MINUTES`, `EXPORT_REQUIRE_LAST_TWO_SUCCESSES`,
  `EXPORT_RECENT_CHECKS_WINDOW`, `EXPORT_MIN_RECENT_SUCCESS_RATIO`,
  `EXPORT_MIN_USER_TARGET_SUCCESS_RATIO`, `EXPORT_REQUIRE_CRITICAL_TARGETS_ALL_SUCCESS`,
  `EXPORT_MIN_CRITICAL_TARGET_SUCCESS_RATIO`, and `EXPORT_MIN_FRESHNESS_SCORE`.

## Speed measurement
The prober keeps connect/exit-IP success separate from throughput quality. A proxy can be `connect_ok=true` even when speed measurement is unavailable.

Speed testing is deterministic and bounded:
- `PROBER_SPEED_URLS` is a comma-separated bounded speed-target list for throughput checks;
- `SPEED_TEST_URLS` remains a legacy fallback if `PROBER_SPEED_URLS` is empty.
- `SPEED_TEST_URL` is still supported as a legacy fallback.
- `SPEED_TEST_ATTEMPTS` controls total bounded attempts; every configured endpoint is tried at least once until up to three successful samples are collected.
- successful samples are aggregated with median `first_byte_ms` and median `download_mbps`.
- `SPEED_TEST_CONNECT_TIMEOUT_SECONDS`, `SPEED_TEST_READ_TIMEOUT_SECONDS`, `SPEED_TEST_MAX_BYTES`, and `SPEED_TEST_CHUNK_SIZE` keep each candidate probe bounded.

Multi-host lightweight verification is configured separately from throughput:
- `PROBER_MULTIHOST_ENABLED=true` enables bounded baseline/critical checks per candidate;
- `PROBER_BASELINE_URLS` and `PROBER_CRITICAL_URLS` are comma-separated deterministic target lists;
- `PROBER_MULTIHOST_MAX_TARGETS_PER_GROUP` caps per-group checks;
- `PROBER_MAX_TARGET_FIRST_BYTE_MS` and `PROBER_MAX_TARGET_LATENCY_MS` enforce per-target thresholds;
- `PROBER_MIN_USER_TARGET_SUCCESS_RATIO`, `PROBER_REQUIRE_CRITICAL_TARGETS_ALL_SUCCESS`, and `PROBER_MIN_CRITICAL_TARGET_SUCCESS_RATIO` define policy and diagnostics persisted in `proxy_checks`.

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

## Scoring and export hardening
`geo_confidence`, `exit_country`, `source_country_tag`, and `geo_match` are diagnostic-only fields.
They remain visible in DB/API/debug artifacts, but country expectation no longer contributes to:
- `proxy_state.final_score`;
- active/degraded/dead/unknown status calculation;
- export selection bonus or penalty.

`final_score` is now based on throughput, latency, stability, freshness/status penalties, and missing-speed penalty only. The non-geo scoring weights keep the previous relative balance:
- throughput: `0.4444`;
- latency: `0.3333`;
- stability: `0.2223`.

Exporter selection is intentionally stricter than scorer ranking. It first orders candidates by `proxy_state.final_score`, then applies hard export thresholds:
- `EXPORT_MAX_PER_COUNTRY=2`: at most two selected configs per `current_country` group;
- `EXPORT_MAX_PER_HOST=1`: at most one selected config per host; empty host falls back to fingerprint, then candidate id;
- `EXPORT_REQUIRE_LATEST_CHECK_SUCCESS=true`: latest `proxy_checks` row must be `connect_ok=true`;
- `EXPORT_MAX_LATEST_CHECK_AGE_MINUTES=75`: latest check must be fresh;
- `EXPORT_REQUIRE_LAST_TWO_SUCCESSES=true`: two latest checks must both be successful;
- `EXPORT_RECENT_CHECKS_WINDOW=5` + `EXPORT_MIN_RECENT_SUCCESS_RATIO=0.80`: recent stability hard gate;
- `EXPORT_MAX_LATENCY_MS=3000`: both aggregated state latency and latest check latency must be <= threshold;
- `EXPORT_MAX_FIRST_BYTE_MS=2200`: latest check first-byte must be <= threshold;
- `EXPORT_REQUIRE_SPEED_MEASUREMENT=true`: candidates with `state_download_mbps=null` are rejected by default;
- `EXPORT_MIN_DOWNLOAD_MBPS=2.0`: both aggregated state and latest-check speed must meet threshold;
- `EXPORT_MIN_USER_TARGET_SUCCESS_RATIO=0.80`: latest multi-host user target ratio hard gate;
- `EXPORT_REQUIRE_CRITICAL_TARGETS_ALL_SUCCESS=true` or `EXPORT_MIN_CRITICAL_TARGET_SUCCESS_RATIO=0.95`: critical target pass policy;
- `EXPORT_MIN_FRESHNESS_SCORE=0.75`: stale active candidates are rejected before curated output.

If `EXPORT_REQUIRE_SPEED_MEASUREMENT=false`, candidates with missing `state_download_mbps` may pass the speed-availability gate, but measured candidates still have to satisfy `EXPORT_MIN_DOWNLOAD_MBPS`. This switch exists for temporary low-coverage incidents; the default curated policy requires a real speed signal.

The selected TXT files remain client-facing output. Exporter now relabels only display names at export-time while preserving connection-critical link parts. The debug JSON files are the operator view and include:
- `policy`: thresholds used by the exporter;
- `summary.disabled_candidate_skipped`;
- `summary.low_final_score_skipped`;
- `summary.latest_check_failed_skipped`;
- `summary.stale_skipped`;
- `summary.missing_speed_skipped`;
- `summary.low_speed_skipped`;
- `summary.high_latency_skipped`;
- `summary.high_first_byte_skipped`;
- `summary.freshness_threshold_skipped`;
- `summary.unstable_recent_checks_skipped`;
- `summary.low_user_target_success_ratio_skipped`;
- `summary.critical_targets_failed_skipped`;
- `summary.country_limit_skipped`;
- `summary.host_limit_skipped`;
- `summary.legacy_no_speed_semantics_skipped`;
- `items[].selection_decision` for selected rows;
- `rejected_items[].selection_decision` for rejected rows.

### Standardized display label format
Export-time raw-link display label is standardized to:
`#{N} {FLAG}{CC} {GRP} {SPD} {LAT} #{NF}`

Where:
- `N` = `rank_global` (`#-` fallback);
- `FLAG` + `CC` = flag emoji + ISO alpha-2 code from factual `current_country` (`🏳️ZZ` fallback);
- `GRP` = family short code: `BLK` (`black`), `CIDR` (`white_cidr`), `SNI` (`white_sni`);
- `SPD` = aggregated `state_download_mbps` compact token like `20.9M` (`NS` fallback);
- `LAT` = aggregated `state_latency_ms` token like `622ms` (`NAms` fallback);
- `NF` = `rank_in_family` (`#-` fallback).

Examples:
- `#13 🇱🇹LT CIDR 20.9M 622ms #13`
- `#55 🇳🇱NL SNI 20.9M 622ms #1`
- `#8 🇫🇮FI BLK 32.1M 287ms #2`

Relabeling rules:
- URL-like schemes (`vless`, `trojan`, `ss`, `hysteria2`, `tuic`, and other URL-like supported schemes): replace URL fragment after `#` with the standardized label;
- `vmess` base64-json: decode payload, update `ps`, re-encode payload; if payload decode fails, fallback to fragment relabel only;
- no hardening/scoring/API/publisher behavior changes.

Quick hardening checks:
```bash
# runtime line counts
docker compose exec -T pipeline-runner sh -lc 'wc -l /app/output/*ETALON.txt'

# country distribution in ALL export
docker compose exec -T pipeline-runner python - <<'PY'
import json
from collections import Counter
from pathlib import Path
data = json.loads(Path('/app/output/ALL-ETALON-debug.json').read_text(encoding='utf-8'))
print(Counter(item['selection_country_group'] for item in data['items']).most_common())
print(data['summary'])
PY

# host diversity in ALL export
docker compose exec -T pipeline-runner python - <<'PY'
import json
from collections import Counter
from pathlib import Path
data = json.loads(Path('/app/output/ALL-ETALON-debug.json').read_text(encoding='utf-8'))
print(Counter(item['selection_host_group'] for item in data['items']).most_common())
PY

# latency/speed profile for selected rows
docker compose exec -T pipeline-runner python - <<'PY'
import json
from pathlib import Path
data = json.loads(Path('/app/output/ALL-ETALON-debug.json').read_text(encoding='utf-8'))
items = data['items']
lat = sorted(item['latency_ms'] for item in items if item['latency_ms'] is not None)
spd = sorted(item['download_mbps'] for item in items if item['download_mbps'] is not None)
missing_speed = sum(1 for item in items if item['download_mbps'] is None)
print({'items': len(items), 'missing_speed': missing_speed, 'max_latency': max(lat, default=None), 'min_speed': min(spd, default=None)})
PY

# prove geo is diagnostic-only for score/selection
docker compose exec -T pipeline-runner python - <<'PY'
from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal
from app.common.settings import get_settings
from app.scorer.models import CandidateAggregation
from app.scorer.scoring import score_candidate_state

settings = get_settings()
base = CandidateAggregation(
    candidate_id='00000000-0000-0000-0000-000000000001',
    family='white_sni',
    checks_total=3,
    checks_successful=3,
    last_check_at=datetime.now(timezone.utc),
    last_success_at=datetime.now(timezone.utc),
    current_country='DE',
    latency_ms=800,
    download_mbps=Decimal('12.0'),
    stability_ratio=Decimal('1.0000'),
    geo_confidence=Decimal('0.0000'),
)
high_geo = replace(base, geo_confidence=Decimal('1.0000'))
print(score_candidate_state(base, settings, scored_at=datetime.now(timezone.utc)).final_score)
print(score_candidate_state(high_geo, settings, scored_at=datetime.now(timezone.utc)).final_score)
PY
```

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

TXT remains client-facing distribution format and now contains relabeled raw links with standardized compact display names.
Debug JSON is for operator analysis and includes:
- `summary` counters (considered/selected/limits/hardening and diversity skip reasons);
- `policy` thresholds used by the current exporter run;
- `speed_quality` counters for latest checks;
- ordered `items` with `selection_position`, `display_label`, `source_raw_config`, `export_raw_config`, grouping keys, aggregated `state_*` metrics, and concrete `latest_check_*` diagnostics.
- explicit label tokens: `label_country`, `label_flag`, `label_group`, `label_download_mbps`, `label_latency_ms`, `label_rank_global`, `label_rank_in_family`, `label_strategy`.
- optional relabel diagnostics when relabel is skipped/partial: `label_error_code`, `label_error_text`.
- `rejected_items` with `selection_decision.stage`, `primary_reason`, and all rejection reasons for candidates that did not reach TXT output.

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

# validate standardized label format in exported fragments
python -c "import pathlib,re,urllib.parse as u; p=pathlib.Path('output/ALL-ETALON.txt'); lines=[x.strip() for x in p.read_text(encoding='utf-8').splitlines() if x.strip()]; rx=re.compile(r'^#(?:\\d+|-)\\s+.+\\s+(?:BLK|CIDR|SNI|[A-Z_]+)\\s+(?:NS|\\d+(?:\\.\\d+)?M)\\s+(?:NAms|\\d+ms)\\s+#(?:\\d+|-)$'); bad=[u.unquote(line.split('#',1)[1]) for line in lines if '#' in line and not rx.fullmatch(u.unquote(line.split('#',1)[1]))]; print({'lines':len(lines), 'bad_labels':len(bad), 'sample_bad':bad[:3]})"

# validate debug label fields and source/export diff visibility
python -c "import json,pathlib; d=json.loads(pathlib.Path('output/ALL-ETALON-debug.json').read_text(encoding='utf-8')); item=(d.get('items') or [{}])[0]; keys=('display_label','source_raw_config','export_raw_config','label_rank_global','label_rank_in_family'); print({k:(k in item) for k in keys}); print({k:item.get(k) for k in keys})"
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
