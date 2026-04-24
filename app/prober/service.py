"""Stage 5 prober orchestration: select, probe, persist checks."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.common.db import session_scope
from app.common.logging import get_logger
from app.common.settings import Settings, get_settings

from .checker import ProbeResult, SingBoxProbeBackend
from .errors import ProbeErrorCode
from .selectors import select_candidates_for_probe

logger = get_logger(__name__)


@dataclass(slots=True)
class ProbeCycleStats:
    """Execution metrics for one probe run."""

    selected: int = 0
    succeeded: int = 0
    failed: int = 0
    unsupported: int = 0

    def to_log_extra(self) -> dict[str, int]:
        return {
            "selected": self.selected,
            "succeeded": self.succeeded,
            "failed": self.failed,
            "unsupported": self.unsupported,
        }


def run_probe_cycle(app_settings: Settings | None = None) -> ProbeCycleStats:
    """Run one Stage 5 probe cycle with deterministic selection."""
    settings = app_settings or get_settings()
    stats = ProbeCycleStats()

    with session_scope(settings) as session:
        candidates = select_candidates_for_probe(
            session,
            batch_size=settings.PROBE_BATCH_SIZE,
        )

    stats.selected = len(candidates)
    logger.info(
        "Prober batch started",
        extra={
            "batch_size_limit": settings.PROBE_BATCH_SIZE,
            "selected": stats.selected,
            "connect_timeout_seconds": settings.CONNECT_TIMEOUT_SECONDS,
            "read_timeout_seconds": settings.DOWNLOAD_TIMEOUT_SECONDS,
            "singbox_binary": settings.SINGBOX_BINARY,
            "process_start_timeout_seconds": settings.PROBER_PROCESS_START_TIMEOUT_SECONDS,
        },
    )

    backend = SingBoxProbeBackend(
        singbox_binary=settings.SINGBOX_BINARY,
        bind_host=settings.PROBER_LOCAL_BIND_HOST,
        base_local_port=settings.PROBER_BASE_LOCAL_PORT,
        process_start_timeout_seconds=settings.PROBER_PROCESS_START_TIMEOUT_SECONDS,
        temp_dir=settings.PROBER_TEMP_DIR,
        connect_timeout_seconds=settings.CONNECT_TIMEOUT_SECONDS,
        read_timeout_seconds=settings.DOWNLOAD_TIMEOUT_SECONDS,
        exit_ip_url=settings.PROBER_EXIT_IP_URL,
    )

    for candidate in candidates:
        result = backend.probe_candidate(candidate)

        try:
            with session_scope(settings) as session:
                insert_proxy_check(session, candidate_id=candidate.id, result=result)
        except Exception:
            logger.exception(
                "Failed to persist probe result",
                extra={
                    "candidate_id": candidate.id,
                    "protocol": candidate.protocol,
                    "connect_ok": result.connect_ok,
                    "error_code": result.error_code,
                },
            )
            stats.failed += 1
            continue

        _apply_result_to_stats(stats, result)

        logger.info(
            "Candidate probed",
            extra={
                "candidate_id": candidate.id,
                "protocol": candidate.protocol,
                "connect_ok": result.connect_ok,
                "connect_ms": result.connect_ms,
                "exit_ip": result.exit_ip,
                "error_code": result.error_code,
            },
        )

    logger.info("Prober batch finished", extra=stats.to_log_extra())
    return stats


def insert_proxy_check(session: Session, *, candidate_id: str, result: ProbeResult) -> None:
    """Persist single probe attempt into proxy_checks."""
    session.execute(
        text(
            """
            INSERT INTO proxy_checks (
                candidate_id,
                checked_at,
                connect_ok,
                connect_ms,
                exit_ip,
                error_code,
                error_text
            )
            VALUES (
                :candidate_id,
                :checked_at,
                :connect_ok,
                :connect_ms,
                :exit_ip,
                :error_code,
                :error_text
            )
            """
        ),
        {
            "candidate_id": candidate_id,
            "checked_at": result.checked_at,
            "connect_ok": result.connect_ok,
            "connect_ms": result.connect_ms,
            "exit_ip": result.exit_ip,
            "error_code": result.error_code,
            "error_text": result.error_text,
        },
    )


def _apply_result_to_stats(stats: ProbeCycleStats, result: ProbeResult) -> None:
    if result.connect_ok:
        stats.succeeded += 1
        return

    stats.failed += 1
    if result.error_code == ProbeErrorCode.UNSUPPORTED_PROTOCOL.value:
        stats.unsupported += 1
