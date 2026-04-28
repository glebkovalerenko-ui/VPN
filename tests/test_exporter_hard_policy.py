"""Unit tests for exporter hard eligibility policy."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
import unittest

from app.exporter.models import ExportCandidate
from app.exporter.service import ExportPolicy, _policy_rejection_reasons


def _build_policy(**overrides: object) -> ExportPolicy:
    base: dict[str, object] = {
        "max_per_country": 2,
        "max_per_host": 1,
        "max_latency_ms": 2500,
        "max_first_byte_ms": 1500,
        "min_download_mbps": Decimal("5.0"),
        "require_speed_measurement": True,
        "allow_legacy_speed_if_other_signals_strong": False,
        "require_latest_check_success": True,
        "max_latest_check_age_minutes": 60,
        "require_last_two_successes": False,
        "require_consecutive_successes": True,
        "min_consecutive_successes": 2,
        "recent_checks_window": 5,
        "min_recent_success_ratio": Decimal("0.8000"),
        "min_user_target_success_ratio": Decimal("0.8000"),
        "require_critical_targets_all_success": True,
        "min_critical_target_success_ratio": Decimal("0.9500"),
        "min_freshness_score": Decimal("0.7500"),
        "min_final_score_exclusive": Decimal("0.0000"),
    }
    base.update(overrides)
    return ExportPolicy(**base)


def _build_candidate(**overrides: object) -> ExportCandidate:
    now = datetime.now(timezone.utc)
    base: dict[str, object] = {
        "candidate_id": "candidate-1",
        "status": "active",
        "family": "black",
        "raw_config": "vless://11111111-2222-3333-4444-555555555555@example.com:443?encryption=none#x",
        "host": "example.com",
        "fingerprint": "fp-1",
        "source_country_tag": "RU",
        "is_enabled": True,
        "current_country": "RU",
        "final_score": Decimal("0.9000"),
        "stability_ratio": Decimal("0.9500"),
        "latency_ms": 420,
        "download_mbps": Decimal("25.000"),
        "latest_check_checked_at": now,
        "latest_check_connect_ok": True,
        "latest_check_connect_ms": 500,
        "latest_check_first_byte_ms": 120,
        "latest_check_download_mbps": Decimal("23.500"),
        "latest_check_exit_country": "RU",
        "latest_check_geo_match": True,
        "latest_user_targets_total": 4,
        "latest_user_targets_successful": 4,
        "latest_user_targets_success_ratio": Decimal("1.0000"),
        "latest_critical_targets_total": 2,
        "latest_critical_targets_successful": 2,
        "latest_critical_targets_all_success": True,
        "latest_multihost_failure_reason": None,
        "latest_multihost_summary": {"passed_policy": True},
        "speed_error_code": None,
        "speed_failure_reason": None,
        "speed_error_text": None,
        "speed_endpoint_url": "https://speed.cloudflare.com/__down?bytes=1048576",
        "speed_attempts": 2,
        "speed_successes": 2,
        "recent_checks_total": 5,
        "recent_checks_successful": 5,
        "recent_checks_success_ratio": Decimal("1.0000"),
        "latest_two_checks_successful": True,
        "latest_consecutive_successes": 5,
        "geo_confidence": Decimal("1.0000"),
        "freshness_score": Decimal("0.9000"),
        "last_success_at": now,
        "rank_global": 1,
        "rank_in_family": 1,
        "rank_in_country": 1,
    }
    base.update(overrides)
    return ExportCandidate(**base)


class ExporterHardPolicyTests(unittest.TestCase):
    def test_healthy_candidate_has_no_rejections(self) -> None:
        candidate = _build_candidate()
        policy = _build_policy()

        reasons = _policy_rejection_reasons(
            candidate,
            policy,
            evaluated_at=datetime.now(timezone.utc),
        )
        self.assertEqual(reasons, [])

    def test_low_user_target_success_ratio_is_rejected(self) -> None:
        candidate = _build_candidate(
            latest_user_targets_success_ratio=Decimal("0.5000"),
            latest_user_targets_successful=2,
        )
        policy = _build_policy()

        reasons = _policy_rejection_reasons(
            candidate,
            policy,
            evaluated_at=datetime.now(timezone.utc),
        )
        self.assertIn("low_user_target_success_ratio", reasons)

    def test_critical_policy_rejects_when_all_success_required(self) -> None:
        candidate = _build_candidate(
            latest_critical_targets_all_success=False,
            latest_critical_targets_successful=1,
            latest_multihost_failure_reason="critical_targets_failed",
        )
        policy = _build_policy(require_critical_targets_all_success=True)

        reasons = _policy_rejection_reasons(
            candidate,
            policy,
            evaluated_at=datetime.now(timezone.utc),
        )
        self.assertIn("critical_targets_failed", reasons)

    def test_stale_latest_check_and_recent_instability_are_rejected(self) -> None:
        stale_at = datetime.now(timezone.utc) - timedelta(minutes=121)
        candidate = _build_candidate(
            latest_check_checked_at=stale_at,
            recent_checks_success_ratio=Decimal("0.4000"),
            latest_two_checks_successful=False,
            latest_consecutive_successes=0,
            recent_checks_successful=2,
        )
        policy = _build_policy(max_latest_check_age_minutes=60, min_recent_success_ratio=Decimal("0.8000"))

        reasons = _policy_rejection_reasons(
            candidate,
            policy,
            evaluated_at=datetime.now(timezone.utc),
        )
        self.assertIn("stale", reasons)
        self.assertIn("unstable_recent_checks", reasons)

    def test_single_recent_success_can_pass_when_min_consecutive_is_one(self) -> None:
        candidate = _build_candidate(
            latest_two_checks_successful=False,
            latest_consecutive_successes=1,
            recent_checks_total=1,
            recent_checks_successful=1,
            recent_checks_success_ratio=Decimal("1.0000"),
        )
        policy = _build_policy(min_consecutive_successes=1)

        reasons = _policy_rejection_reasons(
            candidate,
            policy,
            evaluated_at=datetime.now(timezone.utc),
        )
        self.assertNotIn("unstable_recent_checks", reasons)

    def test_critical_policy_can_use_ratio_threshold(self) -> None:
        candidate = _build_candidate(
            latest_critical_targets_total=2,
            latest_critical_targets_successful=1,
            latest_critical_targets_all_success=False,
            latest_multihost_failure_reason="critical_targets_failed",
        )
        policy = _build_policy(
            require_critical_targets_all_success=False,
            min_critical_target_success_ratio=Decimal("0.5000"),
        )

        reasons = _policy_rejection_reasons(
            candidate,
            policy,
            evaluated_at=datetime.now(timezone.utc),
        )
        self.assertNotIn("critical_targets_failed", reasons)


if __name__ == "__main__":
    unittest.main()
