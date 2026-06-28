"""#110: Evidence-backed direction field for causes/exposed_to/sensitive_to edges.

Tests that extraction emits the correct direction (+1/-1/0) from text evidence,
that graph_build sets effective polarity from direction, and that backward
compatibility is maintained.

Acceptance criteria:
  (1) Extraction direction — rule-based:
      beneficial text -> +1, adverse text -> -1, ambiguous text -> 0.
  (2) Extraction direction — fake-LLM case (OpenAIExtractor._to_result):
      direction field parsed correctly from tool-call args.
  (3) Graph polarity rule:
      causes/exposed_to/sensitive_to: polarity = direction (0 if unknown/absent).
      benefits/hurts: polarity = base_polarity (unchanged).
  (4) Backward compatibility:
      edges.parquet without direction column loads with default polarity 0.
  (5) Community_input_edges unchanged.

All tests are hermetic: no network, no LLM, no filesystem beyond temp dirs.
"""

from __future__ import annotations

import json
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from theme_engine.extraction import (
    DIRECTION_EDGE_TYPES,
    EDGES_COLUMNS,
    ENTITIES_COLUMNS,
    EdgeCandidate,
    ExtractionResult,
    RuleBasedExtractor,
    _infer_direction,
)
from theme_engine.extraction import OpenAIExtractor  # for _to_result static method
from theme_engine import graph_build, runs
from theme_engine.config import settings
from theme_engine.models import RunCreateRequest


# ---------------------------------------------------------------------------
# (1) _infer_direction() unit tests — pure function, no I/O
# ---------------------------------------------------------------------------


class TestInferDirection:
    """Unit tests for the _infer_direction() helper function."""

    def test_beneficial_text_returns_plus_one(self):
        """Text with unambiguous beneficial signal -> +1."""
        assert _infer_direction("Acme Corp is positively exposed to electricity demand.") == 1

    def test_beneficial_tailwind_returns_plus_one(self):
        """Tailwind keyword -> +1."""
        assert _infer_direction("Rising copper prices are a tailwind for Cameco.") == 1

    def test_adverse_text_returns_minus_one(self):
        """Text with unambiguous adverse signal -> -1."""
        assert _infer_direction("Acme Corp is adversely exposed to copper prices.") == -1

    def test_adverse_headwind_returns_minus_one(self):
        """Headwind keyword -> -1."""
        assert _infer_direction("Higher interest rates are a headwind for real estate firms.") == -1

    def test_adverse_harm_returns_minus_one(self):
        """Harm keyword -> -1."""
        assert _infer_direction("Inflation causes harm to consumer purchasing power.") == -1

    def test_ambiguous_both_signals_returns_zero(self):
        """Both beneficial and adverse signals present -> 0 (ambiguous)."""
        # "positively" (beneficial) + "headwinds" (adverse) -> both fire -> 0
        assert _infer_direction("Positively exposed but headwinds from regulatory changes.") == 0

    def test_no_signal_returns_zero(self):
        """No directional language -> 0 (unknown)."""
        assert _infer_direction("Acme Corp is exposed to copper.") == 0

    def test_empty_string_returns_zero(self):
        """Empty string -> 0."""
        assert _infer_direction("") == 0

    def test_neutral_text_returns_zero(self):
        """Factual / neutral text without directional qualifiers -> 0."""
        assert _infer_direction("Electricity demand causes capex increases in the grid sector.") == 0

    def test_favorable_keyword_returns_plus_one(self):
        """'favorable' keyword -> +1."""
        assert _infer_direction("The conditions are favorable for Hydro One.") == 1

    def test_pressured_keyword_returns_minus_one(self):
        """'pressured' keyword -> -1."""
        assert _infer_direction("Margins are pressured by rising input costs.") == -1


# ---------------------------------------------------------------------------
# (2) RuleBasedExtractor direction tests — hermetic, no network
# ---------------------------------------------------------------------------


