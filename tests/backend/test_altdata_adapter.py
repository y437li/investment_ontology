"""Hermetic tests for the alt/structured-data adapter framework.

PIT correctness (release lag / no leakage), regime computation (threshold &
zscore, PIT-only), typed node + structural edge emission, guarded edges, and
loud/honest validation. No network; tiny CSVs + a seeded run under tmp_path.
"""

import sys
from datetime import date
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

BACKEND = Path(__file__).resolve().parents[2] / "app" / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from theme_engine import altdata_adapter, runs  # noqa: E402
from theme_engine.config import settings  # noqa: E402
from theme_engine.models import RunCreateRequest  # noqa: E402

_POWER_RISING = ("date,US_TOTAL,ERCOT\n"
                 "2024-01-01,3400,820\n2024-02-01,3380,810\n2024-03-01,3450,840\n"
                 "2024-04-01,3500,860\n2024-05-01,3620,900\n2024-06-01,3700,930\n")
_POWER_FALLING = ("date,US_TOTAL,ERCOT\n"
                  "2024-01-01,3700,930\n2024-02-01,3680,920\n2024-03-01,3600,900\n"
                  "2024-04-01,3500,860\n2024-05-01,3400,820\n2024-06-01,3300,800\n")
_DATACENTER = ("observation_date,CAP_GW\n"
               "2024-01-01,8.0\n2024-02-01,8.6\n2024-03-01,9.2\n"
               "2024-04-01,10.5\n2024-05-01,11.8\n2024-06-01,13.0\n")

_ONTOLOGY = (
    "entity_types:\n"
    "  Company: {keep: true}\n"
    "  MacroIndicator: {keep: true}\n"
    "  Commodity: {keep: true}\n"
    "  EconomicConcept: {keep: true}\n"
)


def _seed_run(tmp_path, sources_yaml, companies_yaml, monkeypatch, as_of="2024-06-30"):
    """Write configs/{altdata,ontology,uni}.yml under tmp_path, monkeypatch
    CONFIG_DIR/UNIVERSE_CONFIG, create a run, seed discovery parquets."""
    cfg = tmp_path / "configs"
    cfg.mkdir(exist_ok=True)
    (cfg / "altdata.yml").write_text(sources_yaml)
    (cfg / "ontology.yml").write_text(_ONTOLOGY)
    (cfg / "uni.yml").write_text(companies_yaml)
    monkeypatch.setenv("CONFIG_DIR", str(cfg))
    monkeypatch.setenv("UNIVERSE_CONFIG", str(cfg / "uni.yml"))

    run = runs.create_run(RunCreateRequest(as_of_date=as_of))
    d = Path(settings.run_output_dir) / run.run_id / "discovery"
    d.mkdir(parents=True, exist_ok=True)
    return run, d


def _seed_entities(d, companies):
    """companies: list of (entity_id, name, sector_ignored)."""
    n = len(companies)
    pq.write_table(pa.table({
        "schema_version": [1] * n,
        "entity_id": [c[0] for c in companies],
        "entity_type": ["Company"] * n,
        "name": [c[1] for c in companies],
        "canonical_name": [c[1] for c in companies],
        "ticker": [None] * n,
    }), d / "entities.parquet")
    pq.write_table(pa.table({
        "schema_version": pa.array([], pa.int64()), "edge_id": pa.array([], pa.string()),
        "source_entity_id": pa.array([], pa.string()), "target_entity_id": pa.array([], pa.string()),
        "edge_type": pa.array([], pa.string()), "confidence": pa.array([], pa.float64()),
        "evidence_chunk_ids": pa.array([], pa.list_(pa.string())),
        "first_seen_at": pa.array([], pa.string()), "last_seen_at": pa.array([], pa.string()),
        "as_of_date": pa.array([], pa.string()), "extraction_method": pa.array([], pa.string()),
        "review_status": pa.array([], pa.string())}), d / "edges.parquet")
    pq.write_table(pa.table({
        "schema_version": pa.array([], pa.int64()), "edge_id": pa.array([], pa.string()),
        "explanation": pa.array([], pa.string()),
        "evidence_chunk_ids": pa.array([], pa.list_(pa.string())),
        "confidence": pa.array([], pa.float64()), "generated_by": pa.array([], pa.string()),
        "created_at": pa.array([], pa.string())}), d / "edge_explanations.parquet")


