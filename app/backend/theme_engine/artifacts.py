"""Artifact-serving endpoint for run artifacts.

Serves an allowlisted set of run artifacts from data/runs/<run_id>/.
Security:
  - Only allowlisted artifact names are served.
  - Path traversal is rejected (no '..', no absolute paths).
  - 404 if run or artifact is missing.
  - Parquet files are returned as JSON records.
  - JSON/Markdown files are returned with appropriate content-type.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import HTTPException
from fastapi.responses import PlainTextResponse, JSONResponse

from .config import settings

# Allowlisted artifact names — only these may be served.
# Parquet files are served as JSON records.
_JSON_ARTIFACTS = frozenset(
    {
        "graph.json",
        "communities.json",
        "theme_snapshots.json",
        "theme_lineage.json",
    }
)

_PARQUET_ARTIFACTS = frozenset(
    {
        "theme_metrics.parquet",
        "company_theme_exposure.parquet",
    }
)

_CSV_ARTIFACTS = frozenset(
    {
        "validation/validation.csv",
    }
)

_MARKDOWN_ARTIFACTS = frozenset(
    {
        "report.md",
    }
)

ALLOWED_ARTIFACTS: frozenset[str] = (
    _JSON_ARTIFACTS | _PARQUET_ARTIFACTS | _CSV_ARTIFACTS | _MARKDOWN_ARTIFACTS
)


def _reject_traversal(artifact_name: str) -> None:
    """Raise 400 if artifact_name looks like a traversal attempt."""
    if ".." in artifact_name or artifact_name.startswith("/"):
        raise HTTPException(
            status_code=400,
            detail=f"Invalid artifact name: {artifact_name!r}",
        )


def _resolve_artifact_path(run_id: str, artifact_name: str) -> Path:
    """Return the absolute path to the artifact inside the run directory.

    For convenience the frontend can request both 'graph.json' and
    'discovery/graph.json'; this function handles both forms by looking in the
    discovery/ sub-directory for JSON/Parquet artifacts.
    """
    run_dir = settings.run_output_dir / run_id
    if not run_dir.is_dir():
        raise HTTPException(status_code=404, detail=f"run not found: {run_id}")

    # Strip optional 'discovery/' or 'validation/' prefix supplied by callers.
    bare = artifact_name
    for prefix in ("discovery/", "validation/"):
        if bare.startswith(prefix):
            bare = bare[len(prefix):]
            break

    # Try discovery/ sub-dir first for json/parquet artifacts, then run root.
    candidates: list[Path] = []
    if bare in _JSON_ARTIFACTS or bare in _PARQUET_ARTIFACTS:
        candidates.append(run_dir / "discovery" / bare)
    if bare in _MARKDOWN_ARTIFACTS:
        candidates.append(run_dir / bare)
    if bare in {"validation.csv"}:
        candidates.append(run_dir / "validation" / bare)
    # CSV under validation/ path
    if artifact_name == "validation/validation.csv":
        candidates.append(run_dir / "validation" / "validation.csv")

    for p in candidates:
        if p.is_file():
            return p

    # Fall through: artifact doesn't exist
    raise HTTPException(
        status_code=404,
        detail=f"artifact not found: {artifact_name!r} for run {run_id!r}",
    )


def _parquet_to_records(path: Path) -> list[dict[str, Any]]:
    """Read a Parquet file and return records as a list of dicts."""
    try:
        import pyarrow.parquet as pq  # noqa: PLC0415
    except ImportError as exc:
        raise HTTPException(
            status_code=500, detail=f"pyarrow not available: {exc}"
        ) from exc

    table = pq.read_table(path)
    return table.to_pylist()


def _csv_to_records(path: Path) -> list[dict[str, Any]]:
    """Read a CSV file and return records as a list of dicts."""
    import csv  # noqa: PLC0415

    with path.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        return list(reader)


def serve_artifact(run_id: str, artifact_name: str):
    """Serve an allowlisted artifact for the given run.

    Returns a FastAPI response object (JSONResponse or PlainTextResponse).
    """
    _reject_traversal(artifact_name)

    # Normalise: strip leading 'discovery/' or 'validation/' to get the bare name
    bare = artifact_name
    for prefix in ("discovery/", "validation/"):
        if bare.startswith(prefix):
            bare = bare[len(prefix):]
            break

    if artifact_name not in ALLOWED_ARTIFACTS and bare not in ALLOWED_ARTIFACTS:
        raise HTTPException(
            status_code=400,
            detail=f"artifact not in allowlist: {artifact_name!r}",
        )

    # Resolve to file path
    path = _resolve_artifact_path(run_id, artifact_name)

    suffix = path.suffix.lower()

    if suffix == ".json":
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise HTTPException(
                status_code=500, detail=f"failed to read artifact: {exc}"
            ) from exc
        return JSONResponse(content=data)

    if suffix == ".parquet":
        records = _parquet_to_records(path)
        return JSONResponse(content=records)

    if suffix == ".csv":
        records = _csv_to_records(path)
        return JSONResponse(content=records)

    if suffix == ".md":
        content = path.read_text(encoding="utf-8")
        return PlainTextResponse(content=content, media_type="text/markdown")

    # Fallback — should not be reached given the allowlist
    raise HTTPException(status_code=400, detail=f"unsupported artifact type: {suffix}")
