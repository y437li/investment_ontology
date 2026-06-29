from __future__ import annotations

import calendar
import json
from datetime import datetime


def parse_iso_date(value: str) -> datetime:
    """Parse a date-like string into a UTC-naive datetime."""

    return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)


def add_months(value: datetime, months: int) -> datetime:
    """Add calendar months, clamping day to the last day of month."""

    year = value.year
    month = value.month + months
    year += (month - 1) // 12
    month = ((month - 1) % 12) + 1
    day = min(
        value.day,
        calendar.monthrange(year, month)[1],
    )
    return value.replace(year=year, month=month, day=day)


def validate_manifest_freeze_gate(
    manifest_text: str,
) -> tuple[bool, str | None]:
    """Validate freeze gate metadata shape from run manifest text.

    Returns:
        (ok, failure_reason)
    """

    manifest = json.loads(manifest_text)

    if not manifest.get("discovery_frozen", False):
        return True, None

    hash_map = manifest.get("discovery_artifact_hashes")
    if not isinstance(hash_map, dict) or not hash_map:
        return False, "discovery_frozen=true requires discovery_artifact_hashes"

    required_keys = {
        "discovery/raw_documents.parquet",
        "discovery/documents.parquet",
        "discovery/document_cleaning_log.parquet",
        "discovery/chunks.parquet",
        "discovery/entities.parquet",
        "discovery/entity_aliases.parquet",
        "discovery/edges.parquet",
        "discovery/graph.json",
    }

    missing_keys = sorted(required_keys - set(hash_map))
    if missing_keys:
        return False, f"missing discovery_frozen hashes: {missing_keys}"

    return True, None


def validate_chunk_leakage_gate(
    rows: list[tuple[str, str]],
    as_of_date: str,
) -> tuple[bool, list[str]]:
    """Validate evidence chunk dates against an as_of boundary.

    rows: list[(chunk_id, available_at)]
    """

    threshold = parse_iso_date(as_of_date)
    violations = []
    for chunk_id, available_at in rows:
        if parse_iso_date(available_at) > threshold:
            violations.append(f"{chunk_id} available_at {available_at} > {as_of_date}")
    return not violations, violations


def validate_forward_window_gate(
    market_rows: list[tuple[str, str]],
    as_of_date: str,
    holding_window_months: int,
) -> tuple[bool, str | None]:
    """Validate that at least one forward row exists beyond required window."""

    as_of = parse_iso_date(as_of_date)
    required_end = add_months(as_of, holding_window_months)
    max_price_date = max(parse_iso_date(price_date) for _, price_date in market_rows)

    if max_price_date < required_end:
        return (
            False,
            f"forward coverage missing: max {max_price_date.date()} < required {required_end.date()}",
        )
    return True, None


def test_freeze_manifest_gate_rejects_missing_discovery_hashes():
    # Build a minimal synthetic manifest that is frozen but missing required hashes.
    manifest = {
        "discovery_frozen": True,
        "discovery_artifact_hashes": {
            "discovery/raw_documents.parquet": "sha256:demo",
            "discovery/documents.parquet": "sha256:demo",
        },
    }

    ok, reason = validate_manifest_freeze_gate(json.dumps(manifest))

    assert ok is False
    assert reason is not None
    assert "missing discovery_frozen hashes" in reason


def test_freeze_manifest_gate_accepts_complete_hash_map():
    manifest = {
        "discovery_frozen": True,
        "discovery_artifact_hashes": {
            "discovery/raw_documents.parquet": "sha256:raw",
            "discovery/documents.parquet": "sha256:documents",
            "discovery/document_cleaning_log.parquet": "sha256:cleaning",
            "discovery/chunks.parquet": "sha256:chunks",
            "discovery/entities.parquet": "sha256:entities",
            "discovery/entity_aliases.parquet": "sha256:aliases",
            "discovery/edges.parquet": "sha256:edges",
            "discovery/graph.json": "sha256:graph",
        },
    }

    ok, reason = validate_manifest_freeze_gate(json.dumps(manifest))

    assert ok is True
    assert reason is None


def test_chunk_leakage_gate_blocks_future_chunks():
    rows = [
        ("c1", "2024-01-01T10:00:00Z"),
        ("c2", "2024-07-01T00:00:00Z"),
    ]
    ok, violations = validate_chunk_leakage_gate(rows, "2024-06-30")

    assert ok is False
    assert len(violations) == 1
    assert "c2" in violations[0]


def test_forward_window_gate_fails_when_price_series_is_short():
    market_rows = [
        ("AAPL", "2024-03-30"),
    ]
    ok, reason = validate_forward_window_gate(market_rows, "2024-03-31", 3)

    assert ok is False
    assert reason is not None
    assert "forward coverage missing" in reason


# --------------------------------------------------------------------------- #
# OI-6 R1 companion: per-point freeze hash-key shape                           #
# --------------------------------------------------------------------------- #


def validate_per_point_hash_keys(
    hash_map: dict[str, str],
    as_of: str,
) -> tuple[bool, list[str]]:
    """Validate that a per-point run's required hash keys are of the form
    ``discovery/<as_of>/<name>`` (not the flat ``discovery/<name>`` form).

    Returns ``(ok, missing_keys)``.
    """
    required_names = {
        "raw_documents.parquet",
        "documents.parquet",
        "document_cleaning_log.parquet",
        "chunks.parquet",
        "entities.parquet",
        "entity_aliases.parquet",
        "edges.parquet",
        "graph.json",
    }
    expected = {f"discovery/{as_of}/{n}" for n in required_names}
    missing = sorted(expected - set(hash_map))
    return not missing, missing


def test_per_point_hash_keys_use_as_of_segment():
    as_of = "2024-06-30"
    hash_map = {
        f"discovery/{as_of}/raw_documents.parquet": "sha256:a",
        f"discovery/{as_of}/documents.parquet": "sha256:b",
        f"discovery/{as_of}/document_cleaning_log.parquet": "sha256:c",
        f"discovery/{as_of}/chunks.parquet": "sha256:d",
        f"discovery/{as_of}/entities.parquet": "sha256:e",
        f"discovery/{as_of}/entity_aliases.parquet": "sha256:f",
        f"discovery/{as_of}/edges.parquet": "sha256:g",
        f"discovery/{as_of}/graph.json": "sha256:h",
        # a sibling point's keys must not be confused for this point's keys
        "discovery/2024-03-31/graph.json": "sha256:other",
    }
    ok, missing = validate_per_point_hash_keys(hash_map, as_of)
    assert ok is True, f"missing per-point keys: {missing}"
    # The flat key form must NOT satisfy a per-point gate.
    flat_map = {"discovery/graph.json": "sha256:flat"}
    ok2, missing2 = validate_per_point_hash_keys(flat_map, as_of)
    assert ok2 is False
    assert f"discovery/{as_of}/graph.json" in missing2


def test_forward_window_gate_passes_with_coverage():
    market_rows = [
        ("AAPL", "2024-05-01"),
        ("AAPL", "2024-07-01"),
        ("AAPL", "2024-08-31"),
    ]
    ok, reason = validate_forward_window_gate(market_rows, "2024-06-30", 1)

    assert ok is True
    assert reason is None
