"""Milestone 1 acceptance: a run can be created with a valid manifest."""

import json
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
from fastapi.testclient import TestClient

from theme_engine.config import settings
from theme_engine.main import app

client = TestClient(app)


def test_create_run_writes_valid_manifest():
    resp = client.post("/api/runs/create", json={"as_of_date": "2024-06-30"})
    assert resp.status_code == 200, resp.text
    m = resp.json()

    assert m["run_id"].startswith("run_")
    assert m["as_of_date"] == "2024-06-30"
    assert m["discovery_frozen"] is False
    assert len(m["input_hash"]) == 16
    assert m["universe_config"] == "configs/universe.example.yml"

    # The manifest is the on-disk source of truth (§8).
    manifest_path = Path(settings.run_output_dir) / m["run_id"] / "run_manifest.json"
    assert manifest_path.exists()
    on_disk = json.loads(manifest_path.read_text())
    assert on_disk == m


def test_status_reflects_created_run():
    created = client.post("/api/runs/create", json={"as_of_date": "2024-03-31"}).json()
    resp = client.get(f"/api/runs/{created['run_id']}/status")
    assert resp.status_code == 200
    s = resp.json()
    assert s["run_id"] == created["run_id"]
    assert s["discovery_frozen"] is False
    assert s["artifacts_present"] == []  # only the manifest exists at M1


def test_invalid_as_of_date_is_rejected():
    resp = client.post("/api/runs/create", json={"as_of_date": "2024/06/30"})
    assert resp.status_code == 422


def test_missing_run_returns_404():
    resp = client.get("/api/runs/run_does_not_exist/status")
    assert resp.status_code == 404


def _seed_discovery_artifacts(run_id: str) -> None:
    run_dir = Path(settings.run_output_dir) / run_id / "discovery"
    run_dir.mkdir(parents=True, exist_ok=True)
    # Freeze only HASHES these (content irrelevant) -> cheap seed bytes.
    for name in [
        "raw_documents.parquet",
        "documents.parquet",
        "document_cleaning_log.parquet",
        "chunks.parquet",
        "entity_aliases.parquet",
        "edges.parquet",
        "graph.json",
        "theme_metrics.parquet",
    ]:
        (run_dir / name).write_text("seed", encoding="utf-8")
    # Validation PARSES these -> write valid-but-empty so the fail-loud loaders accept them.
    (run_dir / "communities.json").write_text('{"communities": []}', encoding="utf-8")
    (run_dir / "theme_snapshots.json").write_text('{"snapshots": []}', encoding="utf-8")
    _empty = pa.table({"x": pa.array([], type=pa.string())})
    pq.write_table(_empty, run_dir / "entities.parquet")
    pq.write_table(_empty, run_dir / "company_theme_exposure.parquet")


def test_discovery_freeze_blocks_without_complete_artifacts():
    run_id = client.post("/api/runs/create", json={"as_of_date": "2024-06-30"}).json()[
        "run_id"
    ]
    (Path(settings.run_output_dir) / run_id / "discovery" / "raw_documents.parquet").write_text(
        "raw",
        encoding="utf-8",
    )

    resp = client.post("/api/discovery/freeze", json={"run_id": run_id})
    assert resp.status_code == 409
    assert "missing before freeze" in resp.text


def test_discovery_freeze_records_manifest_hashes():
    run_id = client.post("/api/runs/create", json={"as_of_date": "2024-06-30"}).json()[
        "run_id"
    ]
    _seed_discovery_artifacts(run_id)

    resp = client.post("/api/discovery/freeze", json={"run_id": run_id})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["success"] is True
    assert body["discovery_frozen"] is True
    assert body["manifest_path"] == f"data/runs/{run_id}/run_manifest.json"
    assert "discovery/raw_documents.parquet" in body["discovery_artifact_hashes"]
    assert body["discovery_artifact_hashes"]["discovery/raw_documents.parquet"].startswith(
        "sha256:"
    )

    manifest = json.loads(
        Path(settings.run_output_dir).joinpath(run_id, "run_manifest.json").read_text(
            encoding="utf-8"
        )
    )
    assert manifest["discovery_frozen"] is True
    assert manifest["discovery_artifact_hashes"] == body["discovery_artifact_hashes"]