class TestRuleBasedExtractorDirection:
    """Verify that RuleBasedExtractor emits correct direction on directional edges."""

    def _extractor(self) -> RuleBasedExtractor:
        return RuleBasedExtractor()

    def test_exposed_to_beneficial_direction(self):
        """Beneficial exposure language -> exposed_to edge with direction=+1."""
        ex = self._extractor()
        text = "Acme Corp is positively exposed to copper prices and benefits from higher prices."
        result = ex.extract("chunk_1", text)
        exposed_edges = [e for e in result.edges if e.edge_type == "exposed_to"]
        assert exposed_edges, "Expected at least one exposed_to edge"
        # All exposed_to edges should have direction=+1 (beneficial signal in text)
        for e in exposed_edges:
            assert e.direction == 1, (
                f"Expected direction=+1 for beneficial exposure, got {e.direction!r}. "
                f"Edge: {e}"
            )

    def test_exposed_to_adverse_direction(self):
        """Adverse exposure language -> exposed_to edge with direction=-1."""
        ex = self._extractor()
        text = "Acme Corp is adversely exposed to copper prices."
        result = ex.extract("chunk_1", text)
        exposed_edges = [e for e in result.edges if e.edge_type == "exposed_to"]
        assert exposed_edges, "Expected at least one exposed_to edge"
        for e in exposed_edges:
            assert e.direction == -1, (
                f"Expected direction=-1 for adverse exposure, got {e.direction!r}."
            )

    def test_exposed_to_unknown_direction(self):
        """Exposure without directional qualifier -> exposed_to edge with direction=0."""
        ex = self._extractor()
        text = "Acme Corp is exposed to copper."
        result = ex.extract("chunk_1", text)
        exposed_edges = [e for e in result.edges if e.edge_type == "exposed_to"]
        assert exposed_edges, "Expected at least one exposed_to edge"
        for e in exposed_edges:
            assert e.direction == 0, (
                f"Expected direction=0 for ambiguous exposure, got {e.direction!r}."
            )

    def test_sensitive_to_adverse_direction(self):
        """Adverse sensitivity language -> sensitive_to edge with direction=-1."""
        ex = self._extractor()
        text = "Acme Corp is adversely sensitive to inflation."
        result = ex.extract("chunk_1", text)
        sensitive_edges = [e for e in result.edges if e.edge_type == "sensitive_to"]
        assert sensitive_edges, "Expected at least one sensitive_to edge"
        for e in sensitive_edges:
            assert e.direction == -1, (
                f"Expected direction=-1 for adverse sensitivity, got {e.direction!r}."
            )

    def test_sensitive_to_unknown_direction(self):
        """Sensitivity without directional qualifier -> direction=0."""
        ex = self._extractor()
        text = "Acme Corp is sensitive to inflation."
        result = ex.extract("chunk_1", text)
        sensitive_edges = [e for e in result.edges if e.edge_type == "sensitive_to"]
        assert sensitive_edges, "Expected at least one sensitive_to edge"
        for e in sensitive_edges:
            assert e.direction == 0

    def test_causes_adverse_direction(self):
        """Adverse causes language -> causes edge with direction=-1."""
        ex = self._extractor()
        text = "Electricity Demand adversely causes Capex Increase for grid operators."
        result = ex.extract("chunk_1", text)
        causes_edges = [e for e in result.edges if e.edge_type == "causes"]
        assert causes_edges, "Expected at least one causes edge"
        for e in causes_edges:
            assert e.direction == -1, (
                f"Expected direction=-1 for adverse causes, got {e.direction!r}."
            )

    def test_causes_beneficial_direction(self):
        """Beneficial causes language -> causes edge with direction=+1."""
        ex = self._extractor()
        text = "Electricity Demand favorably causes Capex Increase, supporting grid providers."
        result = ex.extract("chunk_1", text)
        causes_edges = [e for e in result.edges if e.edge_type == "causes"]
        assert causes_edges, "Expected at least one causes edge"
        for e in causes_edges:
            assert e.direction == 1, (
                f"Expected direction=+1 for beneficial causes, got {e.direction!r}."
            )

    def test_causes_unknown_direction(self):
        """Causes without directional qualifier -> direction=0."""
        ex = self._extractor()
        text = "Electricity Demand causes Capex Increase."
        result = ex.extract("chunk_1", text)
        causes_edges = [e for e in result.edges if e.edge_type == "causes"]
        assert causes_edges, "Expected at least one causes edge"
        for e in causes_edges:
            assert e.direction == 0, (
                f"Expected direction=0 for unqualified causes, got {e.direction!r}."
            )

    def test_benefits_edge_has_no_direction_field_relevance(self):
        """benefits edge direction defaults to 0 (sign from edge_type, not direction)."""
        ex = self._extractor()
        text = "Acme Corp benefits from electricity demand."
        result = ex.extract("chunk_1", text)
        benefit_edges = [e for e in result.edges if e.edge_type == "benefits"]
        # If any benefits edges produced, their direction field is 0 (not used for sign)
        for e in benefit_edges:
            assert e.direction == 0

    def test_no_direction_asserted_without_evidence(self):
        """Evidence-backed: direction non-zero only when text supports it."""
        ex = self._extractor()
        # Plain factual statement — no directional language
        text = "Acme Corp has some exposure to copper via its mining operations."
        result = ex.extract("chunk_1", text)
        for e in result.edges:
            if e.edge_type in DIRECTION_EDGE_TYPES:
                assert e.direction == 0, (
                    f"No directional evidence in text but got direction={e.direction!r} "
                    f"on {e.edge_type} edge. This violates evidence-backed direction."
                )


