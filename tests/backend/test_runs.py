"""Milestone 1 acceptance: a run can be created with a valid manifest."""

import csv
import json
from pathlib import Path

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
    assert s["validation_status"] is None
    assert s["validation_artifacts"] == []
    assert s["artifacts_present"] == []  # only the manifest exists at M1


def test_get_artifact_manifest_and_discovery_file():
    created = client.post("/api/runs/create", json={"as_of_date": "2024-03-31"}).json()
    run_id = created["run_id"]
    run_dir = Path(settings.run_output_dir) / run_id
    (run_dir / "discovery" / "raw_documents.parquet").write_text(
        "seed",
        encoding="utf-8",
    )

    manifest_resp = client.get(f"/api/artifacts/{run_id}/run_manifest.json")
    assert manifest_resp.status_code == 200
    manifest = manifest_resp.json()
    assert manifest["run_id"] == run_id

    artifact_resp = client.get(f"/api/artifacts/{run_id}/discovery/raw_documents.parquet")
    assert artifact_resp.status_code == 200
    assert artifact_resp.text == "seed"


def test_get_artifact_rejects_missing_and_traversal():
    created = client.post("/api/runs/create", json={"as_of_date": "2024-03-31"}).json()
    run_id = created["run_id"]

    not_found = client.get(f"/api/artifacts/{run_id}/discovery/does_not_exist.parquet")
    assert not_found.status_code == 404

    traversal = client.get(f"/api/artifacts/{run_id}/../run_manifest.json")
    assert traversal.status_code == 404


def test_invalid_as_of_date_is_rejected():
    resp = client.post("/api/runs/create", json={"as_of_date": "2024/06/30"})
    assert resp.status_code == 422


def test_missing_run_returns_404():
    resp = client.get("/api/runs/run_does_not_exist/status")
    assert resp.status_code == 404


def _seed_discovery_artifacts(run_id: str) -> None:
    run_dir = Path(settings.run_output_dir) / run_id / "discovery"
    run_dir.mkdir(parents=True, exist_ok=True)
    for name in [
        "raw_documents.parquet",
        "documents.parquet",
        "document_cleaning_log.parquet",
        "chunks.parquet",
        "entities.parquet",
        "entity_aliases.parquet",
        "edges.parquet",
        "graph.json",
    ]:
        (run_dir / name).write_text("seed", encoding="utf-8")


def _seed_market_input(
    market_dir: Path,
    *,
    as_of_date: str = "2024-06-30",
    price_date: str = "2024-12-31",
) -> None:
    market_dir.mkdir(parents=True, exist_ok=True)
    (market_dir / "prices.csv").write_text(
        "company_id,ticker,price_date,close,adjusted_close,available_at,currency,source,source_id\n"
        f"DEMO_COMPANY_A,DEMO,{price_date},100.5,100.4,{as_of_date},USD,stub,price-feed\n"
        f"DEMO_COMPANY_B,DMOB,{price_date},101.2,101.0,{as_of_date},USD,stub,price-feed\n",
        encoding="utf-8",
    )


def _seed_fundamentals_input(
    fundamentals_dir: Path,
    *,
    as_of_date: str = "2024-06-30",
) -> None:
    fundamentals_dir.mkdir(parents=True, exist_ok=True)
    (fundamentals_dir / "fundamentals.csv").write_text(
        "company_id,ticker,period_end,metric_name,metric_value,unit,currency,filing_date,available_at,source,source_id\n"
        f"DEMO_COMPANY_A,DEMO,{as_of_date},revenue_growth,1.2,pct,USD,{as_of_date},{as_of_date},stub,fund-stub\n"
        f"DEMO_COMPANY_B,DMOB,{as_of_date},eps_revision,0.8,pct,USD,{as_of_date},{as_of_date},stub,fund-stub\n",
        encoding="utf-8",
    )


def _seed_fundamentals_input_with_missing_available_at(
    fundamentals_dir: Path,
    *,
    as_of_date: str = "2024-06-30",
) -> None:
    fundamentals_dir.mkdir(parents=True, exist_ok=True)
    (fundamentals_dir / "fundamentals.csv").write_text(
        "company_id,ticker,period_end,metric_name,metric_value,unit,currency,filing_date,available_at,source,source_id\n"
        f"DEMO_COMPANY_A,DEMO,{as_of_date},revenue_growth,1.2,pct,USD,{as_of_date},,stub,fund-stub\n"
        f"DEMO_COMPANY_B,DMOB,{as_of_date},eps_revision,0.8,pct,USD,{as_of_date},,stub,fund-stub\n",
        encoding="utf-8",
    )


