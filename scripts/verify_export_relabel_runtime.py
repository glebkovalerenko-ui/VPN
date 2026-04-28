"""Controlled runtime verification for standardized exporter relabeling."""

from __future__ import annotations

import argparse
import base64
import binascii
import json
import sys
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

PROJECT_ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIR = PROJECT_ROOT / "output"
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.exporter.models import ExportCandidate
from app.exporter.relabel import build_relabeled_raw_link

BLACK_TXT = "BLACK-ETALON.txt"
WHITE_CIDR_TXT = "WHITE-CIDR-ETALON.txt"
WHITE_SNI_TXT = "WHITE-SNI-ETALON.txt"
ALL_TXT = "ALL-ETALON.txt"

DEBUG_BY_TXT = {
    BLACK_TXT: "BLACK-ETALON-debug.json",
    WHITE_CIDR_TXT: "WHITE-CIDR-ETALON-debug.json",
    WHITE_SNI_TXT: "WHITE-SNI-ETALON-debug.json",
    ALL_TXT: "ALL-ETALON-debug.json",
}

REQUIRED_DEBUG_FIELDS = (
    "display_label",
    "source_raw_config",
    "export_raw_config",
    "label_rank_global",
    "label_rank_in_family",
    "label_strategy",
)

VMESS_CRITICAL_FIELDS = (
    "add",
    "port",
    "id",
    "aid",
    "net",
    "type",
    "host",
    "path",
    "tls",
    "sni",
    "alpn",
)


@dataclass(slots=True, frozen=True)
class RuntimePair:
    txt_name: str
    txt_path: Path
    debug_path: Path
    txt_lines: list[str]
    debug_items: list[dict[str, Any]]
    debug_payload: dict[str, Any]


