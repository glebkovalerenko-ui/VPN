# Proxy Aggregation Project - Stage 9 (Exporter MVP)

## What Stage 9 adds
Stage 9 introduces the first real delivery layer: exporter outputs for manual testing in client apps.

Exporter behavior:
- reads ranking and quality from `proxy_state` (source of truth);
- reads `raw_config` and `family` from `proxy_candidates`;
- does not recalculate quality scores;
- applies deterministic sorting and diversity limits;
- writes idempotent TXT outputs plus JSON manifest.

## Stage 0-8 coverage
- Postgres + migrations
- fetcher -> `source_snapshots`
- parser -> `proxy_candidates`
- prober -> `proxy_checks`
- scorer -> `proxy_state`

## Out of scope for Stage 9
- HTTP API
- scheduler/queues
- subscription server

## Export outputs
Exporter writes files into `output/`:
- `BLACK-ETALON.txt` (`family = black`)
- `WHITE-CIDR-ETALON.txt` (`family = white_cidr`)
- `WHITE-SNI-ETALON.txt` (`family = white_sni`)
- `ALL-ETALON.txt` (global top across all families, same policy/diversity)
- `export_manifest.json` (run metadata and counts)

TXT format (MVP):
- one line = one `raw_config`;
- selection-order preserved;
- no blank lines;
- no duplicates per output file;
- trailing newline is written.

## Selection policy
Base filters:
- `status = active`
- `final_score IS NOT NULL`
- `final_score > 0`
- non-empty `raw_config`

Sort order:
1. `final_score DESC`
2. `stability_ratio DESC NULLS LAST`
3. `last_success_at DESC NULLS LAST`
4. `candidate_id ASC`

Diversity constraints (applied during selection):
- `MAX_PER_COUNTRY` by `current_country` (`NULL` grouped as `__unknown_country__`)
- `MAX_PER_HOST` by `host`
- if `host` is null/empty, exporter uses `fingerprint` (or `candidate_id`) as unique host group

Per-file limits from settings:
- `EXPORT_BLACK_LIMIT`
- `EXPORT_WHITE_CIDR_LIMIT`
- `EXPORT_WHITE_SNI_LIMIT`
- `EXPORT_ALL_LIMIT`

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

## Full pipeline through Stage 9
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

## Output checks
List output files:
```bash
ls -la output
```

Windows PowerShell:
```powershell
Get-ChildItem output
```

Preview first lines:
```bash
head -n 20 output/BLACK-ETALON.txt
head -n 20 output/WHITE-CIDR-ETALON.txt
head -n 20 output/WHITE-SNI-ETALON.txt
head -n 20 output/ALL-ETALON.txt
```

Windows PowerShell:
```powershell
Get-Content output/BLACK-ETALON.txt -TotalCount 20
Get-Content output/WHITE-CIDR-ETALON.txt -TotalCount 20
Get-Content output/WHITE-SNI-ETALON.txt -TotalCount 20
Get-Content output/ALL-ETALON.txt -TotalCount 20
```

Check line counts:
```bash
wc -l output/BLACK-ETALON.txt output/WHITE-CIDR-ETALON.txt output/WHITE-SNI-ETALON.txt output/ALL-ETALON.txt
```

Windows PowerShell:
```powershell
Get-ChildItem output/*.txt | ForEach-Object { "{0}`t{1}" -f $_.Name, (Get-Content $_.FullName).Count }
```

Inspect manifest:
```bash
cat output/export_manifest.json
```

Windows PowerShell:
```powershell
Get-Content output/export_manifest.json
```

## SQL verification
Count active rows in `proxy_state`:
```bash
docker compose exec -T db sh -lc 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "SELECT COUNT(*) AS active_rows FROM proxy_state WHERE status = '\''active'\'';"'
```

Top rows by family to compare with exports:
```bash
docker compose exec -T db sh -lc 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "SELECT pc.family, ps.candidate_id, ps.final_score, ps.stability_ratio, ps.last_success_at FROM proxy_state ps JOIN proxy_candidates pc ON pc.id = ps.candidate_id WHERE ps.status = '\''active'\'' AND ps.final_score IS NOT NULL AND ps.final_score > 0 ORDER BY pc.family ASC, ps.final_score DESC, ps.stability_ratio DESC NULLS LAST, ps.last_success_at DESC NULLS LAST, ps.candidate_id ASC LIMIT 60;"'
```