def _seed_market_input_with_custom_columns(
    market_dir: Path,
    *,
    as_of_date: str = "2024-06-30",
    price_date: str = "2024-12-31",
) -> None:
    market_dir.mkdir(parents=True, exist_ok=True)
    (market_dir / "prices.csv").write_text(
        "cid,trading_day,px,published,source_name\n"
        f"DEMO_COMPANY_A,{price_date},100.5,{as_of_date},stub\n"
        f"DEMO_COMPANY_B,{price_date},101.2,{as_of_date},stub\n",
        encoding="utf-8",
    )


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


def test_validation_run_preflight_after_freeze(tmp_path):
    run_id = client.post("/api/runs/create", json={"as_of_date": "2024-06-30"}).json()[
        "run_id"
    ]
    _seed_discovery_artifacts(run_id)
    market_dir = tmp_path / "market"
    _seed_market_input(market_dir)

    freeze_resp = client.post("/api/discovery/freeze", json={"run_id": run_id})
    assert freeze_resp.status_code == 200

    resp = client.post(
        "/api/validation/run",
        json={"run_id": run_id, "market_data_dir": str(market_dir)},
    )
    assert resp.status_code == 200
    assert resp.json()["success"] is True
    assert resp.json()["validation_status"] == "disabled_not_enough_snapshots"
    assert resp.json()["message"] == "validation pipeline completed"
    assert resp.json()["validated_themes"] >= 1
    assert sorted(resp.json()["artifacts"]) == [
        "validation/fundamentals.parquet",
        "validation/market_prices.parquet",
        "validation/portfolio_baskets.parquet",
        "validation/validation.csv",
    ]

    run_dir = Path(settings.run_output_dir) / run_id
    for artifact in resp.json()["artifacts"]:
        assert (run_dir / artifact).exists()

    with open(run_dir / "validation/validation.csv", encoding="utf-8", newline="") as fp:
        rows = list(csv.DictReader(fp))
    assert len(rows) >= 1
    assert (
        rows[0]["caveats"]
        == "backtesting requires temporal panel and is not meaningful for single-snapshot inputs."
    )
    assert rows[0]["validation_status"] == "disabled_not_enough_snapshots"


def test_run_status_includes_validation_context_after_validation(tmp_path):
    run_id = client.post("/api/runs/create", json={"as_of_date": "2024-06-30"}).json()[
        "run_id"
    ]
    _seed_discovery_artifacts(run_id)
    market_dir = tmp_path / "market"
    _seed_market_input(market_dir)
    freeze_resp = client.post("/api/discovery/freeze", json={"run_id": run_id})
    assert freeze_resp.status_code == 200

    validation_resp = client.post(
        "/api/validation/run",
        json={"run_id": run_id, "market_data_dir": str(market_dir)},
    )
    assert validation_resp.status_code == 200
    expected = sorted(validation_resp.json()["artifacts"])

    status_resp = client.get(f"/api/runs/{run_id}/status")
    assert status_resp.status_code == 200
    status = status_resp.json()
    assert status["validation_status"] == "disabled_not_enough_snapshots"
    assert sorted(status["validation_artifacts"]) == expected


def test_validation_rejects_without_market_data_dir():
    run_id = client.post("/api/runs/create", json={"as_of_date": "2024-06-30"}).json()[
        "run_id"
    ]
    _seed_discovery_artifacts(run_id)

    freeze_resp = client.post("/api/discovery/freeze", json={"run_id": run_id})
    assert freeze_resp.status_code == 200

    resp = client.post("/api/validation/run", json={"run_id": run_id})
    assert resp.status_code == 409
    assert "market_data_dir is required for validation" in resp.text


def test_validation_rejects_fundamentals_without_fundamentals_dir(tmp_path):
    run_id = client.post("/api/runs/create", json={"as_of_date": "2024-06-30"}).json()[
        "run_id"
    ]
    _seed_discovery_artifacts(run_id)

    freeze_resp = client.post("/api/discovery/freeze", json={"run_id": run_id})
    assert freeze_resp.status_code == 200

    market_dir = tmp_path / "market"
    _seed_market_input(market_dir)

    resp = client.post(
        "/api/validation/run",
        json={
            "run_id": run_id,
            "market_data_dir": str(market_dir),
            "include_fundamentals": True,
        },
    )
    assert resp.status_code == 409
    assert "include_fundamentals is true but fundamentals_data_dir is not provided" in resp.text