# ---------------------------------------------------------------------------
# (2b) Fake-LLM case: OpenAIExtractor._to_result direction parsing
# ---------------------------------------------------------------------------


class TestOpenAIExtractorDirectionParsing:
    """Verify direction is parsed from LLM tool-call args (no network call)."""

    def _parse(self, direction_value) -> list[EdgeCandidate]:
        """Invoke _to_result with a single edge carrying the given direction."""
        args = {
            "entities": [],
            "edges": [
                {
                    "source_name": "Acme Corp",
                    "target_name": "Copper",
                    "edge_type": "exposed_to",
                    "confidence": 0.85,
                    "explanation": "Acme is exposed to copper.",
                    "stated_in_text": True,
                    "direction": direction_value,
                }
            ],
        }
        return OpenAIExtractor._to_result(args).edges

    def test_direction_plus1_parsed(self):
        edges = self._parse(1)
        assert edges[0].direction == 1

    def test_direction_minus1_parsed(self):
        edges = self._parse(-1)
        assert edges[0].direction == -1

    def test_direction_zero_parsed(self):
        edges = self._parse(0)
        assert edges[0].direction == 0

    def test_direction_absent_defaults_to_zero(self):
        """Missing direction field defaults to 0."""
        args = {
            "entities": [],
            "edges": [
                {
                    "source_name": "Acme Corp",
                    "target_name": "Copper",
                    "edge_type": "exposed_to",
                    "confidence": 0.8,
                    "explanation": "x",
                    "stated_in_text": True,
                    # no direction field
                }
            ],
        }
        edges = OpenAIExtractor._to_result(args).edges
        assert edges[0].direction == 0

    def test_direction_invalid_string_defaults_to_zero(self):
        """Invalid direction value defaults to 0 (safe fallback)."""
        edges = self._parse("unknown")
        assert edges[0].direction == 0

    def test_direction_out_of_range_defaults_to_zero(self):
        """Out-of-range integer (e.g. 2) defaults to 0."""
        edges = self._parse(2)
        assert edges[0].direction == 0


# ---------------------------------------------------------------------------
# (3) graph_build polarity rule — uses real parquet fixtures
# ---------------------------------------------------------------------------

def _make_run() -> str:
    run = runs.create_run(RunCreateRequest(as_of_date="2024-06-30"))
    return run.run_id


def _write_fixture(run_id: str, ents: list[dict], edges: list[dict]) -> Path:
    ddir = Path(settings.run_output_dir) / run_id / "discovery"
    ddir.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.Table.from_pylist(ents), ddir / "entities.parquet")
    pq.write_table(pa.Table.from_pylist(edges), ddir / "edges.parquet")
    return ddir


def _ent(eid: str, etype: str = "Company") -> dict:
    return {c: "" for c in ENTITIES_COLUMNS} | {
        "entity_id": eid,
        "entity_type": etype,
        "name": eid,
        "canonical_name": eid,
        "first_seen_at": "2024-01-01",
    }


