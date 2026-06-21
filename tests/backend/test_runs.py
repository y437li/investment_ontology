"""Milestone 1 acceptance: a run can be created with a valid manifest."""

import json
from pathlib import Path

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