def test_validation_loads_fundamentals_data(tmp_path):
    run_id = client.post("/api/runs/create", json={"as_of_date": "2024-06-30"}).json()[
        "run_id"
    ]
    _seed_discovery_artifacts(run_id)

    freeze_resp = client.post("/api/discovery/freeze", json={"run_id": run_id})
    assert freeze_resp.status_code == 200

    market_dir = tmp_path / "market"
    fundamentals_dir = tmp_path / "fundamentals"
    _seed_market_input(market_dir, as_of_date="2024-06-30")
    _seed_fundamentals_input(fundamentals_dir, as_of_date="2024-06-30")

    resp = client.post(
        "/api/validation/run",
        json={
            "run_id": run_id,
            "market_data_dir": str(market_dir),
            "fundamentals_data_dir": str(fundamentals_dir),
            "include_fundamentals": True,
        },
    )
    assert resp.status_code == 200
    assert resp.json()["success"] is True

    fundamentals = pq.read_table(
        Path(settings.run_output_dir) / run_id / "validation/fundamentals.parquet"
    )
    assert fundamentals.num_rows == 2
    assert set(fundamentals["metric_name"].to_pylist()) == {"revenue_growth", "eps_revision"}


def test_validation_rejects_fundamentals_with_missing_available_at(tmp_path):
    run_id = client.post("/api/runs/create", json={"as_of_date": "2024-06-30"}).json()[
        "run_id"
    ]
    _seed_discovery_artifacts(run_id)

    freeze_resp = client.post("/api/discovery/freeze", json={"run_id": run_id})
    assert freeze_resp.status_code == 200

    market_dir = tmp_path / "market"
    fundamentals_dir = tmp_path / "fundamentals"
    _seed_market_input(market_dir, as_of_date="2024-06-30")
    _seed_fundamentals_input_with_missing_available_at(fundamentals_dir, as_of_date="2024-06-30")

    resp = client.post(
        "/api/validation/run",
        json={
            "run_id": run_id,
            "market_data_dir": str(market_dir),
            "fundamentals_data_dir": str(fundamentals_dir),
            "include_fundamentals": True,
        },
    )
    assert resp.status_code == 409
    assert "missing available_at for fundamentals" in resp.text


def test_validation_filters_fundamentals_by_optional_metric_list(tmp_path):
    run_id = client.post(
        "/api/runs/create",
        json={
            "as_of_date": "2024-06-30",
            "validation_config": str(
                tmp_path / "validation.custom.yml"
            ),
        },
    ).json()["run_id"]
    _seed_discovery_artifacts(run_id)

    freeze_resp = client.post("/api/discovery/freeze", json={"run_id": run_id})
    assert freeze_resp.status_code == 200

    config_path = Path(tmp_path / "validation.custom.yml")
    config_path.write_text(
        """
optional_fundamentals:
  - revenue_growth
""",
        encoding="utf-8",
    )

    market_dir = tmp_path / "market"
    fundamentals_dir = tmp_path / "fundamentals"
    _seed_market_input(market_dir, as_of_date="2024-06-30")
    _seed_fundamentals_input(fundamentals_dir, as_of_date="2024-06-30")

    resp = client.post(
        "/api/validation/run",
        json={
            "run_id": run_id,
            "market_data_dir": str(market_dir),
            "fundamentals_data_dir": str(fundamentals_dir),
            "include_fundamentals": True,
        },
    )
    assert resp.status_code == 200

    fundamentals = pq.read_table(
        Path(settings.run_output_dir) / run_id / "validation/fundamentals.parquet"
    )
    assert fundamentals.num_rows == 1
    assert set(fundamentals["metric_name"].to_pylist()) == {"revenue_growth"}


def test_validation_loads_market_data_from_directory(tmp_path):
    run_id = client.post("/api/runs/create", json={"as_of_date": "2024-06-30"}).json()[
        "run_id"
    ]
    _seed_discovery_artifacts(run_id)

    freeze_resp = client.post("/api/discovery/freeze", json={"run_id": run_id})
    assert freeze_resp.status_code == 200

    market_dir = tmp_path / "market"
    _seed_market_input(market_dir, as_of_date="2024-06-30")

    resp = client.post(
        "/api/validation/run",
        json={
            "run_id": run_id,
            "market_data_dir": str(market_dir),
            "include_fundamentals": False,
        },
    )
    assert resp.status_code == 200
    assert resp.json()["success"] is True

    market_table = pq.read_table(Path(settings.run_output_dir) / run_id / "validation/market_prices.parquet")
    assert market_table.num_rows == 2


