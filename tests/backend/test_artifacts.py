"""Tests for GET /api/artifacts/{run_id}/{artifact_name}.

Assertions:
  (a) Allowlisted JSON artifact returns 200 with correct content.
  (b) Allowlisted Parquet artifact returns 200 as JSON records.
  (c) Allowlisted Markdown artifact returns 200 as text/markdown.
  (d) Allowlisted CSV artifact returns 200 as JSON records.
  (e) Unknown artifact name returns 400.
  (f) Path traversal ('..') returns 400.
  (g) Missing run returns 404.
  (h) Allowlisted name but missing file returns 404.
"""

from __future__ import annotations

import json
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from fastapi.testclient import TestClient

from theme_engine.config import settings
from theme_engine.main import app
from theme_engine import runs
from theme_engine.models import RunCreateRequest

client = TestClient(app)


def _create_run() -> str:
    """Create a minimal run and return its run_id."""
    resp = client.post("/api/runs/create", json={"as_of_date": "2024-06-30"})
    assert resp.status_code == 200, resp.text
    return resp.json()["run_id"]


def _discovery_dir(run_id: str) -> Path:
    return settings.run_output_dir / run_id / "discovery"


def _validation_dir(run_id: str) -> Path:
    return settings.run_output_dir / run_id / "validation"


# ---------------------------------------------------------------------------
# (a) JSON artifact: graph.json
# ---------------------------------------------------------------------------


def test_serve_graph_json():
    """GET graph.json returns 200 with JSON content matching what was written."""
    run_id = _create_run()
    disc = _discovery_dir(run_id)
    disc.mkdir(parents=True, exist_ok=True)

    payload = {"schema_version": "1.0", "run_id": run_id, "nodes": [], "edges": []}
    (disc / "graph.json").write_text(json.dumps(payload), encoding="utf-8")

    resp = client.get(f"/api/artifacts/{run_id}/graph.json")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["run_id"] == run_id
    assert body["nodes"] == []


def test_serve_communities_json():
    """GET communities.json returns 200."""
    run_id = _create_run()
    disc = _discovery_dir(run_id)
    disc.mkdir(parents=True, exist_ok=True)

    payload = {"schema_version": "1.0", "run_id": run_id, "communities": []}
    (disc / "communities.json").write_text(json.dumps(payload), encoding="utf-8")

    resp = client.get(f"/api/artifacts/{run_id}/communities.json")
    assert resp.status_code == 200, resp.text
    assert resp.json()["communities"] == []


def test_serve_theme_snapshots_json():
    """GET theme_snapshots.json returns 200."""
    run_id = _create_run()
    disc = _discovery_dir(run_id)
    disc.mkdir(parents=True, exist_ok=True)

    payload = {"schema_version": "1.0", "run_id": run_id, "snapshots": []}
    (disc / "theme_snapshots.json").write_text(json.dumps(payload), encoding="utf-8")

    resp = client.get(f"/api/artifacts/{run_id}/theme_snapshots.json")
    assert resp.status_code == 200, resp.text
    assert resp.json()["snapshots"] == []


def test_serve_theme_lineage_json():
    """GET theme_lineage.json returns 200."""
    run_id = _create_run()
    disc = _discovery_dir(run_id)
    disc.mkdir(parents=True, exist_ok=True)

    payload = {"schema_version": "1.0", "run_id": run_id, "lineages": []}
    (disc / "theme_lineage.json").write_text(json.dumps(payload), encoding="utf-8")

    resp = client.get(f"/api/artifacts/{run_id}/theme_lineage.json")
    assert resp.status_code == 200, resp.text
    assert resp.json()["lineages"] == []


# ---------------------------------------------------------------------------
# (b) Parquet artifact: theme_metrics.parquet returned as JSON records
# ---------------------------------------------------------------------------


def test_serve_theme_metrics_parquet_as_json():
    """GET theme_metrics.parquet returns 200 with JSON array of records."""
    run_id = _create_run()
    disc = _discovery_dir(run_id)
    disc.mkdir(parents=True, exist_ok=True)

    rows = [{"community_id": "community_0", "strength": 0.8, "cohesion": 0.7}]
    table = pa.Table.from_pylist(rows)
    pq.write_table(table, disc / "theme_metrics.parquet")

    resp = client.get(f"/api/artifacts/{run_id}/theme_metrics.parquet")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert isinstance(body, list)
    assert len(body) == 1
    assert body[0]["community_id"] == "community_0"


def test_serve_company_theme_exposure_parquet_as_json():
    """GET company_theme_exposure.parquet returns 200 with JSON array of records."""
    run_id = _create_run()
    disc = _discovery_dir(run_id)
    disc.mkdir(parents=True, exist_ok=True)

    rows = [{"company_id": "c1", "community_id": "community_0", "exposure_score": 0.5}]
    table = pa.Table.from_pylist(rows)
    pq.write_table(table, disc / "company_theme_exposure.parquet")

    resp = client.get(f"/api/artifacts/{run_id}/company_theme_exposure.parquet")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert isinstance(body, list)
    assert body[0]["company_id"] == "c1"


