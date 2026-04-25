"""Stage 11 orchestration loop for fetch/parse/probe/score/export/publish."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from time import monotonic, sleep
from typing import Any, Callable

from app.common.logging import get_logger
from app.common.settings import Settings, get_settings
from app.exporter.service import run_export_cycle
from app.fetcher.service import run_fetch_cycle
from app.parser.service import run_parse_cycle
from app.prober.service import run_probe_cycle
from app.publisher.git_publish import publish_output
from app.scorer.service import run_scorer_cycle

logger = get_logger(__name__)

_StageRunner = Callable[[Settings], Any]
_STAGE_ORDER: tuple[tuple[str, _StageRunner], ...] = (
    ("fetcher", run_fetch_cycle),
    ("parser", run_parse_cycle),
    ("prober", run_probe_cycle),
    ("scorer", run_scorer_cycle),
    ("exporter", run_export_cycle),
)


@dataclass(slots=True, frozen=True)
class PipelineCycleResult:
    """Outcome for one orchestrated end-to-end cycle."""

    started_at: datetime
    finished_at: datetime
    success: bool
    cycle_seconds: float
    failed_stage: str | None
    stage_results: dict[str, dict[str, Any]]
    publish: dict[str, Any]

    def to_log_extra(self) -> dict[str, Any]:
        return {
            "cycle_started_at": self.started_at.isoformat(),
            "cycle_finished_at": self.finished_at.isoformat(),
            "cycle_success": self.success,
            "cycle_seconds": round(self.cycle_seconds, 3),
            "failed_stage": self.failed_stage,
            "stage_results": self.stage_results,
            "publish": self.publish,
        }


def run_pipeline_cycle(app_settings: Settings | None = None) -> PipelineCycleResult:
    """Execute one full pipeline cycle in fail-fast stage order."""
    settings = app_settings or get_settings()
    started_at = datetime.now(timezone.utc)
    cycle_started = monotonic()
    stage_results: dict[str, dict[str, Any]] = {}

    for stage_name, stage_runner in _STAGE_ORDER:
        stage_started = monotonic()
        try:
            stats = stage_runner(settings)
        except Exception as exc:
            stage_results[stage_name] = {
                "success": False,
                "duration_seconds": round(monotonic() - stage_started, 3),
                "error": _short_error_text(exc),
            }
            finished_at = datetime.now(timezone.utc)
            return PipelineCycleResult(
                started_at=started_at,
                finished_at=finished_at,
                success=False,
                cycle_seconds=monotonic() - cycle_started,
                failed_stage=stage_name,
                stage_results=stage_results,
                publish={"skipped_reason": "stage_failure"},
            )

        stage_results[stage_name] = {
            "success": True,
            "duration_seconds": round(monotonic() - stage_started, 3),
            "stats": _serialize_stats(stats),
        }

    try:
        publish_result = publish_output(settings)
        publish_details = publish_result.to_log_extra()
    except Exception as exc:
        finished_at = datetime.now(timezone.utc)
        return PipelineCycleResult(
            started_at=started_at,
            finished_at=finished_at,
            success=False,
            cycle_seconds=monotonic() - cycle_started,
            failed_stage="publisher",
            stage_results=stage_results,
            publish={"error": _short_error_text(exc)},
        )

    finished_at = datetime.now(timezone.utc)
    return PipelineCycleResult(
        started_at=started_at,
        finished_at=finished_at,
        success=True,
        cycle_seconds=monotonic() - cycle_started,
        failed_stage=None,
        stage_results=stage_results,
        publish=publish_details,
    )


def run_orchestrator_loop(app_settings: Settings | None = None) -> int:
    """Run pipeline in continuous interval loop."""
    settings = app_settings or get_settings()
    interval_seconds = settings.FETCH_INTERVAL_MINUTES * 60
    startup_delay = settings.ORCHESTRATOR_STARTUP_DELAY_SECONDS

    logger.info(
        "Orchestrator loop started",
        extra={
            "interval_seconds": interval_seconds,
            "startup_delay_seconds": startup_delay,
            "exit_on_failure": settings.ORCHESTRATOR_EXIT_ON_FAILURE,
        },
    )

    if startup_delay > 0:
        sleep(startup_delay)

    cycle_number = 0
    while True:
        cycle_number += 1
        cycle_t0 = monotonic()
        result = run_pipeline_cycle(settings)
        if result.success:
            logger.info(
                "Orchestrator cycle completed",
                extra={"cycle_number": cycle_number, **result.to_log_extra()},
            )
        else:
            logger.error(
                "Orchestrator cycle failed",
                extra={"cycle_number": cycle_number, **result.to_log_extra()},
            )
            if settings.ORCHESTRATOR_EXIT_ON_FAILURE:
                logger.error(
                    "Orchestrator exiting due to failure",
                    extra={"failed_stage": result.failed_stage, "cycle_number": cycle_number},
                )
                return 1

        elapsed = monotonic() - cycle_t0
        sleep_seconds = max(0.0, interval_seconds - elapsed)
        logger.info(
            "Orchestrator sleeping before next cycle",
            extra={"cycle_number": cycle_number, "sleep_seconds": round(sleep_seconds, 3)},
        )
        sleep(sleep_seconds)


def _serialize_stats(stats: Any) -> dict[str, Any]:
    if hasattr(stats, "to_log_extra"):
        try:
            payload = stats.to_log_extra()
            if isinstance(payload, dict):
                return payload
        except Exception:
            return {"repr": repr(stats)}
    return {"repr": repr(stats)}


def _short_error_text(exc: Exception) -> str:
    message = str(exc).strip()
    if not message:
        message = exc.__class__.__name__
    return message[:500]

