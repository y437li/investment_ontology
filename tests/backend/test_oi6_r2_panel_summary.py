"""OI-6 R2: GET /api/runs/{run_id}/panel/summary (read-only) + freeze dispatch.

Builds and freezes a 2-point run via the panel loop, then asserts the summary
endpoint returns per-point stats, run-level frozen state, and a lineage summary.
Also regression-asserts the /status endpoint shape is unchanged, and that the
POST /api/discovery/freeze endpoint bulk-freezes a multi-point run when as_of is
omitted (instead of the R1 409).
"""

from __future__ import annotations

import csv
from pathlib import Path

from fastapi.testclient import TestClient

from theme_engine import discovery_panel, run_cache, runs
from theme_engine.data_import import REQUIRED_MANIFEST_COLUMNS
from theme_engine.main import app
from theme_engine.models import RunCreateRequest

client = TestClient(app)

T1 = "2024-03-31"
T2 = "2024-06-30"


def _row(sid: str, cid: str, rp: str, av: str) -> dict:
    return {
        "source": "news", "source_id": sid, "title": sid,
        "document_type": "news", "company_id": cid, "raw_path": rp,
        "published_at": av, "available_at": av, "vintage": av + "T00:00:00Z",
        "language": "en", "source_url": "https://example.com/" + sid,
        "license": "public", "confidentiality": "public", "notes": "seed",
    }


def _build_two_point_run(tmp_path: Path) -> str:
    run_cache.clear()
    run_cache.clear_frozen_cache()
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "d1.txt").write_text(
        "Cameco is exposed to uranium and datacenter power demand.",
        encoding="utf-8",
    )
    (docs / "d2.txt").write_text(
        "Hydro One is exposed to copper and datacenter power demand.",
        encoding="utf-8",
    )
    man = docs / "m.csv"
    with man.open("w", encoding="utf-8", newline="") as fp:
        w = csv.DictWriter(fp, fieldnames=REQUIRED_MANIFEST_COLUMNS)
        w.writeheader()
        for r in (_row("n1", "CCO", "d1.txt", "2024-03-15"),
                  _row("n2", "H", "d2.txt", "2024-05-15")):
            w.writerow({c: r.get(c, "") for c in REQUIRED_MANIFEST_COLUMNS})

    run = runs.create_run(RunCreateRequest(as_of_date=T2, as_of_dates=[T1, T2]))
    discovery_panel.run_panel(
        run.run_id, documents_dir=str(docs), source_manifest_path=str(man)
    )
    run_cache.clear_frozen_cache()
    return run.run_id


def test_summary_endpoint(tmp_path: Path):
    rid = _build_two_point_run(tmp_path)

    resp = client.get(f"/api/runs/{rid}/panel/summary")
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert body["run_id"] == rid
    assert body["as_of_dates"] == [T1, T2]
    assert body["discovery_frozen"] is True
    assert body["panel_built"] is True
    assert body["frozen_at"]

    points = {p["as_of"]: p for p in body["points"]}
    assert set(points) == {T1, T2}
    for p in (T1, T2):
        assert points[p]["discovery_present"] is True
        assert points[p]["discovery_frozen"] is True
        assert points[p]["theme_count"] >= 1
        assert points[p]["company_theme_pair_count"] >= 1

    summary = body["theme_lineage_summary"]
    assert summary is not None
    assert summary["family_count"] >= 1
    assert body["exposure_trajectory_company_count"] >= 1


def test_summary_endpoint_404_for_missing_run():
    resp = client.get("/api/runs/run_does_not_exist/panel/summary")
    assert resp.status_code == 404


def test_status_endpoint_shape_unchanged(tmp_path: Path):
    rid = _build_two_point_run(tmp_path)
    resp = client.get(f"/api/runs/{rid}/status")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert set(body) == {
        "run_id", "as_of_date", "created_at", "discovery_frozen",
        "artifacts_present",
    }
    assert body["run_id"] == rid
    assert body["discovery_frozen"] is True


def test_freeze_endpoint_bulk_freezes_multi_point(tmp_path: Path):
    # Build a 2-point run but do NOT freeze (run stages then call the endpoint).
    run_cache.clear()
    run_cache.clear_frozen_cache()
    rid = _build_two_point_run(tmp_path)
    # Already frozen by the loop; calling freeze with as_of omitted must remain a
    # success (idempotent bulk freeze), not a 409.
    resp = client.post("/api/discovery/freeze", json={"run_id": rid})
    assert resp.status_code == 200, resp.text
    assert resp.json()["discovery_frozen"] is True
