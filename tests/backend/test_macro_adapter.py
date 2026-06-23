"""Tests for the macro-data adapter: point-in-time snapshot + graph integration."""

import sys
from datetime import date
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

BACKEND = Path(__file__).resolve().parents[2] / "app" / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from theme_engine import macro_adapter, runs  # noqa: E402
from theme_engine.config import settings  # noqa: E402
from theme_engine.models import RunCreateRequest  # noqa: E402


def _write_csv(p: Path):
    # monthly rate series rising into mid-2024
    p.write_text("observation_date,RATE\n2024-01-01,4.0\n2024-02-01,4.2\n"
                 "2024-03-01,4.5\n2024-04-01,5.0\n2024-05-01,5.3\n2024-06-01,5.5\n")


def test_pit_snapshot_respects_release_lag_and_trend(tmp_path):
    csv = tmp_path / "rate.csv"
    _write_csv(csv)
    spec = {"csv": str(csv), "date_col": "observation_date", "value_col": "RATE"}
    # as_of 2024-06-30 with 35-day lag -> latest available is the May obs, not June
    snap = macro_adapter.pit_snapshot(spec, date(2024, 6, 30), default_lag=35)
    assert snap["obs_date"] == "2024-05-01"     # June not yet released (PIT)
    assert snap["value"] == 5.3
    assert snap["trend"] == "rising"


def test_integrate_macro_links_rate_to_bank(tmp_path, monkeypatch):
    # temp config dir with a macro.yml + universe
    cfg = tmp_path / "configs"
    cfg.mkdir()
    csv = tmp_path / "rate.csv"
    _write_csv(csv)
    (cfg / "macro.yml").write_text(
        "version: 1\nrelease_lag_days: 35\nseries:\n"
        f"  - id: rate\n    label: Test Policy Rate\n    csv: {csv}\n"
        "    date_col: observation_date\n    value_col: RATE\n    unit: \"%\"\n"
        "    sensitivities:\n"
        "      - {sector: Financials, edge_type: benefits, rationale: higher rates widen margins}\n"
    )
    (cfg / "uni.yml").write_text(
        "companies:\n  - name: Royal Bank of Canada\n    sector: Financials\n")
    monkeypatch.setenv("CONFIG_DIR", str(cfg))
    monkeypatch.setenv("UNIVERSE_CONFIG", str(cfg / "uni.yml"))

    run = runs.create_run(RunCreateRequest(as_of_date="2024-06-30"))
    d = Path(settings.run_output_dir) / run.run_id / "discovery"
    d.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.table({"schema_version": [1], "entity_id": ["rbc"],
                             "entity_type": ["Company"], "name": ["Royal Bank of Canada"],
                             "canonical_name": ["Royal Bank of Canada"], "ticker": [None]}),
                   d / "entities.parquet")
    pq.write_table(pa.table({"schema_version": pa.array([], pa.int64()), "edge_id": pa.array([], pa.string()),
                             "source_entity_id": pa.array([], pa.string()), "target_entity_id": pa.array([], pa.string()),
                             "edge_type": pa.array([], pa.string()), "confidence": pa.array([], pa.float64()),
                             "evidence_chunk_ids": pa.array([], pa.list_(pa.string())),
                             "first_seen_at": pa.array([], pa.string()), "last_seen_at": pa.array([], pa.string()),
                             "as_of_date": pa.array([], pa.string()), "extraction_method": pa.array([], pa.string()),
                             "review_status": pa.array([], pa.string())}), d / "edges.parquet")
    pq.write_table(pa.table({"schema_version": pa.array([], pa.int64()), "edge_id": pa.array([], pa.string()),
                             "explanation": pa.array([], pa.string()),
                             "evidence_chunk_ids": pa.array([], pa.list_(pa.string())),
                             "confidence": pa.array([], pa.float64()), "generated_by": pa.array([], pa.string()),
                             "created_at": pa.array([], pa.string())}), d / "edge_explanations.parquet")

    res = macro_adapter.integrate_macro(run.run_id)
    assert res == {"macro_nodes": 1, "macro_edges": 1}
    edges = pq.read_table(d / "edges.parquet").to_pylist()
    assert len(edges) == 1
    e = edges[0]
    assert e["target_entity_id"] == "rbc" and e["edge_type"] == "benefits"
    assert e["extraction_method"] == "metadata_inferred"      # labeled, not document_stated
    ents = pq.read_table(d / "entities.parquet").to_pylist()
    assert any(x["entity_type"] == "MacroIndicator" and x["name"] == "Test Policy Rate" for x in ents)


def test_macro_rejects_non_structural_edge_type(tmp_path, monkeypatch):
    """Audit medium: a macro.yml edge_type that is not structural raises (would
    otherwise be silently dropped out of community discovery)."""
    import pytest
    cfg = tmp_path / "configs"; cfg.mkdir()
    csv = tmp_path / "rate.csv"; _write_csv(csv)
    (cfg / "macro.yml").write_text(
        "version: 1\nrelease_lag_days: 35\nseries:\n"
        f"  - id: rate\n    label: Test Rate\n    csv: {csv}\n"
        "    date_col: observation_date\n    value_col: RATE\n    unit: \"%\"\n"
        "    sensitivities:\n      - {sector: Financials, edge_type: mentioned_in, rationale: bad}\n")
    (cfg / "uni.yml").write_text("companies:\n  - name: Royal Bank of Canada\n    sector: Financials\n")
    monkeypatch.setenv("CONFIG_DIR", str(cfg))
    monkeypatch.setenv("UNIVERSE_CONFIG", str(cfg / "uni.yml"))
    run = runs.create_run(RunCreateRequest(as_of_date="2024-06-30"))
    d = Path(settings.run_output_dir) / run.run_id / "discovery"; d.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.table({"schema_version": [1], "entity_id": ["rbc"], "entity_type": ["Company"],
                             "name": ["Royal Bank of Canada"], "canonical_name": ["Royal Bank of Canada"],
                             "ticker": [None]}), d / "entities.parquet")
    with pytest.raises(ValueError):
        macro_adapter.integrate_macro(run.run_id)
