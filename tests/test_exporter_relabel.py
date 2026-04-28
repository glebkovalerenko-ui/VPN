"""Unit tests for standardized exporter relabeling."""

from __future__ import annotations

import base64
import json
import re
import unittest
from datetime import datetime, timezone
from decimal import Decimal
from urllib.parse import unquote, urlsplit

from app.exporter.models import ExportCandidate
from app.exporter.relabel import build_relabeled_raw_link

_LABEL_RE = re.compile(
    r"^#(?:\d+|-) [^\s]+ [A-Z0-9_]+ (?:\d+(?:\.\d)?M|NS) (?:\d+ms|NAms) #(?:\d+|-)$"
)


def _make_candidate(raw_config: str, **overrides: object) -> ExportCandidate:
    base: dict[str, object] = {
        "candidate_id": "candidate-1",
        "status": "active",
        "family": "black",
        "raw_config": raw_config,
        "host": "example.com",
        "fingerprint": "fp-1",
        "source_country_tag": "RU",
        "is_enabled": True,
        "current_country": "RU",
        "final_score": Decimal("0.9999"),
        "stability_ratio": Decimal("0.9500"),
        "latency_ms": 705,
        "download_mbps": Decimal("46.077"),
        "latest_check_checked_at": datetime(2026, 4, 26, tzinfo=timezone.utc),
        "latest_check_connect_ok": True,
        "latest_check_connect_ms": 120,
        "latest_check_first_byte_ms": 42,
        "latest_check_download_mbps": Decimal("46.077"),
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
        "speed_attempts": 1,
        "speed_successes": 1,
        "recent_checks_total": 5,
        "recent_checks_successful": 5,
        "recent_checks_success_ratio": Decimal("1.0000"),
        "latest_two_checks_successful": True,
        "latest_consecutive_successes": 5,
        "geo_confidence": Decimal("1.0"),
        "freshness_score": Decimal("0.95"),
        "last_success_at": datetime(2026, 4, 26, tzinfo=timezone.utc),
        "rank_global": 16,
        "rank_in_family": 1,
        "rank_in_country": 1,
    }
    base.update(overrides)
    return ExportCandidate(**base)


def _extract_vmess_token(raw_config: str) -> str:
    assert raw_config.startswith("vmess://")
    vmess_tail = raw_config[len("vmess://") :]
    for index, ch in enumerate(vmess_tail):
        if ch in ("?", "#"):
            return vmess_tail[:index]
    return vmess_tail


def _decode_vmess_payload_from_link(raw_config: str) -> dict[str, object]:
    token = _extract_vmess_token(raw_config)
    normalized = token.replace("-", "+").replace("_", "/")
    normalized += "=" * (-len(normalized) % 4)
    decoded = base64.b64decode(normalized)
    payload = json.loads(decoded.decode("utf-8"))
    assert isinstance(payload, dict)
    return payload