def _edge_row(
    eid: str,
    src: str,
    tgt: str,
    etype: str,
    confidence: float = 0.8,
    direction: int = 0,
) -> dict:
    """Build an edge parquet row including the new direction column."""
    return {c: "" for c in EDGES_COLUMNS} | {
        "edge_id": eid,
        "source_entity_id": src,
        "target_entity_id": tgt,
        "edge_type": etype,
        "confidence": str(confidence),
        "extraction_method": "document_stated",
        "first_seen_at": "2024-01-01",
        "evidence_chunk_ids": ["chunk_1"],
        "direction": direction,
    }


class TestGraphBuildDirectionPolarity:
    """graph_build.py sets effective polarity from direction for directional edge types."""

    @pytest.fixture
    def graph_with_causes_edges(self):
        run_id = _make_run()
        ents = [_ent("e_mi", "MacroIndicator"), _ent("e_ec", "EconomicConcept"), _ent("e_co", "Company")]
        edges = [
            _edge_row("causes_plus1",  "e_mi", "e_ec", "causes",    direction=1),
            _edge_row("causes_minus1", "e_mi", "e_co", "causes",    direction=-1),
            _edge_row("causes_zero",   "e_ec", "e_co", "causes",    direction=0),
        ]
        _write_fixture(run_id, ents, edges)
        graph_build.build_graph(run_id)
        ddir = Path(settings.run_output_dir) / run_id / "discovery"
        return json.loads((ddir / "graph.json").read_text())

    def test_causes_direction_plus1_polarity_is_plus1(self, graph_with_causes_edges):
        e_by_id = {e["edge_id"]: e for e in graph_with_causes_edges["edges"]}
        assert e_by_id["causes_plus1"]["polarity"] == 1

    def test_causes_direction_minus1_polarity_is_minus1(self, graph_with_causes_edges):
        e_by_id = {e["edge_id"]: e for e in graph_with_causes_edges["edges"]}
        assert e_by_id["causes_minus1"]["polarity"] == -1

    def test_causes_direction_zero_polarity_is_zero(self, graph_with_causes_edges):
        """Unknown direction -> polarity=0 -> excluded from signed propagation."""
        e_by_id = {e["edge_id"]: e for e in graph_with_causes_edges["edges"]}
        assert e_by_id["causes_zero"]["polarity"] == 0, (
            "Locked design decision: unknown direction -> 0, NOT +1."
        )

    def test_benefits_polarity_unchanged_at_plus1(self):
        """benefits edges keep polarity=+1 from base_polarity (unchanged by #110)."""
        run_id = _make_run()
        ents = [_ent("e_co", "Company"), _ent("e_ec", "EconomicConcept")]
        edges = [_edge_row("e_benefits", "e_co", "e_ec", "benefits", direction=0)]
        _write_fixture(run_id, ents, edges)
        graph_build.build_graph(run_id)
        ddir = Path(settings.run_output_dir) / run_id / "discovery"
        g = json.loads((ddir / "graph.json").read_text())
        e_by_id = {e["edge_id"]: e for e in g["edges"]}
        assert e_by_id["e_benefits"]["polarity"] == 1

    def test_hurts_polarity_unchanged_at_minus1(self):
        """hurts edges keep polarity=-1 from base_polarity (unchanged by #110)."""
        run_id = _make_run()
        ents = [_ent("e_co", "Company"), _ent("e_ec", "EconomicConcept")]
        edges = [_edge_row("e_hurts", "e_co", "e_ec", "hurts", direction=0)]
        _write_fixture(run_id, ents, edges)
        graph_build.build_graph(run_id)
        ddir = Path(settings.run_output_dir) / run_id / "discovery"
        g = json.loads((ddir / "graph.json").read_text())
        e_by_id = {e["edge_id"]: e for e in g["edges"]}
        assert e_by_id["e_hurts"]["polarity"] == -1

    def test_exposed_to_direction_minus1_polarity_minus1(self):
        """exposed_to with direction=-1 -> polarity=-1."""
        run_id = _make_run()
        ents = [_ent("e_co", "Company"), _ent("e_ec", "EconomicConcept")]
        edges = [_edge_row("e_exp_adverse", "e_co", "e_ec", "exposed_to", direction=-1)]
        _write_fixture(run_id, ents, edges)
        graph_build.build_graph(run_id)
        ddir = Path(settings.run_output_dir) / run_id / "discovery"
        g = json.loads((ddir / "graph.json").read_text())
        e_by_id = {e["edge_id"]: e for e in g["edges"]}
        assert e_by_id["e_exp_adverse"]["polarity"] == -1

    def test_sensitive_to_direction_plus1_polarity_plus1(self):
        """sensitive_to with direction=+1 -> polarity=+1."""
        run_id = _make_run()
        ents = [_ent("e_co", "Company"), _ent("e_mi", "MacroIndicator")]
        edges = [_edge_row("e_sens_beneficial", "e_co", "e_mi", "sensitive_to", direction=1)]
        _write_fixture(run_id, ents, edges)
        graph_build.build_graph(run_id)
        ddir = Path(settings.run_output_dir) / run_id / "discovery"
        g = json.loads((ddir / "graph.json").read_text())
        e_by_id = {e["edge_id"]: e for e in g["edges"]}
        assert e_by_id["e_sens_beneficial"]["polarity"] == 1


