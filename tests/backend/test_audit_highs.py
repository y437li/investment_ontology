"""Regression tests for the two deferred audit HIGHs (#75 alias direction, #76 fail-loud)."""
import sys
from pathlib import Path
import pyarrow as pa, pyarrow.parquet as pq
import pytest
BACKEND = Path(__file__).resolve().parents[2] / "app" / "backend"
if str(BACKEND) not in sys.path: sys.path.insert(0, str(BACKEND))
from theme_engine import entity_resolution, validation, runs
from theme_engine.config import settings
from theme_engine.models import RunCreateRequest


def test_abbreviation_aliases_emitted_for_long_canonical():
    """#75: an entity whose canonical_name is the LONG form (as extraction produces)
    still gets its short aliases emitted (direction-agnostic match)."""
    run = runs.create_run(RunCreateRequest(as_of_date="2024-06-30"))
    d = Path(settings.run_output_dir) / run.run_id / "discovery"; d.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.table({
        "schema_version": [1], "entity_id": ["e1"], "entity_type": ["Company"],
        "name": ["Royal Bank of Canada"], "canonical_name": ["Royal Bank of Canada"],
        "ticker": [None], "first_seen_at": ["2024-01-01"], "as_of_date": ["2024-06-30"],
        "source_chunk_ids": [["c1"]],
    }), d / "entities.parquet")
    pq.write_table(pa.table({"chunk_id": ["c1"], "available_at": ["2024-01-01"]}), d / "chunks.parquet")
    entity_resolution.resolve_entities(run.run_id)
    aliases = {a["alias"] for a in pq.read_table(d / "entity_aliases.parquet").to_pylist()}
    assert "RBC" in aliases   # long canonical -> short alias still emitted (was the bug)


def test_validation_loader_fails_loud_on_corrupt_but_empty_on_absent():
    """#76: absent frozen artifact -> empty; present-but-corrupt -> raise (not false-clean)."""
    run = runs.create_run(RunCreateRequest(as_of_date="2024-06-30"))
    d = Path(settings.run_output_dir) / run.run_id / "discovery"; d.mkdir(parents=True, exist_ok=True)
    assert validation._load_communities(run.run_id) == {}          # absent -> empty
    (d / "communities.json").write_text("not json {", encoding="utf-8")
    with pytest.raises(ValueError):                                # corrupt -> raises
        validation._load_communities(run.run_id)
