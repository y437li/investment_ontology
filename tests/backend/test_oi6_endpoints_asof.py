"""OI-6 R1-4 / R1-5: endpoint default-latest, ?as_of= selection, 404, legacy flat.

The artifact endpoint defaults to the latest point, honours an explicit
?as_of=, returns 404 for an unknown point, and a legacy flat run (no
as_of_dates) still serves with no ?as_of=.
"""

from __future__ import annotations

import json

from fastapi.testclient import TestClient

from theme_engine import runs
from theme_engine.main import app
from theme_engine.models import RunCreateRequest

client = TestClient(app)

T1 = "2024-03-31"
T2 = "2024-06-30"


def _write_graph(run_id: str, as_of: str | None, payload: dict) -> None:
    d = runs.discovery_point_dir(run_id, as_of, for_write=True)
    (d / "graph.json").write_text(json.dumps(payload), encoding="utf-8")


def test_default_latest_and_explicit_point_and_unknown_404():
    run = runs.create_run(RunCreateRequest(as_of_date=T1, as_of_dates=[T1, T2]))
    run_id = run.run_id
    _write_graph(run_id, T1, {"point": "t1"})
    _write_graph(run_id, T2, {"point": "t2"})

    # No ?as_of= → latest point (T2).
    r = client.get(f"/api/artifacts/{run_id}/graph.json")
    assert r.status_code == 200, r.text
    assert r.json() == {"point": "t2"}

    # ?as_of=T1 → T1 subtree.
    r1 = client.get(f"/api/artifacts/{run_id}/graph.json", params={"as_of": T1})
    assert r1.status_code == 200, r1.text
    assert r1.json() == {"point": "t1"}

    # Unknown point → 404.
    r404 = client.get(f"/api/artifacts/{run_id}/graph.json", params={"as_of": "2099-01-01"})
    assert r404.status_code == 404


def test_legacy_flat_run_serves_without_as_of():
    run = runs.create_run(RunCreateRequest(as_of_date=T2))  # no as_of_dates → flat
    run_id = run.run_id
    assert run.as_of_dates is None
    _write_graph(run_id, None, {"point": "flat"})

    r = client.get(f"/api/artifacts/{run_id}/graph.json")
    assert r.status_code == 200, r.text
    assert r.json() == {"point": "flat"}
