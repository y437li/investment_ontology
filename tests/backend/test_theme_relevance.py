"""Tests for temporal relevance (spec §12 states from evidence recency)."""

import json
import sys
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

BACKEND = Path(__file__).resolve().parents[2] / "app" / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from theme_engine import theme_relevance, runs  # noqa: E402
from theme_engine.config import settings  # noqa: E402
from theme_engine.models import RunCreateRequest  # noqa: E402


def _seed(chunk_dates: dict) -> str:
    """chunk_dates: {chunk_id: available_at}. Two communities: c_fresh, c_stale."""
    run = runs.create_run(RunCreateRequest(as_of_date="2024-06-30"))
    d = Path(settings.run_output_dir) / run.run_id / "discovery"
    d.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.table({"chunk_id": list(chunk_dates), "available_at": list(chunk_dates.values())}),
                   d / "chunks.parquet")
    pq.write_table(pa.table({
        "edge_id": ["e_fresh", "e_stale"],
        "source_entity_id": ["a", "c"], "target_entity_id": ["b", "d"],
        "edge_type": ["benefits", "benefits"],
        "evidence_chunk_ids": [["fresh1", "fresh2"], ["stale1"]],
    }), d / "edges.parquet")
    (d / "communities.json").write_text(json.dumps({"communities": [
        {"community_id": "c_fresh", "edge_ids": ["e_fresh"]},
        {"community_id": "c_stale", "edge_ids": ["e_stale"]},
    ]}))
    return run.run_id


def test_recent_theme_ranks_above_stale_and_states():
    rid = _seed({"fresh1": "2024-06-20", "fresh2": "2024-06-10", "stale1": "2024-01-05"})
    doc = theme_relevance.compute_relevance(rid, window_days=90)
    by = {t["community_id"]: t for t in doc["themes"]}
    assert by["c_fresh"]["relevance_score"] > by["c_stale"]["relevance_score"]
    assert by["c_fresh"]["state"] == "emerging"        # within 90d of as_of
    assert by["c_stale"]["state"] == "dormant"         # ~6 months old, outside window
    assert doc["themes"][0]["community_id"] == "c_fresh"   # sorted by relevance
    assert by["c_fresh"]["last_evidence_at"] == "2024-06-20"


def test_relevance_artifact_written():
    rid = _seed({"fresh1": "2024-06-20", "fresh2": "2024-06-10", "stale1": "2024-01-05"})
    theme_relevance.compute_relevance(rid)
    art = Path(settings.run_output_dir) / rid / "discovery" / "theme_relevance.json"
    assert art.exists()
    assert "as_of_date" in json.loads(art.read_text())


def test_score_excludes_future_dated_evidence():
    """Audit medium: future-dated evidence (not knowable at as_of) is dropped, not
    scored as maximally recent."""
    from datetime import date
    from theme_engine import theme_relevance as tr
    as_of = date(2024, 6, 30)
    r = tr._score(["2024-12-31", "2024-06-20"], as_of, 90)   # one future, one recent
    assert r["evidence_count"] == 1                          # future dropped
    assert r["last_evidence_at"] == "2024-06-20"
    r2 = tr._score(["2025-01-01"], as_of, 90)                # all future
    assert r2["state"] == "dormant" and r2["evidence_count"] == 0
