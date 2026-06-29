"""Run-level data integrity validator.

Validates point-in-time correctness, referential integrity, schema conformance,
reconciliation counts, and non-null required fields across all discovery
artifacts in a run.

Usage::

    from theme_engine.integrity import validate_run, assert_run_ok

    report = validate_run("run_20240630_120000")
    # report.ok is True/False
    # report.checks is a list of CheckResult

    assert_run_ok("run_20240630_120000")   # raises IntegrityError on any hard failure
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import pyarrow.parquet as pq

from . import runs
from .data_import import REQUIRED_RAW_COLUMNS
from .data_cleaning import DOCUMENTS_COLUMNS, CLEANING_LOG_COLUMNS
from .chunking import CHUNKS_COLUMNS
from .extraction import ENTITIES_COLUMNS, EDGES_COLUMNS, EDGE_EXPLANATIONS_COLUMNS
from .entity_resolution import ENTITY_ALIASES_COLUMNS

# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------


@dataclass
class CheckResult:
    """Result of a single integrity check."""

    check: str
    """Short check name (e.g. 'PIT_NO_LEAKAGE')."""

    passed: bool
    """True when no violations were found."""

    violations: list[str] = field(default_factory=list)
    """Human-readable description of each violation, naming artifact + id(s)."""

    note: str = ""
    """Informational note (e.g. 'artifact absent — skipped')."""


@dataclass
class IntegrityReport:
    """Top-level result of validate_run."""

    run_id: str
    ok: bool
    """True when all present checks passed."""

    checks: list[CheckResult] = field(default_factory=list)


class IntegrityError(RuntimeError):
    """Raised by assert_run_ok when hard failures exist."""


# ---------------------------------------------------------------------------
# Artifact schema constants (keyed by artifact name, values = required cols)
# These match the actual pipeline column lists, which are the source of truth.
# ---------------------------------------------------------------------------

# raw_documents.parquet — actual columns produced by data_import.py
RAW_DOCUMENTS_COLUMNS: list[str] = REQUIRED_RAW_COLUMNS

# Canonical schema map: artifact basename (without .parquet) -> required columns
ARTIFACT_SCHEMA: dict[str, list[str]] = {
    "raw_documents": RAW_DOCUMENTS_COLUMNS,
    "documents": DOCUMENTS_COLUMNS,
    "document_cleaning_log": CLEANING_LOG_COLUMNS,
    "chunks": CHUNKS_COLUMNS,
    "entities": ENTITIES_COLUMNS,
    "edges": EDGES_COLUMNS,
    "edge_explanations": EDGE_EXPLANATIONS_COLUMNS,
    "entity_aliases": ENTITY_ALIASES_COLUMNS,
}

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _discovery_path(discovery_dir: Path, name: str) -> Path:
    # OI-6 R1: callers pass the resolved discovery dir (flat or per-point).
    return discovery_dir / f"{name}.parquet"


def _load(run_dir: Path, name: str) -> Optional[list[dict]]:
    """Load a parquet artifact as a list of dicts, or None if absent."""
    p = _discovery_path(run_dir, name)
    if not p.exists():
        return None
    table = pq.read_table(p)
    return table.to_pylist()


def _load_columns(run_dir: Path, name: str) -> Optional[list[str]]:
    """Return the column names of a parquet artifact, or None if absent."""
    p = _discovery_path(run_dir, name)
    if not p.exists():
        return None
    table = pq.read_table(p)
    return table.schema.names


def _to_date_str(val) -> str:
    """Coerce a date/timestamp value to a YYYY-MM-DD string."""
    if val is None:
        return ""
    from datetime import date, datetime  # local import to avoid module-level dep
    if isinstance(val, (date, datetime)):
        return val.strftime("%Y-%m-%d")
    s = str(val)
    if "T" in s:
        return s.split("T")[0]
    return s[:10]


# ---------------------------------------------------------------------------
# Check implementations
# ---------------------------------------------------------------------------


def _check_pit_no_leakage(
    run_dir: Path,
    as_of_date: str,
) -> CheckResult:
    """Check 1: PIT / NO-LEAKAGE.

    Every row in documents / chunks / entities / edges must have its
    available_at (or first_seen_at for edges) <= run as_of_date.

    Additionally, every chunk.available_at must equal the available_at
    of its parent document (inheritance).
    """
    violations: list[str] = []

    # documents.available_at <= as_of_date
    docs = _load(run_dir, "documents")
    doc_available_at: dict[str, str] = {}
    if docs is not None:
        for row in docs:
            doc_id = str(row.get("document_id") or "")
            avail = _to_date_str(row.get("available_at"))
            if doc_id:
                doc_available_at[doc_id] = avail
            if avail and avail > as_of_date:
                violations.append(
                    f"documents: document_id={doc_id!r} available_at={avail!r} > as_of_date={as_of_date!r}"
                )

    # chunks.available_at <= as_of_date AND inherits from document
    chunks = _load(run_dir, "chunks")
    if chunks is not None:
        for row in chunks:
            chunk_id = str(row.get("chunk_id") or "")
            doc_id = str(row.get("document_id") or "")
            avail = _to_date_str(row.get("available_at"))

            if avail and avail > as_of_date:
                violations.append(
                    f"chunks: chunk_id={chunk_id!r} available_at={avail!r} > as_of_date={as_of_date!r}"
                )

            # Inheritance: chunk.available_at must match document.available_at
            if doc_id and doc_id in doc_available_at:
                expected = doc_available_at[doc_id]
                if avail != expected:
                    violations.append(
                        f"chunks: chunk_id={chunk_id!r} available_at={avail!r} "
                        f"!= parent document_id={doc_id!r} available_at={expected!r} "
                        f"(inheritance violation)"
                    )

    # entities — no available_at in spec; use first_seen_at as proxy
    # (entities use first_seen_at, not available_at)
    entities = _load(run_dir, "entities")
    if entities is not None:
        for row in entities:
            entity_id = str(row.get("entity_id") or "")
            first_seen = _to_date_str(row.get("first_seen_at"))
            if first_seen and first_seen > as_of_date:
                violations.append(
                    f"entities: entity_id={entity_id!r} first_seen_at={first_seen!r} > as_of_date={as_of_date!r}"
                )

    # edges — use first_seen_at
    edges = _load(run_dir, "edges")
    if edges is not None:
        for row in edges:
            edge_id = str(row.get("edge_id") or "")
            first_seen = _to_date_str(row.get("first_seen_at"))
            if first_seen and first_seen > as_of_date:
                violations.append(
                    f"edges: edge_id={edge_id!r} first_seen_at={first_seen!r} > as_of_date={as_of_date!r}"
                )

    return CheckResult(
        check="PIT_NO_LEAKAGE",
        passed=len(violations) == 0,
        violations=violations,
    )


def _check_referential_integrity(run_dir: Path) -> CheckResult:
    """Check 2: REFERENTIAL INTEGRITY (no orphans).

    - documents.raw_document_id ⊆ raw_documents.document_id
    - chunks.document_id ⊆ documents.document_id
    - edges.source_entity_id + target_entity_id ⊆ entities.entity_id
    - for document_stated edges: every id in evidence_chunk_ids ∈ chunks.chunk_id
    - entity_aliases.canonical_entity_id ⊆ entities.entity_id
    """
    violations: list[str] = []

    # Collect raw_documents primary key set
    raw_docs = _load(run_dir, "raw_documents")
    raw_doc_ids: set[str] = set()
    if raw_docs is not None:
        for row in raw_docs:
            # The pipeline stores the raw document PK in 'document_id' column
            doc_id = str(row.get("document_id") or "")
            if doc_id:
                raw_doc_ids.add(doc_id)

    # Collect documents primary key set + check FK to raw_documents
    docs = _load(run_dir, "documents")
    doc_ids: set[str] = set()
    if docs is not None:
        for row in docs:
            doc_id = str(row.get("document_id") or "")
            if doc_id:
                doc_ids.add(doc_id)
            raw_doc_id = str(row.get("raw_document_id") or "")
            if raw_doc_ids and raw_doc_id and raw_doc_id not in raw_doc_ids:
                violations.append(
                    f"documents: document_id={doc_id!r} has raw_document_id={raw_doc_id!r} "
                    f"not in raw_documents.document_id"
                )

    # Collect chunks primary key set + check FK to documents
    chunks = _load(run_dir, "chunks")
    chunk_ids: set[str] = set()
    if chunks is not None:
        for row in chunks:
            chunk_id = str(row.get("chunk_id") or "")
            if chunk_id:
                chunk_ids.add(chunk_id)
            doc_id = str(row.get("document_id") or "")
            if doc_ids and doc_id and doc_id not in doc_ids:
                violations.append(
                    f"chunks: chunk_id={chunk_id!r} has document_id={doc_id!r} "
                    f"not in documents.document_id"
                )

    # Collect entity primary key set
    entities = _load(run_dir, "entities")
    entity_ids: set[str] = set()
    if entities is not None:
        for row in entities:
            entity_id = str(row.get("entity_id") or "")
            if entity_id:
                entity_ids.add(entity_id)

    # Edges: check source/target entity FK + evidence chunk FK for document_stated
    edges = _load(run_dir, "edges")
    if edges is not None:
        for row in edges:
            edge_id = str(row.get("edge_id") or "")
            src = str(row.get("source_entity_id") or "")
            tgt = str(row.get("target_entity_id") or "")

            if entity_ids and src and src not in entity_ids:
                violations.append(
                    f"edges: edge_id={edge_id!r} source_entity_id={src!r} not in entities.entity_id"
                )
            if entity_ids and tgt and tgt not in entity_ids:
                violations.append(
                    f"edges: edge_id={edge_id!r} target_entity_id={tgt!r} not in entities.entity_id"
                )

            # For document_stated edges, evidence_chunk_ids must exist in chunks
            method = str(row.get("extraction_method") or "")
            if method == "document_stated" and chunk_ids:
                evidence = row.get("evidence_chunk_ids") or []
                if hasattr(evidence, "as_py"):
                    evidence = evidence.as_py()
                for cid in evidence:
                    cid_str = str(cid) if cid is not None else ""
                    if cid_str and cid_str not in chunk_ids:
                        violations.append(
                            f"edges: edge_id={edge_id!r} evidence_chunk_id={cid_str!r} "
                            f"not in chunks.chunk_id"
                        )

    # entity_aliases: canonical_entity_id ⊆ entities.entity_id
    aliases = _load(run_dir, "entity_aliases")
    if aliases is not None:
        for row in aliases:
            alias = str(row.get("alias") or "")
            canonical_id = str(row.get("canonical_entity_id") or "")
            if entity_ids and canonical_id and canonical_id not in entity_ids:
                violations.append(
                    f"entity_aliases: alias={alias!r} canonical_entity_id={canonical_id!r} "
                    f"not in entities.entity_id"
                )

    return CheckResult(
        check="REFERENTIAL_INTEGRITY",
        passed=len(violations) == 0,
        violations=violations,
    )


def _check_schema_conformance(run_dir: Path) -> CheckResult:
    """Check 3: SCHEMA CONFORMANCE.

    Each present artifact's columns must equal exactly its expected column set.
    No missing, extra, or renamed columns are allowed.
    """
    violations: list[str] = []

    for artifact_name, expected_cols in ARTIFACT_SCHEMA.items():
        actual_cols = _load_columns(run_dir, artifact_name)
        if actual_cols is None:
            # Artifact absent; skip (caller handles the skip note)
            continue

        expected_set = set(expected_cols)
        actual_set = set(actual_cols)

        missing = sorted(expected_set - actual_set)
        extra = sorted(actual_set - expected_set)

        if missing:
            violations.append(
                f"{artifact_name}.parquet: missing columns {missing}"
            )
        if extra:
            violations.append(
                f"{artifact_name}.parquet: unexpected extra columns {extra}"
            )

    return CheckResult(
        check="SCHEMA_CONFORMANCE",
        passed=len(violations) == 0,
        violations=violations,
    )


def _check_reconciliation(run_dir: Path) -> CheckResult:
    """Check 4: RECONCILIATION.

    documents(included) + cleaning_log(distinct quarantined raw_document_id)
    == raw_documents count (no silent drops).

    Also: every quarantined record in the cleaning log must have a non-empty
    warning_message (reason for quarantine).
    """
    violations: list[str] = []

    raw_docs = _load(run_dir, "raw_documents")
    docs = _load(run_dir, "documents")
    log = _load(run_dir, "document_cleaning_log")

    if raw_docs is None:
        return CheckResult(
            check="RECONCILIATION",
            passed=True,
            violations=[],
            note="raw_documents absent — skipped",
        )
    if docs is None or log is None:
        return CheckResult(
            check="RECONCILIATION",
            passed=True,
            violations=[],
            note="documents or document_cleaning_log absent — skipped",
        )

    raw_count = len(raw_docs)

    # Count included documents (those with raw_document_id present in documents)
    included_count = len(docs)

    # Count distinctly quarantined raw_document_ids from the cleaning log
    quarantined_ids: set[str] = set()
    for row in log:
        status = str(row.get("status") or "")
        raw_doc_id = str(row.get("raw_document_id") or "")
        if status == "quarantined":
            if not row.get("warning_message"):
                violations.append(
                    f"document_cleaning_log: quarantined raw_document_id={raw_doc_id!r} "
                    f"has no warning_message"
                )
            if raw_doc_id:
                quarantined_ids.add(raw_doc_id)

    # Count raw_document_ids that were silently dropped (neither in docs nor quarantined)
    raw_doc_ids_in_raw = {str(r.get("document_id") or "") for r in raw_docs}
    raw_doc_ids_in_docs = {str(r.get("raw_document_id") or "") for r in docs}
    accounted_for = raw_doc_ids_in_docs | quarantined_ids
    silent_drops = raw_doc_ids_in_raw - accounted_for - {""}

    if silent_drops:
        violations.append(
            f"reconciliation: {len(silent_drops)} raw document(s) silently dropped "
            f"(not in documents or cleaning_log quarantine): {sorted(silent_drops)}"
        )

    # Numeric reconciliation: included + quarantined_distinct == raw_count
    accounted_count = included_count + len(quarantined_ids)
    if accounted_count != raw_count:
        violations.append(
            f"reconciliation: raw_documents={raw_count}, "
            f"documents(included)={included_count}, "
            f"quarantined_distinct={len(quarantined_ids)}, "
            f"total accounted={accounted_count} != {raw_count}"
        )

    return CheckResult(
        check="RECONCILIATION",
        passed=len(violations) == 0,
        violations=violations,
    )


def _check_non_null(run_dir: Path) -> CheckResult:
    """Check 5: NON-NULL required id + available_at fields.

    Checks that these required fields are non-empty:
    - raw_documents: document_id
    - documents: document_id, available_at
    - chunks: chunk_id, available_at
    - entities: entity_id
    - edges: edge_id
    - entity_aliases: (no specific id column; canonical_entity_id is FK)
    """
    violations: list[str] = []

    REQUIRED_NON_NULL: dict[str, list[str]] = {
        "raw_documents": ["document_id"],
        "documents": ["document_id", "available_at"],
        "chunks": ["chunk_id", "available_at"],
        "entities": ["entity_id"],
        "edges": ["edge_id"],
    }

    for artifact_name, required_cols in REQUIRED_NON_NULL.items():
        rows = _load(run_dir, artifact_name)
        if rows is None:
            continue
        for row_idx, row in enumerate(rows):
            for col in required_cols:
                val = row.get(col)
                if val is None or str(val).strip() == "":
                    # Try to get the primary id for better reporting
                    id_col_map = {
                        "raw_documents": "document_id",
                        "documents": "document_id",
                        "chunks": "chunk_id",
                        "entities": "entity_id",
                        "edges": "edge_id",
                    }
                    id_col = id_col_map.get(artifact_name, "")
                    id_val = row.get(id_col) if id_col else None
                    id_str = f"id={id_val!r}" if id_val else f"row_index={row_idx}"
                    violations.append(
                        f"{artifact_name}.parquet: {id_str} has null/empty {col!r}"
                    )

    return CheckResult(
        check="NON_NULL",
        passed=len(violations) == 0,
        violations=violations,
    )


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def validate_run(run_id: str, as_of: str | None = None) -> IntegrityReport:
    """Validate a run's discovery artifacts for integrity.

    Runs all checks for artifacts that exist in the run.
    Missing artifacts are noted but do not cause failures (the check is
    skipped with a note rather than counted as a violation).

    Args:
        run_id: The run identifier (must have a run_manifest.json).
        as_of: Optional per-point selector (OI-6 R1); defaults to latest/flat.

    Returns:
        IntegrityReport with ok=True iff all present checks passed.
    """
    manifest = runs.load_manifest(run_id)
    if manifest is None:
        raise ValueError(f"run not found or manifest missing: {run_id!r}")

    # OI-6 R1: the _check_* helpers consume the resolved discovery dir.
    discovery_dir = runs.discovery_point_dir(run_id, as_of)
    as_of_date: str = as_of if as_of is not None else manifest.as_of_date
    checks: list[CheckResult] = []

    # 1. PIT / No-leakage
    checks.append(_check_pit_no_leakage(discovery_dir, as_of_date))

    # 2. Referential integrity
    checks.append(_check_referential_integrity(discovery_dir))

    # 3. Schema conformance
    checks.append(_check_schema_conformance(discovery_dir))

    # 4. Reconciliation
    checks.append(_check_reconciliation(discovery_dir))

    # 5. Non-null required fields
    checks.append(_check_non_null(discovery_dir))

    ok = all(c.passed for c in checks)
    return IntegrityReport(run_id=run_id, ok=ok, checks=checks)


def assert_run_ok(run_id: str) -> None:
    """Run validate_run and raise IntegrityError if any hard failure exists.

    Use as a pipeline gate::

        from theme_engine.integrity import assert_run_ok
        assert_run_ok(run_id)   # raises IntegrityError if any check fails
    """
    report = validate_run(run_id)
    if not report.ok:
        failures = [
            f"[{c.check}] {v}"
            for c in report.checks
            if not c.passed
            for v in c.violations
        ]
        raise IntegrityError(
            f"Run {run_id!r} failed integrity checks ({len(failures)} violation(s)):\n"
            + "\n".join(f"  - {f}" for f in failures)
        )