# ---------------------------------------------------------------------------
def test_pit_signal_respects_release_lag_and_trend(tmp_path):
    csv = tmp_path / "power.csv"
    csv.write_text(_POWER_RISING)
    spec = {"csv": str(csv), "reader": "wide_table", "date_col": "date", "series_col": "US_TOTAL"}
    snap = altdata_adapter.pit_signal(spec, date(2024, 6, 30), default_lag=35)
    assert snap["obs_date"] == "2024-05-01"   # June withheld by 35d lag = PIT
    assert snap["value"] == 3620
    assert snap["trend"] == "rising"


def test_pit_signal_zscore_and_threshold_regime(tmp_path):
    # (a) zscore on rising power series
    pcsv = tmp_path / "power.csv"
    pcsv.write_text(_POWER_RISING)
    pspec = {"csv": str(pcsv), "reader": "wide_table", "date_col": "date",
             "series_col": "US_TOTAL", "regime": {"mode": "zscore", "window_days": 365}}
    psnap = altdata_adapter.pit_signal(pspec, date(2024, 6, 30), default_lag=35)
    assert psnap["regime"] is not None
    assert psnap["regime"] in ("elevated", "normal")

    # (b) threshold on datacenter series. PIT obs at as_of 2024-06-30 with lag 45
    # (cutoff 2024-05-16) is the 2024-05-01 value 11.8 -> regime 'scaling'
    # (11.8 > 10, <= 25), computed from PIT-available data only.
    dcsv = tmp_path / "dc.csv"
    dcsv.write_text(_DATACENTER)
    dspec = {"csv": str(dcsv), "reader": "fred_csv", "date_col": "observation_date",
             "value_col": "CAP_GW", "lag_days": 45,
             "regime": {"mode": "threshold", "thresholds": [10, 25],
                        "labels": ["early", "scaling", "hyperscale"]}}
    dsnap = altdata_adapter.pit_signal(dspec, date(2024, 6, 30), default_lag=35)
    assert dsnap["obs_date"] == "2024-05-01"
    assert dsnap["value"] == 11.8
    assert dsnap["regime"] == "scaling"   # 11.8 > 10, <= 25


def test_integrate_altdata_emits_econconcept_node_and_structural_edge(tmp_path, monkeypatch):
    dcsv = tmp_path / "dc.csv"
    dcsv.write_text(_DATACENTER)
    sources = (
        "version: 1\nrelease_lag_days: 35\nsources:\n"
        "  - id: datacenter_capacity\n    label: Datacenter Capacity Buildout\n"
        "    node_type: EconomicConcept\n    source_class: datacenter\n    unit: \"GW\"\n"
        f"    reader: fred_csv\n    csv: {dcsv}\n"
        "    date_col: observation_date\n    value_col: CAP_GW\n    lag_days: 45\n"
        "    sensitivities:\n"
        "      - {sector: Utilities, edge_type: causes, rationale: datacenter buildout drives baseload power demand}\n"
    )
    companies = "companies:\n  - name: Fortis Inc\n    sector: Utilities\n"
    run, d = _seed_run(tmp_path, sources, companies, monkeypatch)
    _seed_entities(d, [("fortis", "Fortis Inc", "Utilities")])

    res = altdata_adapter.integrate_altdata(run.run_id)
    assert res == {"altdata_nodes": 1, "altdata_edges": 1}

    ents = pq.read_table(d / "entities.parquet").to_pylist()
    assert any(x["entity_type"] == "EconomicConcept" and x["name"] == "Datacenter Capacity Buildout"
               for x in ents)

    edges = pq.read_table(d / "edges.parquet").to_pylist()
    assert len(edges) == 1
    e = edges[0]
    assert e["edge_type"] == "causes"
    assert e["target_entity_id"] == "fortis"
    assert e["extraction_method"] == "metadata_inferred"
    assert e["review_status"] == "auto"

    expl = pq.read_table(d / "edge_explanations.parquet").to_pylist()
    assert len(expl) == 1
    assert expl[0]["generated_by"] == "altdata_adapter:datacenter_capacity"
    assert "point-in-time, vintage 2024-06-30" in expl[0]["explanation"]