def test_validation_supports_custom_market_field_mappings(tmp_path):
    run_id = client.post(
        "/api/runs/create",
        json={
            "as_of_date": "2024-06-30",
            "validation_config": str(
                tmp_path / "validation.custom.yml"
            ),
        },
    ).json()["run_id"]
    _seed_discovery_artifacts(run_id)

    freeze_resp = client.post("/api/discovery/freeze", json={"run_id": run_id})
    assert freeze_resp.status_code == 200

    config_path = Path(tmp_path / "validation.custom.yml")
    config_path.write_text(
        """
field_mappings:
  market:
    company_id:
      - cid
    price_date:
      - trading_day
    close:
      - px
    available_at:
      - published
""",
        encoding="utf-8",
    )

    market_dir = tmp_path / "market"
    _seed_market_input_with_custom_columns(market_dir, as_of_date="2024-06-30")

    resp = client.post(
        "/api/validation/run",
        json={
            "run_id": run_id,
            "market_data_dir": str(market_dir),
            "include_fundamentals": False,
        },
    )
    assert resp.status_code == 200
    assert resp.json()["success"] is True

    market_table = pq.read_table(Path(settings.run_output_dir) / run_id / "validation/market_prices.parquet")
    assert market_table.num_rows == 2
    assert set(market_table["company_id"].to_pylist()) == {
        "DEMO_COMPANY_A",
        "DEMO_COMPANY_B",
    }


def test_validation_rejects_unknown_market_field_mappings(tmp_path):
    run_id = client.post(
        "/api/runs/create",
        json={
            "as_of_date": "2024-06-30",
            "validation_config": str(
                tmp_path / "validation.invalid.yml"
            ),
        },
    ).json()["run_id"]
    _seed_discovery_artifacts(run_id)

    freeze_resp = client.post("/api/discovery/freeze", json={"run_id": run_id})
    assert freeze_resp.status_code == 200

    config_path = Path(tmp_path / "validation.invalid.yml")
    config_path.write_text(
        """
field_mappings:
  market:
    not_a_field:
      - bogus
""",
        encoding="utf-8",
    )

    market_dir = tmp_path / "market"
    _seed_market_input(market_dir, as_of_date="2024-06-30")

    resp = client.post(
        "/api/validation/run",
        json={
            "run_id": run_id,
            "market_data_dir": str(market_dir),
            "include_fundamentals": False,
        },
    )
    assert resp.status_code == 409
    assert "unknown market field mapping" in resp.text


def test_validation_applies_custom_forward_coverage_rules(tmp_path):
    run_id = client.post(
        "/api/runs/create",
        json={
            "as_of_date": "2024-06-30",
            "validation_config": str(
                tmp_path / "validation.custom.yml"
            ),
        },
    ).json()["run_id"]
    _seed_discovery_artifacts(run_id)

    freeze_resp = client.post("/api/discovery/freeze", json={"run_id": run_id})
    assert freeze_resp.status_code == 200

    config_path = Path(tmp_path / "validation.custom.yml")
    config_path.write_text(
        """
forward_windows:
  - 2M
  - 4M

forward_coverage_months:
  2M: 2
  4M: 4
""",
        encoding="utf-8",
    )

    market_dir = tmp_path / "market"
    _seed_market_input(market_dir, as_of_date="2024-06-30", price_date="2024-08-31")

    resp = client.post(
        "/api/validation/run",
        json={
            "run_id": run_id,
            "market_data_dir": str(market_dir),
            "include_fundamentals": False,
        },
    )
    assert resp.status_code == 409
    assert "forward-coverage violated" in resp.text


def test_validation_report_uses_validation_config_forward_windows(tmp_path):
    run_id = client.post(
        "/api/runs/create",
        json={
            "as_of_date": "2024-06-30",
            "validation_config": str(
                tmp_path / "validation.custom.yml"
            ),
        },
    ).json()["run_id"]
    _seed_discovery_artifacts(run_id)

    freeze_resp = client.post("/api/discovery/freeze", json={"run_id": run_id})
    assert freeze_resp.status_code == 200

    config_path = Path(tmp_path / "validation.custom.yml")
    config_path.write_text(
        """
forward_windows:
  - 6M
  - 12M
""",
        encoding="utf-8",
    )

    market_dir = tmp_path / "market"
    _seed_market_input(market_dir, as_of_date="2024-06-30")

    resp = client.post(
        "/api/validation/run",
        json={
            "run_id": run_id,
            "market_data_dir": str(market_dir),
            "include_fundamentals": False,
        },
    )
    assert resp.status_code == 200

    report_path = Path(settings.run_output_dir) / run_id / "validation/validation.csv"
    rows = list(csv.DictReader(report_path.open("r", encoding="utf-8")))
    assert sorted({r["forward_window"] for r in rows}) == ["12M", "6M"]


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