# ---------------------------------------------------------------------------
# (4) Backward compatibility: old parquet without direction column
# ---------------------------------------------------------------------------


class TestBackwardCompatibility:
    """Old edges.parquet without the direction column loads with default polarity 0."""

    def test_old_edges_without_direction_column_load_safely(self):
        """edges.parquet missing the direction column -> direction treated as 0 -> polarity 0 for causes."""
        run_id = _make_run()
        ents = [_ent("e_mi", "MacroIndicator"), _ent("e_co", "Company")]

        # Build old-style edge rows WITHOUT the direction column
        old_cols_without_direction = [c for c in EDGES_COLUMNS if c != "direction"]
        old_edge = {c: "" for c in old_cols_without_direction}
        old_edge.update({
            "edge_id": "old_causes",
            "source_entity_id": "e_mi",
            "target_entity_id": "e_co",
            "edge_type": "causes",
            "confidence": "0.8",
            "extraction_method": "document_stated",
            "first_seen_at": "2024-01-01",
            "evidence_chunk_ids": ["chunk_1"],
        })

        ddir = Path(settings.run_output_dir) / run_id / "discovery"
        ddir.mkdir(parents=True, exist_ok=True)
        pq.write_table(pa.Table.from_pylist([_ent("e_mi", "MacroIndicator"), _ent("e_co", "Company")]), ddir / "entities.parquet")
        # Write old-style edges parquet without direction column
        pq.write_table(pa.Table.from_pylist([old_edge]), ddir / "edges.parquet")

        # Build graph — should not raise
        graph_build.build_graph(run_id)

        ddir2 = Path(settings.run_output_dir) / run_id / "discovery"
        g = json.loads((ddir2 / "graph.json").read_text())
        e_by_id = {e["edge_id"]: e for e in g["edges"]}
        assert "old_causes" in e_by_id
        # Without direction column: direction=None -> effective polarity=0 for causes
        assert e_by_id["old_causes"]["polarity"] == 0, (
            "Old edges.parquet without direction column should yield polarity=0 for causes "
            "(backward-compatible default, locked design decision: unknown -> 0)."
        )

    def test_community_input_edges_unchanged_by_direction(self):
        """community_input_edges selection is unaffected by the direction field."""
        run_id = _make_run()
        ents = [_ent("e_co", "Company"), _ent("e_ec", "EconomicConcept")]
        edges = [
            _edge_row("e_struct",    "e_co", "e_ec", "exposed_to",    direction=-1),  # structural
            _edge_row("e_evidence",  "e_co", "e_ec", "co_occurs_with", direction=0),  # evidence
        ]
        _write_fixture(run_id, ents, edges)
        graph_build.build_graph(run_id)
        ddir = Path(settings.run_output_dir) / run_id / "discovery"
        g = json.loads((ddir / "graph.json").read_text())
        cie = set(g["community_input_edges"])
        assert "e_struct" in cie, "structural exposed_to edge must be in community_input_edges"
        assert "e_evidence" not in cie, "co_occurs_with must NOT be in community_input_edges"