def test_when_trend_guard_filters_edges(tmp_path, monkeypatch):
    def _sources(csv_path):
        return (
            "version: 1\nrelease_lag_days: 35\nsources:\n"
            "  - id: us_power_demand\n    label: US Electricity Demand\n"
            "    node_type: MacroIndicator\n    source_class: power\n    unit: \"GWh\"\n"
            f"    reader: wide_table\n    csv: {csv_path}\n"
            "    date_col: date\n    series_col: US_TOTAL\n    lag_days: 30\n"
            "    sensitivities:\n"
            "      - {sector: Utilities, edge_type: benefits, rationale: load growth}\n"
            "      - {sector: Energy, edge_type: benefits, when_trend: rising, rationale: gas burn}\n"
        )
    companies = ("companies:\n  - name: Fortis Inc\n    sector: Utilities\n"
                 "  - name: Suncor Energy\n    sector: Energy\n")

    # rising -> both edges
    pcsv = tmp_path / "power_rise.csv"
    pcsv.write_text(_POWER_RISING)
    run, d = _seed_run(tmp_path, _sources(pcsv), companies, monkeypatch)
    _seed_entities(d, [("fortis", "Fortis Inc", "Utilities"),
                       ("suncor", "Suncor Energy", "Energy")])
    res = altdata_adapter.integrate_altdata(run.run_id)
    assert res["altdata_edges"] == 2

    # falling -> only the unguarded Utilities edge fires
    fcsv = tmp_path / "power_fall.csv"
    fcsv.write_text(_POWER_FALLING)
    run2, d2 = _seed_run(tmp_path, _sources(fcsv), companies, monkeypatch)
    _seed_entities(d2, [("fortis", "Fortis Inc", "Utilities"),
                        ("suncor", "Suncor Energy", "Energy")])
    res2 = altdata_adapter.integrate_altdata(run2.run_id)
    assert res2["altdata_edges"] == 1
    edges = pq.read_table(d2 / "edges.parquet").to_pylist()
    assert edges[0]["target_entity_id"] == "fortis"


def test_invalid_edge_type_raises_and_unknown_node_type_skipped(tmp_path, monkeypatch):
    dcsv = tmp_path / "dc.csv"
    dcsv.write_text(_DATACENTER)

    # (a) non-structural edge_type -> ValueError mentioning the structural set
    bad = (
        "version: 1\nrelease_lag_days: 35\nsources:\n"
        "  - id: bad_edge\n    label: Bad Edge Source\n    node_type: MacroIndicator\n"
        f"    reader: fred_csv\n    csv: {dcsv}\n"
        "    date_col: observation_date\n    value_col: CAP_GW\n    lag_days: 45\n"
        "    sensitivities:\n"
        "      - {sector: Utilities, edge_type: mentioned_in, rationale: not structural}\n"
    )
    companies = "companies:\n  - name: Fortis Inc\n    sector: Utilities\n"
    run, d = _seed_run(tmp_path, bad, companies, monkeypatch)
    _seed_entities(d, [("fortis", "Fortis Inc", "Utilities")])
    with pytest.raises(ValueError) as exc:
        altdata_adapter.integrate_altdata(run.run_id)
    assert "structural" in str(exc.value)

    # (b) unknown node_type -> honest skip, no crash, zero nodes/edges
    bogus = (
        "version: 1\nrelease_lag_days: 35\nsources:\n"
        "  - id: bogus_node\n    label: Bogus Source\n    node_type: Bogus\n"
        f"    reader: fred_csv\n    csv: {dcsv}\n"
        "    date_col: observation_date\n    value_col: CAP_GW\n    lag_days: 45\n"
        "    sensitivities:\n"
        "      - {sector: Utilities, edge_type: causes, rationale: skipped}\n"
    )
    run2, d2 = _seed_run(tmp_path, bogus, companies, monkeypatch)
    _seed_entities(d2, [("fortis", "Fortis Inc", "Utilities")])
    res = altdata_adapter.integrate_altdata(run2.run_id)
    assert res == {"altdata_nodes": 0, "altdata_edges": 0}
