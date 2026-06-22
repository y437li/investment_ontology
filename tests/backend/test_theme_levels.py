"""Tests for factor-level tagging + the substantive-theme filter."""

import json
import sys
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

BACKEND = Path(__file__).resolve().parents[2] / "app" / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from theme_engine import theme_levels, runs  # noqa: E402
from theme_engine.config import settings  # noqa: E402
from theme_engine.models import RunCreateRequest  # noqa: E402


def _seed() -> str:
    run = runs.create_run(RunCreateRequest(as_of_date="2024-06-30"))
    d = Path(settings.run_output_dir) / run.run_id / "discovery"
    d.mkdir(parents=True, exist_ok=True)
    # macro driver, a company, an idiosyncratic event
    pq.write_table(pa.table({
        "entity_id": ["m1", "co1", "ev1", "frag1"],
        "entity_type": ["Commodity", "Company", "Event", "Event"],
    }), d / "entities.parquet")
    (d / "communities.json").write_text(json.dumps({"communities": [
        {"community_id": "c_big", "node_ids": ["m1", "co1", "ev1"], "size": 3},
        {"community_id": "c_frag", "node_ids": ["frag1"], "size": 1},
    ]}))
    pq.write_table(pa.table({
        "community_id": ["c_big", "c_frag"],
        "strength": [0.8, 0.0],
        "theme_snapshot_id": ["s1", "s2"],
    }), d / "theme_metrics.parquet")
    return run.run_id


def test_level_composition_and_dominant():
    rid = _seed()
    doc = theme_levels.compute_levels(rid)
    big = next(t for t in doc["themes"] if t["community_id"] == "c_big")
    assert big["level_counts"].get("macro") == 1     # Commodity -> macro
    assert big["level_counts"].get("company") == 1   # Company -> company
    assert big["level_counts"].get("idiosyncratic") == 1  # Event -> idiosyncratic
    assert big["dominant_level"] in ("macro", "company", "idiosyncratic")
    assert "macro" in doc["factor_levels"]


def test_substantive_flag_and_filter():
    rid = _seed()
    doc = theme_levels.compute_levels(rid)
    flags = {t["community_id"]: t["substantive"] for t in doc["themes"]}
    assert flags["c_big"] is True                    # size>=3 and strength>0
    assert flags["c_frag"] is False                  # size 1, strength 0 -> noise
    assert theme_levels.substantive_ids(rid) == {"c_big"}
