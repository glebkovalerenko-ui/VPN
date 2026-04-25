"""HTTP routes for Stage 11 API."""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.common.db import check_db_connection, get_engine
from app.common.enums import ProxyStatus, SourceFamily
from app.common.logging import get_logger
from app.common.settings import Settings

from .deps import get_app_settings, get_db_session, get_manifest_path, get_output_dir
from .schemas import (
    HealthResponse,
    StateCandidateItem,
    StateCandidatesResponse,
    StateTopResponse,
)

logger = get_logger(__name__)

router = APIRouter()

_STATE_SELECT_COLUMNS = """
    ps.candidate_id,
    ps.status,
    ps.last_check_at,
    ps.last_success_at,
    ps.current_country,
    ps.latency_ms,
    ps.download_mbps,
    ps.stability_ratio,
    ps.geo_confidence,
    ps.freshness_score,
    ps.final_score,
    ps.rank_global,
    ps.rank_in_family,
    ps.rank_in_country,
    ps.updated_at,
    c.family,
    c.protocol,
    c.host,
    c.port,
    c.sni,
    c.is_enabled
"""


@router.get("/health", response_model=HealthResponse)
def health(
    settings: Settings = Depends(get_app_settings),
    output_dir: Path = Depends(get_output_dir),
    manifest_path: Path = Depends(get_manifest_path),
) -> HealthResponse:
    """Basic service health with DB/output checks."""
    db_ok, db_details = check_db_connection(get_engine(settings))
    output_dir_exists = output_dir.is_dir()
    manifest_exists = manifest_path.is_file()
    overall_ok = db_ok and output_dir_exists and manifest_exists

    response = HealthResponse(
        status="ok" if overall_ok else "degraded",
        db="ok" if db_ok else "error",
        output_dir_exists=output_dir_exists,
        manifest_exists=manifest_exists,
        db_error=None if db_ok else db_details,
    )

    if not db_ok:
        logger.warning("Health check DB failure", extra={"db_error": db_details})
    if not output_dir_exists:
        logger.warning("Health check output directory missing", extra={"output_dir": str(output_dir)})
    if output_dir_exists and not manifest_exists:
        logger.warning(
            "Health check manifest missing",
            extra={"manifest_path": str(manifest_path)},
        )
    return response


@router.get("/state/top", response_model=StateTopResponse)
def state_top(
    limit: int = Query(default=50, ge=1, le=500),
    status: ProxyStatus | None = Query(default=ProxyStatus.ACTIVE),
    family: SourceFamily | None = Query(default=None),
    country: str | None = Query(default=None),
    only_positive_score: bool = Query(default=True),
    session: Session = Depends(get_db_session),
) -> StateTopResponse:
    """Return top ranked state rows from proxy_state joined with proxy_candidates."""
    normalized_country = _normalize_country(country)
    where_sql, query_params = _build_state_filters(
        status=status,
        family=family,
        protocol=None,
        country=normalized_country,
        enabled_only=False,
        min_final_score=None,
        only_positive_score=only_positive_score,
    )

    rows = session.execute(
        text(
            f"""
            SELECT
                {_STATE_SELECT_COLUMNS}
            FROM proxy_state AS ps
            JOIN proxy_candidates AS c
                ON c.id = ps.candidate_id
            WHERE {where_sql}
            ORDER BY
                ps.rank_global ASC NULLS LAST,
                ps.final_score DESC NULLS LAST,
                ps.stability_ratio DESC NULLS LAST,
                ps.last_success_at DESC NULLS LAST,
                ps.candidate_id ASC
            LIMIT :limit
            """
        ),
        {**query_params, "limit": limit},
    ).mappings().all()

    return StateTopResponse(
        limit=limit,
        status=status,
        family=family,
        country=normalized_country,
        only_positive_score=only_positive_score,
        items=[_map_state_row(row) for row in rows],
    )


@router.get("/state/candidates", response_model=StateCandidatesResponse)
def state_candidates(
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    status: ProxyStatus | None = Query(default=None),
    family: SourceFamily | None = Query(default=None),
    protocol: str | None = Query(default=None),
    country: str | None = Query(default=None),
    enabled_only: bool = Query(default=False),
    min_final_score: Decimal | None = Query(default=None),
    only_positive_score: bool = Query(default=False),
    session: Session = Depends(get_db_session),
) -> StateCandidatesResponse:
    """Return filtered state list with pagination."""
    normalized_protocol = _normalize_protocol(protocol)
    normalized_country = _normalize_country(country)
    where_sql, query_params = _build_state_filters(
        status=status,
        family=family,
        protocol=normalized_protocol,
        country=normalized_country,
        enabled_only=enabled_only,
        min_final_score=min_final_score,
        only_positive_score=only_positive_score,
    )

    total = int(
        session.execute(
            text(
                f"""
                SELECT COUNT(*)::int
                FROM proxy_state AS ps
                JOIN proxy_candidates AS c
                    ON c.id = ps.candidate_id
                WHERE {where_sql}
                """
            ),
            query_params,
        ).scalar_one()
    )

    rows = session.execute(
        text(
            f"""
            SELECT
                {_STATE_SELECT_COLUMNS}
            FROM proxy_state AS ps
            JOIN proxy_candidates AS c
                ON c.id = ps.candidate_id
            WHERE {where_sql}
            ORDER BY
                ps.final_score DESC NULLS LAST,
                ps.stability_ratio DESC NULLS LAST,
                ps.last_success_at DESC NULLS LAST,
                ps.updated_at DESC,
                ps.candidate_id ASC
            LIMIT :limit
            OFFSET :offset
            """
        ),
        {**query_params, "limit": limit, "offset": offset},
    ).mappings().all()

    return StateCandidatesResponse(
        limit=limit,
        offset=offset,
        total=total,
        status=status,
        family=family,
        protocol=normalized_protocol,
        country=normalized_country,
        enabled_only=enabled_only,
        min_final_score=min_final_score,
        only_positive_score=only_positive_score,
        items=[_map_state_row(row) for row in rows],
    )


