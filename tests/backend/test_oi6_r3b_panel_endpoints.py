"""OI-6 R3b: read-only panel endpoints consumed by the Vue Panel view.

Covers the three dedicated GET endpoints added in main.py:
  - GET /api/runs/{run_id}/panel/lineage       -> panel/theme_lineage.json
  - GET /api/runs/{run_id}/panel/trajectories  -> panel/exposure_trajectories.parquet (as JSON records)
  - GET /api/runs/{run_id}/panel/validation    -> panel/validation_panel.json

Builds a hermetic 2-point run via the panel loop (rule-based extractor, no LLM,
no network), which writes theme_lineage.json + exposure_trajectories.parquet.
The validation panel is written directly to panel/ (no price data needed).
Also asserts clean 404s for legacy/single-point runs with no panel/ artifacts.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

from fastapi.testclient import TestClient

from theme_engine import discovery_panel, run_cache, runs, validation as validation_mod
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


def _write_validation_panel(run_id: str) -> dict:
    """Write a per-point validation_panel.json directly (no price data needed)."""
    doc = {
        "schema_version": validation_mod.VALIDATION_PANEL_SCHEMA_VERSION,
        "run_id": run_id,
        "forward_window": "3M",
        "baseline": "equal_weight_universe",
        "coverage_policy": "skip",
        "min_points_for_claim": 4,
        "n_points": 1,
        "n_points_authored": 2,
        "mean_excess": 0.05,
        "hit_rate": 1.0,
        "claim_supported": False,
        "illustrative": True,
        "generated_at": "2026-06-29T00:00:00Z",
        "points": [
            {
                "as_of": T1,
                "window_end": "2024-06-30",
                "basket_return": 0.12,
                "baseline_return": 0.07,
                "excess": 0.05,
                "covered": True,
                "skipped_reason": None,
            },
            {
                "as_of": T2,
                "window_end": "2024-09-30",
                "basket_return": None,
                "baseline_return": None,
                "excess": None,
                "covered": False,
                "skipped_reason": "insufficient_forward_coverage",
            },
        ],
    }
    panel_path = runs.panel_dir(run_id, for_write=True) / validation_mod.VALIDATION_PANEL_FILENAME
    panel_path.write_text(json.dumps(doc, indent=2), encoding="utf-8")
    return doc


def test_lineage_endpoint(tmp_path: Path):
    rid = _build_two_point_run(tmp_path)
    resp = client.get(f"/api/runs/{rid}/panel/lineage")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["run_id"] == rid
    assert body["schema_version"] == discovery_panel.LINEAGE_SCHEMA_VERSION
    assert body["points"] == [T1, T2]
    assert isinstance(body["families"], list) and len(body["families"]) >= 1
    assert isinstance(body["lineages"], list) and len(body["lineages"]) >= 1
    fam = body["families"][0]
    assert set(fam["states_by_point"]) == {T1, T2}
    # internal helper maps must be stripped from the artifact
    assert not any(k.startswith("_") for k in body)


def test_trajectories_endpoint(tmp_path: Path):
    rid = _build_two_point_run(tmp_path)
    resp = client.get(f"/api/runs/{rid}/panel/trajectories")
    assert resp.status_code == 200, resp.text
    rows = resp.json()
    assert isinstance(rows, list) and len(rows) >= 1
    expected_cols = set(discovery_panel.TRAJECTORY_COLUMNS)
    assert expected_cols.issubset(set(rows[0]))
    assert {r["as_of_date"] for r in rows}.issubset({T1, T2})
    assert all(r["run_id"] == rid for r in rows)


def test_validation_endpoint(tmp_path: Path):
    rid = _build_two_point_run(tmp_path)
    written = _write_validation_panel(rid)
    resp = client.get(f"/api/runs/{rid}/panel/validation")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["run_id"] == rid
    assert body["claim_supported"] is False
    assert body["illustrative"] is True
    assert len(body["points"]) == 2
    covered = [p for p in body["points"] if p["covered"]]
    skipped = [p for p in body["points"] if not p["covered"]]
    assert len(covered) == 1 and len(skipped) == 1
    assert skipped[0]["skipped_reason"] == "insufficient_forward_coverage"
    assert body == written


def test_endpoints_404_for_missing_run():
    for sub in ("lineage", "trajectories", "validation"):
        resp = client.get(f"/api/runs/run_does_not_exist/panel/{sub}")
        assert resp.status_code == 404, f"{sub}: {resp.text}"


def test_endpoints_404_for_single_point_run(tmp_path: Path):
    # A run with no panel/ artifacts (legacy/single-point) must 404 cleanly.
    run_cache.clear()
    run = runs.create_run(RunCreateRequest(as_of_date=T1))
    for sub in ("lineage", "trajectories", "validation"):
        resp = client.get(f"/api/runs/{run.run_id}/panel/{sub}")
        assert resp.status_code == 404, f"{sub}: {resp.text}"