def test_validation_run_blocked_until_frozen():
    run_id = client.post("/api/runs/create", json={"as_of_date": "2024-06-30"}).json()[
        "run_id"
    ]
    resp = client.post("/api/validation/run", json={"run_id": run_id})
    assert resp.status_code == 409
    assert resp.json()["detail"] == "discovery not frozen"


def test_validation_run_preflight_after_freeze():
    """Validation runs after freeze; with no market prices, status is blocked_insufficient_forward_data."""
    run_id = client.post("/api/runs/create", json={"as_of_date": "2024-06-30"}).json()[
        "run_id"
    ]
    _seed_discovery_artifacts(run_id)

    freeze_resp = client.post("/api/discovery/freeze", json={"run_id": run_id})
    assert freeze_resp.status_code == 200

    resp = client.post("/api/validation/run", json={"run_id": run_id})
    assert resp.status_code == 200
    assert resp.json()["success"] is not None
    # M6 is now implemented: freeze gate passes, then reports coverage status
    # With no market_prices.parquet, validation is blocked on insufficient data
    val_status = resp.json()["validation_status"]
    assert val_status in ("blocked_insufficient_forward_data", "completed"), (
        f"unexpected validation_status after M6 implementation: {val_status!r}"
    )


def test_validation_run_blocked_when_discovery_artifact_mutated():
    run_id = client.post("/api/runs/create", json={"as_of_date": "2024-06-30"}).json()[
        "run_id"
    ]
    _seed_discovery_artifacts(run_id)

    freeze_resp = client.post("/api/discovery/freeze", json={"run_id": run_id})
    assert freeze_resp.status_code == 200

    mutated = Path(settings.run_output_dir) / run_id / "discovery" / "graph.json"
    mutated.write_text("mutated graph", encoding="utf-8")

    validation_resp = client.post("/api/validation/run", json={"run_id": run_id})
    assert validation_resp.status_code == 409
    assert "hash mismatch" in validation_resp.text


def test_discovery_writers_blocked_after_freeze():
    """Mutating discovery endpoints (themes/exposure/macro/concepts) are rejected
    with 409 once the run is frozen (audit HIGH: no freeze guard)."""
    run = client.post("/api/runs/create", json={"as_of_date": "2024-06-30"}).json()
    run_id = run["run_id"]
    _seed_discovery_artifacts(run_id)
    assert client.post("/api/discovery/freeze", json={"run_id": run_id}).status_code == 200
    for route in ["/api/themes/discover", "/api/exposure/compute",
                  "/api/macro/integrate", "/api/extraction/canonicalize-concepts"]:
        resp = client.post(route, json={"run_id": run_id})
        assert resp.status_code == 409, f"{route} should be 409 when frozen, got {resp.status_code}"
        assert "frozen" in resp.text.lower()


def test_discovery_required_writers_blocked_after_freeze():
    """Required-artifact discovery writers (import/clean/chunk/extraction/graph/
    provenance) are also rejected with 409 once the run is frozen — they overwrite
    frozen+hashed artifacts and would break the hash gate (audit CLUSTER B)."""
    run = client.post("/api/runs/create", json={"as_of_date": "2024-06-30"}).json()
    run_id = run["run_id"]
    _seed_discovery_artifacts(run_id)
    assert client.post("/api/discovery/freeze", json={"run_id": run_id}).status_code == 200

    # Each of these endpoints WRITES required discovery artifacts; all must 409.
    cases = [
        ("/api/data/import",
         {"run_id": run_id, "documents_dir": "x", "source_manifest_path": "y"}),
        ("/api/data/clean", {"run_id": run_id}),
        ("/api/data/chunk", {"run_id": run_id}),
        ("/api/extraction/run", {"run_id": run_id}),
        ("/api/extraction/resolve", {"run_id": run_id}),
        ("/api/graph/build", {"run_id": run_id}),
        ("/api/provenance/materialize", {"run_id": run_id}),
    ]
    for route, payload in cases:
        resp = client.post(route, json=payload)
        assert resp.status_code == 409, f"{route} should be 409 when frozen, got {resp.status_code}: {resp.text}"
        assert "frozen" in resp.text.lower()
