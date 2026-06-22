from fastapi.testclient import TestClient
from theme_engine.main import app

client = TestClient(app)


def test_list_runs_returns_created_runs():
    created = client.post("/api/runs/create", json={"as_of_date": "2024-06-30"}).json()
    resp = client.get("/api/runs")
    assert resp.status_code == 200
    ids = {r["run_id"] for r in resp.json()}
    assert created["run_id"] in ids


def test_import_rejects_directory_manifest_with_400():
    run = client.post("/api/runs/create", json={"as_of_date": "2024-06-30"}).json()
    resp = client.post("/api/data/import", json={
        "run_id": run["run_id"], "documents_dir": ".", "source_manifest_path": ".",
    })
    assert resp.status_code == 400
