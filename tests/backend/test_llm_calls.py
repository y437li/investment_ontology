"""Hermetic tests for Issue #29 — per-task model resolution + llm_calls.parquet.

No network: a fake OpenAI-compatible client is injected. Covers:
  1. config.model_for precedence (task > default > env > None).
  2. A fake completion with usage lands exactly one row in llm_calls.parquet,
     and manifest.model_config_resolved records the resolved extraction model.
  3. Source-scan: no hardcoded provider model literal at any wired call site.
  4. The DuckDB view v_disc_llm_calls registers and returns the row.
"""

from __future__ import annotations

import re
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from theme_engine import config, db, extraction, runs
from theme_engine.extraction import OpenAIExtractor, run_extraction
from theme_engine.models import RunCreateRequest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_THEME_ENGINE = _REPO_ROOT / "app" / "backend" / "theme_engine"


# ---------------------------------------------------------------------------
# 1. Resolver precedence
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "block, env, expected",
    [
        ({"extraction": "task-model", "default": "default-model"}, "env-model", "task-model"),
        ({"default": "default-model"}, "env-model", "default-model"),
        ({}, "env-model", "env-model"),
        ({"extraction": "", "default": ""}, "env-model", "env-model"),
        ({}, None, None),
    ],
)
def test_model_for_precedence(monkeypatch, block, env, expected):
    monkeypatch.setattr(config, "_load_llm_models", lambda: block)
    if env is None:
        monkeypatch.delenv("LLM_MODEL_NAME", raising=False)
    else:
        monkeypatch.setenv("LLM_MODEL_NAME", env)
    assert config.model_for("extraction") == expected


# ---------------------------------------------------------------------------
# Fake OpenAI-compatible client (no network)
# ---------------------------------------------------------------------------


class _FakeUsage:
    prompt_tokens = 5
    completion_tokens = 10
    total_tokens = 15
    prompt_tokens_details = None


class _FakeFunction:
    arguments = '{"entities": [], "edges": []}'


class _FakeToolCall:
    function = _FakeFunction()


class _FakeMessage:
    tool_calls = [_FakeToolCall()]


class _FakeChoice:
    message = _FakeMessage()


class _FakeResponse:
    choices = [_FakeChoice()]
    usage = _FakeUsage()


class _FakeCompletions:
    def create(self, **kwargs):
        return _FakeResponse()


class _FakeChat:
    completions = _FakeCompletions()


class _FakeClient:
    chat = _FakeChat()


def _make_run_with_one_chunk() -> str:
    manifest = runs.create_run(RunCreateRequest(as_of_date="2024-06-30"))
    run_id = manifest.run_id
    discovery = runs.get_run_dir(run_id) / "discovery"
    discovery.mkdir(parents=True, exist_ok=True)
    table = pa.table({
        "chunk_id": pa.array(["c1"], type=pa.string()),
        "text": pa.array(["Suncor Energy is exposed to oil prices."], type=pa.string()),
        "available_at": pa.array(["2024-01-15"], type=pa.string()),
        "document_id": pa.array(["doc1"], type=pa.string()),
    })
    pq.write_table(table, discovery / "chunks.parquet")
    return run_id


# ---------------------------------------------------------------------------
# 2. Fake-client usage row lands in parquet + manifest records the model
# ---------------------------------------------------------------------------


def test_usage_row_and_manifest(tmp_path):
    run_id = _make_run_with_one_chunk()
    resolved_model = "fake-resolved-model"
    extractor = OpenAIExtractor(
        api_key="k", base_url="https://example/v1", llm_model_name=resolved_model
    )
    extractor._client = _FakeClient()  # bypass real OpenAI client construction

    run_extraction(run_id, extractor=extractor)

    calls_path = runs.get_run_dir(run_id) / "discovery" / "llm_calls.parquet"
    assert calls_path.exists()
    rows = pq.read_table(calls_path).to_pylist()
    assert len(rows) == 1
    row = rows[0]
    assert row["prompt_tokens"] == 5
    assert row["completion_tokens"] == 10
    assert row["total_tokens"] == 15
    assert row["task"] == "extraction"
    assert row["model"] == resolved_model
    assert row["run_id"] == run_id
    assert row["cache_hit"] is False
    assert row["schema_version"] == extraction.SCHEMA_VERSION

    manifest = runs.load_manifest(run_id)
    assert manifest.model_config_resolved == {"extraction": resolved_model}


def test_rule_based_writes_empty_llm_calls():
    """Rule-based (no-LLM) runs still write an empty, schema-stable file."""
    run_id = _make_run_with_one_chunk()
    run_extraction(run_id, extractor=extraction.RuleBasedExtractor())
    calls_path = runs.get_run_dir(run_id) / "discovery" / "llm_calls.parquet"
    assert calls_path.exists()
    tbl = pq.read_table(calls_path)
    assert tbl.num_rows == 0
    assert tbl.schema.names == extraction.LLM_CALLS_COLUMNS
    # int/bool columns must be typed even when empty
    assert tbl.schema.field("prompt_tokens").type == pa.int64()
    assert tbl.schema.field("cache_hit").type == pa.bool_()


# ---------------------------------------------------------------------------
# 3. Source-scan: no hardcoded provider model literal at wired call sites
# ---------------------------------------------------------------------------


def test_no_hardcoded_model_literals():
    pat = re.compile(r"model\s*=\s*['\"](?:gpt|claude|minimax|gemini)", re.IGNORECASE)
    for name in (
        "extraction.py",
        "reasoning.py",
        "concept_resolution.py",
        "theme_hierarchy.py",
        "node_explanation.py",
    ):
        text = (_THEME_ENGINE / name).read_text(encoding="utf-8")
        assert not pat.search(text), f"hardcoded model literal in {name}"


# ---------------------------------------------------------------------------
# 4. DuckDB view registers and returns the row
# ---------------------------------------------------------------------------


def test_duckdb_view_registers(tmp_path):
    run_id = _make_run_with_one_chunk()
    extractor = OpenAIExtractor(
        api_key="k", base_url="https://example/v1", llm_model_name="m1"
    )
    extractor._client = _FakeClient()
    run_extraction(run_id, extractor=extractor)

    # Stage the artifact under a directory literally named "runs" so the view's
    # path-derived run_id regex (.../runs/<run_id>/discovery/) resolves.
    import shutil

    base = tmp_path / "runs"
    dest = base / run_id / "discovery"
    dest.mkdir(parents=True, exist_ok=True)
    shutil.copy(
        runs.get_run_dir(run_id) / "discovery" / "llm_calls.parquet",
        dest / "llm_calls.parquet",
    )

    with db.open_run(run_id, base_dir=base) as conn:
        names = {r[0] for r in conn.execute("SHOW TABLES").fetchall()}
        assert "v_disc_llm_calls" in names
        rows = conn.execute(
            "SELECT run_id, model, total_tokens FROM v_disc_llm_calls"
        ).fetchall()
    assert len(rows) == 1
    assert rows[0][0] == run_id  # path-derived run_id
    assert rows[0][1] == "m1"
    assert rows[0][2] == 15