# ---------------------------------------------------------------------------
# (c) Markdown artifact: report.md returned as text/markdown
# ---------------------------------------------------------------------------


def test_serve_report_md():
    """GET report.md returns 200 as text/markdown."""
    run_id = _create_run()
    run_dir = settings.run_output_dir / run_id
    (run_dir / "report.md").write_text("# Report\nHello world.", encoding="utf-8")

    resp = client.get(f"/api/artifacts/{run_id}/report.md")
    assert resp.status_code == 200, resp.text
    assert "text/markdown" in resp.headers.get("content-type", "")
    assert "Hello world" in resp.text


# ---------------------------------------------------------------------------
# (d) CSV artifact: validation/validation.csv returned as JSON records
# ---------------------------------------------------------------------------


def test_serve_validation_csv_as_json():
    """GET validation/validation.csv returns 200 with JSON array of records."""
    run_id = _create_run()
    val_dir = _validation_dir(run_id)
    val_dir.mkdir(parents=True, exist_ok=True)

    csv_content = "community_id,theme_name,strength\ncommunity_0,AI Infrastructure,0.85\n"
    (val_dir / "validation.csv").write_text(csv_content, encoding="utf-8")

    resp = client.get(f"/api/artifacts/{run_id}/validation/validation.csv")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert isinstance(body, list)
    assert body[0]["community_id"] == "community_0"
    assert body[0]["theme_name"] == "AI Infrastructure"


# ---------------------------------------------------------------------------
# (e) Unknown artifact name -> 400
# ---------------------------------------------------------------------------


def test_unknown_artifact_name_returns_400():
    """Artifact names not in the allowlist get 400."""
    run_id = _create_run()
    resp = client.get(f"/api/artifacts/{run_id}/secret_config.yml")
    assert resp.status_code == 400, resp.text


def test_unknown_artifact_subpath_returns_400():
    """Subpath that doesn't match any allowlisted name returns 400."""
    run_id = _create_run()
    resp = client.get(f"/api/artifacts/{run_id}/discovery/raw_documents.parquet")
    assert resp.status_code == 400, resp.text


# ---------------------------------------------------------------------------
# (f) Path traversal -> 400
# ---------------------------------------------------------------------------


def test_traversal_double_dot_returns_400():
    """Path traversal with '..' is rejected with 400."""
    run_id = _create_run()
    resp = client.get(f"/api/artifacts/{run_id}/../../../etc/passwd")
    # FastAPI may normalise the path and return 400 from our guard,
    # or the router may raise its own 404. Either is acceptable since the
    # content is NOT served; assert it's not 200.
    assert resp.status_code in {400, 404, 422}, (
        f"Expected 400/404/422 for traversal attempt, got {resp.status_code}"
    )


def test_traversal_encoded_returns_400():
    """Artifact name containing '..' is rejected with 400."""
    run_id = _create_run()
    # Directly call the artifact function to verify our guard
    from theme_engine.artifacts import serve_artifact
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc_info:
        serve_artifact(run_id=run_id, artifact_name="../run_manifest.json")
    assert exc_info.value.status_code == 400


# ---------------------------------------------------------------------------
# (f2) run_id path traversal -> 400 (audit CLUSTER A)
# ---------------------------------------------------------------------------


def test_run_id_traversal_dotdot_returns_400():
    """A run_id of '..' (percent-decoded by Starlette) must not escape the run dir."""
    from theme_engine.artifacts import serve_artifact
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc_info:
        serve_artifact(run_id="..", artifact_name="graph.json")
    assert exc_info.value.status_code == 400

    # Also reject embedded traversal and separators.
    for bad_run_id in ["../../etc", "a/b", "..\\..\\windows", "/abs"]:
        with pytest.raises(HTTPException) as exc_info:
            serve_artifact(run_id=bad_run_id, artifact_name="graph.json")
        assert exc_info.value.status_code == 400, bad_run_id


def test_run_id_traversal_resolve_path_guarded():
    """_resolve_artifact_path also rejects a traversal run_id (defence-in-depth)."""
    from theme_engine.artifacts import _resolve_artifact_path
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc_info:
        _resolve_artifact_path("..", "graph.json")
    assert exc_info.value.status_code == 400


def test_run_id_traversal_encoded_via_http_not_200():
    """Percent-encoded '..' run_id over HTTP must never serve content."""
    resp = client.get("/api/artifacts/%2e%2e/graph.json")
    assert resp.status_code in {400, 404, 422}, resp.status_code


# ---------------------------------------------------------------------------
# (g) Missing run -> 404
# ---------------------------------------------------------------------------


def test_missing_run_returns_404():
    """Non-existent run_id returns 404."""
    resp = client.get("/api/artifacts/nonexistent_run_xyz/graph.json")
    assert resp.status_code == 404, resp.text


# ---------------------------------------------------------------------------
# (h) Allowlisted name but file missing -> 404
# ---------------------------------------------------------------------------


def test_missing_artifact_file_returns_404():
    """Allowlisted artifact name but file not yet written returns 404."""
    run_id = _create_run()
    # The run exists but we never wrote graph.json
    resp = client.get(f"/api/artifacts/{run_id}/graph.json")
    assert resp.status_code == 404, resp.text