@router.get("/exports/manifest")
def exports_manifest(
    manifest_path: Path = Depends(get_manifest_path),
) -> dict[str, Any]:
    """Return export manifest JSON as-is."""
    if not manifest_path.is_file():
        raise HTTPException(
            status_code=404,
            detail=f"Manifest file not found: {manifest_path.name}",
        )

    try:
        with manifest_path.open("r", encoding="utf-8") as file_obj:
            payload = json.load(file_obj)
    except json.JSONDecodeError as exc:
        logger.error(
            "Failed to parse export manifest",
            extra={"manifest_path": str(manifest_path), "error": str(exc)},
        )
        raise HTTPException(status_code=500, detail="Manifest JSON is invalid") from exc

    if not isinstance(payload, dict):
        raise HTTPException(status_code=500, detail="Manifest JSON root must be an object")
    return payload


@router.get("/exports/files/{file_name}")
def exports_file_download(
    file_name: str,
    output_dir: Path = Depends(get_output_dir),
) -> FileResponse:
    """Download one exported TXT file from output/ directory."""
    if Path(file_name).name != file_name:
        raise HTTPException(status_code=400, detail="Invalid file name")
    if not file_name.lower().endswith(".txt"):
        raise HTTPException(status_code=400, detail="Only .txt export files are allowed")

    output_dir_resolved = output_dir.resolve()
    file_path = (output_dir_resolved / file_name).resolve()
    if output_dir_resolved not in file_path.parents:
        raise HTTPException(status_code=400, detail="Invalid file path")
    if not file_path.is_file():
        raise HTTPException(status_code=404, detail=f"Export file not found: {file_name}")

    return FileResponse(
        path=file_path,
        media_type="text/plain",
        filename=file_name,
    )


def _build_state_filters(
    *,
    status: ProxyStatus | None,
    family: SourceFamily | None,
    protocol: str | None,
    country: str | None,
    enabled_only: bool,
    min_final_score: Decimal | None,
    only_positive_score: bool,
) -> tuple[str, dict[str, object]]:
    clauses: list[str] = []
    query_params: dict[str, object] = {}

    if status is not None:
        clauses.append("ps.status = :status")
        query_params["status"] = status.value

    if family is not None:
        clauses.append("c.family = :family")
        query_params["family"] = family.value

    if protocol is not None:
        clauses.append("c.protocol = :protocol")
        query_params["protocol"] = protocol

    if country is not None:
        clauses.append("ps.current_country = :country")
        query_params["country"] = country

    if enabled_only:
        clauses.append("c.is_enabled = TRUE")

    if min_final_score is not None:
        clauses.append("ps.final_score >= :min_final_score")
        query_params["min_final_score"] = min_final_score

    if only_positive_score:
        clauses.append("ps.final_score IS NOT NULL AND ps.final_score > 0")

    if not clauses:
        return "TRUE", query_params
    return " AND ".join(clauses), query_params


def _map_state_row(row: dict[str, Any]) -> StateCandidateItem:
    return StateCandidateItem(
        candidate_id=str(row["candidate_id"]),
        status=row["status"],
        family=row["family"],
        protocol=row["protocol"],
        host=row["host"],
        port=row["port"],
        sni=row["sni"],
        is_enabled=bool(row["is_enabled"]),
        current_country=row["current_country"],
        latency_ms=row["latency_ms"],
        download_mbps=row["download_mbps"],
        stability_ratio=row["stability_ratio"],
        geo_confidence=row["geo_confidence"],
        freshness_score=row["freshness_score"],
        final_score=row["final_score"],
        rank_global=row["rank_global"],
        rank_in_family=row["rank_in_family"],
        rank_in_country=row["rank_in_country"],
        last_check_at=row["last_check_at"],
        last_success_at=row["last_success_at"],
        updated_at=row["updated_at"],
    )


def _normalize_country(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip().upper()
    return normalized or None


def _normalize_protocol(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip().lower()
    return normalized or None