def _read_txt_lines(path: Path) -> list[str]:
    if not path.is_file():
        return []
    return [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _load_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _extract_vmess_token(raw_config: str) -> str | None:
    if not raw_config.lower().startswith("vmess://"):
        return None
    tail = raw_config[len("vmess://") :]
    for index, ch in enumerate(tail):
        if ch in ("?", "#"):
            return tail[:index]
    return tail


def _decode_vmess_payload_from_raw(raw_config: str) -> dict[str, Any] | None:
    token = _extract_vmess_token(raw_config)
    if not token:
        return None

    normalized = token.strip().replace("-", "+").replace("_", "/")
    normalized += "=" * (-len(normalized) % 4)
    try:
        decoded = base64.b64decode(normalized, validate=True)
    except (binascii.Error, ValueError):
        return None

    try:
        payload = json.loads(decoded.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None

    if not isinstance(payload, dict):
        return None
    return payload


def _extract_display_label_from_raw(raw_config: str | None) -> str | None:
    if not raw_config:
        return None

    try:
        split = urlsplit(raw_config)
    except ValueError:
        return None

    if split.scheme.lower() == "vmess":
        payload = _decode_vmess_payload_from_raw(raw_config)
        ps = payload.get("ps") if payload else None
        if isinstance(ps, str) and ps.strip():
            return ps

    if split.fragment:
        return unquote(split.fragment)
    return None


def _candidate_from_raw(raw_config: str) -> ExportCandidate:
    return ExportCandidate(
        candidate_id="synthetic-vmess",
        status="active",
        family="black",
        raw_config=raw_config,
        host="synthetic.example",
        fingerprint="fp-synthetic",
        source_country_tag="US",
        is_enabled=True,
        current_country="US",
        final_score=Decimal("1"),
        stability_ratio=Decimal("1"),
        latency_ms=120,
        download_mbps=Decimal("12.3"),
        latest_check_checked_at=datetime.now(timezone.utc),
        latest_check_connect_ok=True,
        latest_check_connect_ms=120,
        latest_check_first_byte_ms=80,
        latest_check_download_mbps=Decimal("12.3"),
        latest_check_exit_country="US",
        latest_check_geo_match=True,
        latest_user_targets_total=4,
        latest_user_targets_successful=4,
        latest_user_targets_success_ratio=Decimal("1.0000"),
        latest_critical_targets_total=2,
        latest_critical_targets_successful=2,
        latest_critical_targets_all_success=True,
        latest_multihost_failure_reason=None,
        latest_multihost_summary={"passed_policy": True},
        speed_error_code=None,
        speed_failure_reason=None,
        speed_error_text=None,
        speed_endpoint_url="https://speed.cloudflare.com/__down?bytes=1048576",
        speed_attempts=1,
        speed_successes=1,
        recent_checks_total=5,
        recent_checks_successful=5,
        recent_checks_success_ratio=Decimal("1.0000"),
        latest_two_checks_successful=True,
        latest_consecutive_successes=5,
        geo_confidence=Decimal("1"),
        freshness_score=Decimal("1"),
        last_success_at=datetime.now(timezone.utc),
        rank_global=5,
        rank_in_family=5,
        rank_in_country=3,
    )


def _build_pairs() -> list[RuntimePair]:
    pairs: list[RuntimePair] = []
    for txt_name, debug_name in DEBUG_BY_TXT.items():
        txt_path = OUTPUT_DIR / txt_name
        debug_path = OUTPUT_DIR / debug_name
        txt_lines = _read_txt_lines(txt_path)
        debug_payload = _load_json(debug_path)
        debug_items = debug_payload.get("items") or []
        pairs.append(
            RuntimePair(
                txt_name=txt_name,
                txt_path=txt_path,
                debug_path=debug_path,
                txt_lines=txt_lines,
                debug_items=debug_items,
                debug_payload=debug_payload,
            )
        )
    return pairs


def _vmess_proof_from_runtime(pairs: list[RuntimePair]) -> dict[str, Any] | None:
    for pair in pairs:
        for item in pair.debug_items:
            export_raw = item.get("export_raw_config")
            if not isinstance(export_raw, str):
                continue
            if urlsplit(export_raw).scheme.lower() != "vmess":
                continue

            source_raw = item.get("source_raw_config")
            if not isinstance(source_raw, str):
                continue

            old_payload = _decode_vmess_payload_from_raw(source_raw)
            new_payload = _decode_vmess_payload_from_raw(export_raw)
            if not old_payload or not new_payload:
                continue

            unchanged_checks = {
                field: old_payload.get(field) == new_payload.get(field)
                for field in VMESS_CRITICAL_FIELDS
                if field in old_payload or field in new_payload
            }
            non_ps_changed_fields = sorted(
                key
                for key in set(old_payload) | set(new_payload)
                if key != "ps" and old_payload.get(key) != new_payload.get(key)
            )

            return {
                "proof_type": "real_runtime",
                "from_file": pair.txt_name,
                "candidate_id": item.get("candidate_id"),
                "source_raw_config": source_raw,
                "export_raw_config": export_raw,
                "decoded_old_payload_ps": old_payload.get("ps"),
                "decoded_new_payload_ps": new_payload.get("ps"),
                "critical_fields_unchanged": unchanged_checks,
                "non_ps_changed_fields": non_ps_changed_fields,
            }
    return None


def _vmess_synthetic_proof() -> dict[str, Any]:
    payload = {
        "v": "2",
        "ps": "legacy-synthetic-label",
        "add": "synthetic.example",
        "port": "443",
        "id": "11111111-2222-3333-4444-555555555555",
        "aid": "0",
        "net": "ws",
        "type": "none",
        "host": "synthetic.example",
        "path": "/ws",
        "tls": "tls",
        "sni": "synthetic.example",
    }
    encoded = base64.b64encode(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ).decode("ascii")
    source_raw = f"vmess://{encoded}#legacy-fragment"
    relabeled = build_relabeled_raw_link(_candidate_from_raw(source_raw))
    export_raw = relabeled.export_raw_config or ""
    old_payload = _decode_vmess_payload_from_raw(source_raw) or {}
    new_payload = _decode_vmess_payload_from_raw(export_raw) or {}

    unchanged_checks = {
        field: old_payload.get(field) == new_payload.get(field)
        for field in VMESS_CRITICAL_FIELDS
        if field in old_payload or field in new_payload
    }
    non_ps_changed_fields = sorted(
        key
        for key in set(old_payload) | set(new_payload)
        if key != "ps" and old_payload.get(key) != new_payload.get(key)
    )

    return {
        "proof_type": "synthetic_local",
        "note": "No runtime vmess candidate in selected export items; used local synthetic proof.",
        "source_raw_config": source_raw,
        "export_raw_config": export_raw,
        "decoded_old_payload_ps": old_payload.get("ps"),
        "decoded_new_payload_ps": new_payload.get("ps"),
        "critical_fields_unchanged": unchanged_checks,
        "non_ps_changed_fields": non_ps_changed_fields,
    }


def build_runtime_report() -> tuple[dict[str, Any], list[str]]:
    violations: list[str] = []
    pairs = _build_pairs()

    samples = {
        pair.txt_name: pair.txt_lines[:3]
        for pair in pairs
    }

    debug_field_presence_missing: dict[str, int] = {field: 0 for field in REQUIRED_DEBUG_FIELDS}
    raw_equals_export_total = 0
    raw_items_total = 0
    source_diff_when_label_changed_expected = 0
    source_diff_when_label_changed_observed = 0
    order_checks: dict[str, dict[str, Any]] = {}

    for pair in pairs:
        if len(pair.txt_lines) != len(pair.debug_items):
            violations.append(
                f"{pair.txt_name}: TXT lines count ({len(pair.txt_lines)}) != debug items count ({len(pair.debug_items)})."
            )

        line_match_count = 0
        mismatch_positions: list[int] = []
        for index, line in enumerate(pair.txt_lines):
            if index >= len(pair.debug_items):
                mismatch_positions.append(index + 1)
                continue
            item_raw = pair.debug_items[index].get("raw_config")
            if item_raw == line:
                line_match_count += 1
            else:
                mismatch_positions.append(index + 1)

        order_checks[pair.txt_name] = {
            "txt_count": len(pair.txt_lines),
            "debug_items_count": len(pair.debug_items),
            "line_match_count": line_match_count,
            "all_lines_match_1_to_1": len(mismatch_positions) == 0 and len(pair.txt_lines) == len(pair.debug_items),
            "first_mismatch_positions": mismatch_positions[:5],
        }
        if mismatch_positions:
            violations.append(f"{pair.txt_name}: TXT/debug order mismatch at positions {mismatch_positions[:5]}.")

        for item in pair.debug_items:
            raw_items_total += 1
            if item.get("raw_config") == item.get("export_raw_config"):
                raw_equals_export_total += 1
            else:
                violations.append(f"{pair.txt_name}: raw_config != export_raw_config for candidate {item.get('candidate_id')}.")

            for field in REQUIRED_DEBUG_FIELDS:
                if field not in item:
                    debug_field_presence_missing[field] += 1

            display_label = item.get("display_label")
            source_raw = item.get("source_raw_config")
            export_raw = item.get("export_raw_config")
            old_label = _extract_display_label_from_raw(source_raw if isinstance(source_raw, str) else None)
            new_label = _extract_display_label_from_raw(export_raw if isinstance(export_raw, str) else None)

            if isinstance(display_label, str) and new_label and new_label != display_label:
                violations.append(
                    f"{pair.txt_name}: export label mismatch for candidate {item.get('candidate_id')} (new='{new_label}', display='{display_label}')."
                )

            if isinstance(display_label, str) and old_label and old_label != display_label:
                source_diff_when_label_changed_expected += 1
                if source_raw != export_raw:
                    source_diff_when_label_changed_observed += 1

    for field, missing_count in debug_field_presence_missing.items():
        if missing_count:
            violations.append(f"Missing debug field '{field}' in {missing_count} selected items.")

    if source_diff_when_label_changed_expected != source_diff_when_label_changed_observed:
        violations.append(
            "source_raw_config != export_raw_config invariant failed for relabeled items: "
            f"expected {source_diff_when_label_changed_expected}, observed {source_diff_when_label_changed_observed}."
        )

    white_sni_pair = next(pair for pair in pairs if pair.txt_name == WHITE_SNI_TXT)
    white_sni_is_empty = len(white_sni_pair.txt_lines) == 0
    rejected_reasons = Counter(
        (item.get("selection_decision") or {}).get("primary_reason")
        for item in (white_sni_pair.debug_payload.get("rejected_items") or [])
        if (item.get("selection_decision") or {}).get("primary_reason")
    )
    white_sni_evidence = {
        "txt_is_empty": white_sni_is_empty,
        "txt_lines_count": len(white_sni_pair.txt_lines),
        "debug_summary": white_sni_pair.debug_payload.get("summary"),
        "top_rejected_primary_reasons": rejected_reasons.most_common(5),
    }

    vmess_proof = _vmess_proof_from_runtime(pairs)
    if vmess_proof is None:
        vmess_proof = _vmess_synthetic_proof()

    if vmess_proof.get("decoded_new_payload_ps") == vmess_proof.get("decoded_old_payload_ps"):
        violations.append("vmess proof shows unchanged 'ps'; relabel did not happen.")
    if vmess_proof.get("non_ps_changed_fields"):
        violations.append(
            f"vmess proof shows non-ps field changes: {vmess_proof.get('non_ps_changed_fields')}."
        )

    report = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "runtime_files_checked": [
            {
                "txt_name": pair.txt_name,
                "txt_path": str(pair.txt_path),
                "debug_path": str(pair.debug_path),
                "txt_lines_count": len(pair.txt_lines),
                "debug_items_count": len(pair.debug_items),
            }
            for pair in pairs
        ],
        "samples": samples,
        "debug_verification": {
            "required_fields": list(REQUIRED_DEBUG_FIELDS),
            "missing_field_counts": debug_field_presence_missing,
            "raw_equals_export": {
                "true_count": raw_equals_export_total,
                "total_selected_items": raw_items_total,
                "all_true": raw_equals_export_total == raw_items_total,
            },
            "source_diff_when_label_changed": {
                "expected": source_diff_when_label_changed_expected,
                "observed": source_diff_when_label_changed_observed,
                "all_match": source_diff_when_label_changed_expected == source_diff_when_label_changed_observed,
            },
        },
        "txt_debug_order_checks": order_checks,
        "white_sni_evidence": white_sni_evidence,
        "vmess_proof": vmess_proof,
        "violations": violations,
        "status": "passed" if not violations else "failed",
    }
    return report, violations


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--report-path",
        default=str(OUTPUT_DIR / "export-relabel-runtime-verification.json"),
        help="Where to write JSON report.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    report, violations = build_runtime_report()
    report_path = Path(args.report_path).resolve()
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(report, indent=2, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )

    print(f"Runtime verification report: {report_path}")
    print(f"Status: {report['status']}")
    if violations:
        print("Violations:")
        for violation in violations:
            print(f"- {violation}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