class RelabelTests(unittest.TestCase):
    def test_url_like_schemes_change_only_fragment(self) -> None:
        raw_by_scheme = {
            "vless": (
                "vless://11111111-2222-3333-4444-555555555555@example.com:443"
                "?encryption=none&security=tls&type=ws#Old Label"
            ),
            "trojan": (
                "trojan://secret@example.net:8443"
                "?security=tls&sni=example.net#Legacy Node"
            ),
            "ss": (
                "ss://YWVzLTI1Ni1nY206cGFzc3dvcmQ=@198.51.100.10:8388"
                "?plugin=v2ray-plugin%3Btls#Old-SS"
            ),
        }

        for scheme, raw_config in raw_by_scheme.items():
            with self.subTest(scheme=scheme):
                result = build_relabeled_raw_link(_make_candidate(raw_config))
                source_split = urlsplit(raw_config)
                export_split = urlsplit(result.export_raw_config or "")

                self.assertEqual(source_split.scheme, export_split.scheme)
                self.assertEqual(source_split.netloc, export_split.netloc)
                self.assertEqual(source_split.path, export_split.path)
                self.assertEqual(source_split.query, export_split.query)
                self.assertEqual(unquote(export_split.fragment), result.display_label)
                self.assertNotEqual(source_split.fragment, export_split.fragment)
                self.assertTrue(export_split.scheme)
                self.assertTrue(export_split.netloc or export_split.path)
                self.assertRegex(result.display_label, _LABEL_RE)

    def test_vmess_payload_updates_only_ps(self) -> None:
        old_payload = {
            "v": "2",
            "ps": "Legacy VMess Label",
            "add": "example.com",
            "port": "443",
            "id": "11111111-2222-3333-4444-555555555555",
            "aid": "0",
            "net": "ws",
            "type": "none",
            "host": "example.com",
            "path": "/ws",
            "tls": "tls",
            "sni": "example.com",
        }
        old_token = (
            base64.b64encode(
                json.dumps(old_payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            )
            .decode("ascii")
            .rstrip("=")
        )
        raw_config = f"vmess://{old_token}?remarks=keep#old-fragment"

        result = build_relabeled_raw_link(_make_candidate(raw_config))
        self.assertEqual(result.label_error_code, None)
        self.assertTrue(result.label_strategy.endswith(":vmess_payload_ps"))

        source_split = urlsplit(raw_config)
        export_split = urlsplit(result.export_raw_config or "")
        self.assertEqual(source_split.scheme, export_split.scheme)
        self.assertEqual(source_split.query, export_split.query)
        self.assertEqual(unquote(export_split.fragment), result.display_label)

        old_decoded = _decode_vmess_payload_from_link(raw_config)
        new_decoded = _decode_vmess_payload_from_link(result.export_raw_config or "")
        self.assertEqual(new_decoded["ps"], result.display_label)

        for critical_field in ("add", "port", "id", "net", "tls", "path", "host", "type", "sni"):
            self.assertEqual(new_decoded.get(critical_field), old_decoded.get(critical_field))

        old_without_ps = {key: value for key, value in old_decoded.items() if key != "ps"}
        new_without_ps = {key: value for key, value in new_decoded.items() if key != "ps"}
        self.assertDictEqual(new_without_ps, old_without_ps)
        self.assertNotEqual(result.source_raw_config, result.export_raw_config)
        self.assertNotIn("=", _extract_vmess_token(result.export_raw_config or ""))

    def test_vmess_decode_failure_uses_fragment_fallback(self) -> None:
        raw_config = "vmess://not-base64@@@?foo=bar#legacy-fragment"
        result = build_relabeled_raw_link(_make_candidate(raw_config))

        source_split = urlsplit(raw_config)
        export_split = urlsplit(result.export_raw_config or "")
        self.assertEqual(source_split.scheme, export_split.scheme)
        self.assertEqual(source_split.netloc, export_split.netloc)
        self.assertEqual(source_split.path, export_split.path)
        self.assertEqual(source_split.query, export_split.query)
        self.assertEqual(unquote(export_split.fragment), result.display_label)
        self.assertEqual(result.label_error_code, "vmess_payload_decode_failed")
        self.assertTrue(result.label_strategy.endswith(":vmess_fragment_fallback"))
        self.assertNotEqual(result.source_raw_config, result.export_raw_config)

    def test_fallback_tokens_for_unknown_values(self) -> None:
        result = build_relabeled_raw_link(
            _make_candidate(
                "vless://u@example.com:443?type=tcp#legacy",
                current_country="?",
                download_mbps=None,
                latency_ms=None,
                rank_global=None,
                rank_in_family=None,
            )
        )
        self.assertEqual(result.display_label, "#- 🏳️ZZ BLK NS NAms #-")

    def test_exact_standardized_label_format(self) -> None:
        result = build_relabeled_raw_link(
            _make_candidate("trojan://secret@example.net:443?security=tls#legacy")
        )
        self.assertEqual(result.display_label, "#16 🇷🇺RU BLK 46.1M 705ms #1")
        self.assertRegex(result.display_label, _LABEL_RE)


if __name__ == "__main__":
    unittest.main()
