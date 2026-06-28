"""FI-D: projection-narrative pass — hermetic unit tests (GitHub #107).

All tests use a fake OpenAI-compatible client and a temporary run directory.
No network calls, no real LLM, no external filesystem I/O.

Acceptance criteria verified here:
  (1) PATH-ONLY citation: only chunks in the impact's evidence_chunk_ids are
      passed to the LLM; a chunk outside the path never appears in the prompt.
  (2) Thin-evidence impact flagged low-confidence, not asserted:
      < THIN_EVIDENCE_THRESHOLD chunks → confidence_level == "low".
  (3) No narrative/claim emitted without an evidence chunk: empty
      evidence_chunk_ids → no LLM call; returns the no-evidence stub with
      confidence_level == "insufficient".
  (4) Hermetic: no network (fake client only).
  (5) Thin-evidence cap enforced even if model returns a higher confidence:
      model returning "high" is overridden to "low".
  (6) dossier respects path boundary: gather_projection_dossier loads only
      path chunks, not all chunks in the run.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

BACKEND = Path(__file__).resolve().parents[2] / "app" / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from theme_engine import reasoning, runs  # noqa: E402
from theme_engine.config import settings  # noqa: E402
from theme_engine.models import RunCreateRequest  # noqa: E402
from theme_engine.reasoning import (  # noqa: E402
    _NO_EVIDENCE_NARRATIVE,
    _NO_EVIDENCE_SANITY,
    _THIN_EVIDENCE_THRESHOLD,
    gather_projection_dossier,
    synthesize_projection_narrative,
)


# ── fake OpenAI-compatible client ────────────────────────────────────────────

class _Msg:
    def __init__(self, content=None, tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls


class _Resp:
    def __init__(self, msg):
        self.choices = [type("C", (), {"message": msg})()]


class _ToolCall:
    def __init__(self, args_str: str):
        self.function = type("F", (), {"arguments": args_str})()


class _FakeClient:
    """Returns a fixed response; records the last ``messages`` seen."""

    def __init__(self, response_args: dict | None = None, content: str = ""):
        self._args = response_args
        self._content = content
        self.calls: list[dict] = []   # records each create() call kwargs

    @property
    def chat(self):
        return self

    @property
    def completions(self):
        return self

    def create(self, **kwargs):
        self.calls.append(kwargs)
        tool_calls = None
        if self._args is not None:
            tool_calls = [_ToolCall(json.dumps(self._args))]
        return _Resp(_Msg(content=self._content, tool_calls=tool_calls))


# ── run-seeding helper ────────────────────────────────────────────────────────

def _seed_run(
    chunks: list[dict] | None = None,
    edge_explanations: list[dict] | None = None,
) -> str:
    """Write a minimal run directory with chunks + edge_explanations.

    ``chunks`` is a list of dicts with at least ``chunk_id`` and ``text``.
    ``edge_explanations`` is a list of dicts with ``edge_id`` and ``explanation``.
    """
    run = runs.create_run(RunCreateRequest(as_of_date="2024-06-30"))
    d = Path(settings.run_output_dir) / run.run_id / "discovery"
    d.mkdir(parents=True, exist_ok=True)

    # Write chunks.parquet
    if chunks:
        ids = [c["chunk_id"] for c in chunks]
        texts = [c.get("text", "") for c in chunks]
        pq.write_table(
            pa.table({"chunk_id": ids, "text": texts}),
            d / "chunks.parquet",
        )
    else:
        pq.write_table(
            pa.table({"chunk_id": pa.array([], type=pa.string()),
                      "text": pa.array([], type=pa.string())}),
            d / "chunks.parquet",
        )

    # Write edge_explanations.parquet
    if edge_explanations:
        eids = [e["edge_id"] for e in edge_explanations]
        expls = [e.get("explanation", "") for e in edge_explanations]
        pq.write_table(
            pa.table({"edge_id": eids, "explanation": expls}),
            d / "edge_explanations.parquet",
        )
    else:
        pq.write_table(
            pa.table({"edge_id": pa.array([], type=pa.string()),
                      "explanation": pa.array([], type=pa.string())}),
            d / "edge_explanations.parquet",
        )

    return run.run_id


def _impact(
    trigger_id: str = "EV1",
    trigger_kind: str = "Event",
    company_id: str = "CO1",
    direction: int = 1,
    strength: float = 0.8,
    confidence: float = 0.75,
    path: list[str] | None = None,
    contributing_edge_ids: list[str] | None = None,
    evidence_chunk_ids: list[str] | None = None,
) -> dict:
    """Build a minimal projected_impact dict."""
    return {
        "trigger_id": trigger_id,
        "trigger_kind": trigger_kind,
        "company_id": company_id,
        "direction": direction,
        "strength": strength,
        "confidence": confidence,
        "path": path or [],
        "contributing_edge_ids": contributing_edge_ids or [],
        "evidence_chunk_ids": evidence_chunk_ids or [],
    }


# ── (6) gather_projection_dossier: path-only chunk loading ───────────────────

class TestGatherProjectionDossier:
    """gather_projection_dossier loads ONLY the chunks in evidence_chunk_ids."""

    def test_loads_only_path_chunks(self):
        """Chunks outside the path are not returned in the dossier."""
        rid = _seed_run(chunks=[
            {"chunk_id": "path_chunk_1", "text": "On-path evidence A."},
            {"chunk_id": "path_chunk_2", "text": "On-path evidence B."},
            {"chunk_id": "off_path_chunk", "text": "Off-path text — must not appear."},
        ])
        impact = _impact(
            evidence_chunk_ids=["path_chunk_1", "path_chunk_2"],
            contributing_edge_ids=["e1"],
        )
        dossier = gather_projection_dossier(rid, impact)
        returned_ids = {ec["chunk_id"] for ec in dossier["evidence_chunks"]}
        assert returned_ids == {"path_chunk_1", "path_chunk_2"}
        assert "off_path_chunk" not in returned_ids

    def test_off_path_text_absent_from_dossier(self):
        """The text of an off-path chunk must not appear in evidence_chunks."""
        rid = _seed_run(chunks=[
            {"chunk_id": "p1", "text": "Relevant evidence."},
            {"chunk_id": "secret", "text": "SECRET_SIGNAL_NOT_IN_PATH"},
        ])
        impact = _impact(evidence_chunk_ids=["p1"])
        dossier = gather_projection_dossier(rid, impact)
        combined_text = " ".join(ec["text"] for ec in dossier["evidence_chunks"])
        assert "SECRET_SIGNAL_NOT_IN_PATH" not in combined_text

    def test_empty_evidence_ids_returns_empty_chunks(self):
        rid = _seed_run(chunks=[{"chunk_id": "c1", "text": "some text"}])
        impact = _impact(evidence_chunk_ids=[])
        dossier = gather_projection_dossier(rid, impact)
        assert dossier["evidence_chunks"] == []

    def test_evidence_thin_flag_set_correctly(self):
        """evidence_thin == True when < _THIN_EVIDENCE_THRESHOLD chunks."""
        rid = _seed_run(chunks=[{"chunk_id": "c1", "text": "only one chunk"}])
        impact = _impact(evidence_chunk_ids=["c1"])
        dossier = gather_projection_dossier(rid, impact)
        assert dossier["evidence_thin"] is True

    def test_evidence_not_thin_with_sufficient_chunks(self):
        rid = _seed_run(chunks=[
            {"chunk_id": "c1", "text": "chunk 1"},
            {"chunk_id": "c2", "text": "chunk 2"},
        ])
        impact = _impact(evidence_chunk_ids=["c1", "c2"])
        dossier = gather_projection_dossier(rid, impact)
        assert dossier["evidence_thin"] is False

    def test_edge_explanations_loaded_for_contributing_edges_only(self):
        rid = _seed_run(
            chunks=[{"chunk_id": "c1", "text": "text"}],
            edge_explanations=[
                {"edge_id": "path_edge", "explanation": "Path explanation."},
                {"edge_id": "other_edge", "explanation": "Off-path explanation."},
            ],
        )
        impact = _impact(
            evidence_chunk_ids=["c1"],
            contributing_edge_ids=["path_edge"],
        )
        dossier = gather_projection_dossier(rid, impact)
        returned_eids = {ee["edge_id"] for ee in dossier["edge_explanations"]}
        assert returned_eids == {"path_edge"}
        # Note: other_edge is still looked up via expl_all but only path-edge entries
        # are in the returned list because only contributing_edge_ids are iterated.
        # The off-path explanation text must not appear.
        combined_expls = " ".join(ee["explanation"] for ee in dossier["edge_explanations"])
        assert "Off-path explanation" not in combined_expls

    def test_dossier_metadata_copied_from_impact(self):
        rid = _seed_run(chunks=[{"chunk_id": "c1", "text": "x"}])
        impact = _impact(
            trigger_id="EV_ALPHA",
            company_id="CO_BETA",
            direction=-1,
            strength=0.42,
            confidence=0.55,
            evidence_chunk_ids=["c1"],
        )
        dossier = gather_projection_dossier(rid, impact)
        assert dossier["trigger_id"] == "EV_ALPHA"
        assert dossier["company_id"] == "CO_BETA"
        assert dossier["direction"] == -1
        assert abs(dossier["strength"] - 0.42) < 1e-9
        assert abs(dossier["confidence"] - 0.55) < 1e-9


# ── (3) No evidence → no-evidence stub, no LLM call ─────────────────────────

class TestNoEvidence:
    """When evidence_chunk_ids is empty, return the no-evidence stub without LLM."""

    def test_no_llm_call_when_no_evidence(self):
        rid = _seed_run()
        fake = _FakeClient()
        impact = _impact(evidence_chunk_ids=[])
        result = synthesize_projection_narrative(rid, impact, client=fake, model="x")
        assert fake.calls == [], "LLM must NOT be called when evidence is empty"

    def test_returns_insufficient_confidence_when_no_evidence(self):
        rid = _seed_run()
        fake = _FakeClient()
        impact = _impact(evidence_chunk_ids=[])
        result = synthesize_projection_narrative(rid, impact, client=fake, model="x")
        assert result["confidence_level"] == "insufficient"

    def test_narrative_is_no_evidence_stub(self):
        rid = _seed_run()
        fake = _FakeClient()
        impact = _impact(evidence_chunk_ids=[])
        result = synthesize_projection_narrative(rid, impact, client=fake, model="x")
        assert result["narrative"] == _NO_EVIDENCE_NARRATIVE

    def test_sanity_check_is_no_evidence_stub(self):
        rid = _seed_run()
        fake = _FakeClient()
        impact = _impact(evidence_chunk_ids=[])
        result = synthesize_projection_narrative(rid, impact, client=fake, model="x")
        assert result["sanity_check"] == _NO_EVIDENCE_SANITY

    def test_evidence_thin_true_when_no_chunks(self):
        rid = _seed_run()
        fake = _FakeClient()
        impact = _impact(evidence_chunk_ids=[])
        result = synthesize_projection_narrative(rid, impact, client=fake, model="x")
        assert result["evidence_thin"] is True


# ── (2) Thin evidence → confidence_level forced to "low" ─────────────────────

class TestThinEvidence:
    """< _THIN_EVIDENCE_THRESHOLD chunks → confidence_level == 'low'."""

    def _make_one_chunk_run(self) -> tuple[str, dict]:
        rid = _seed_run(chunks=[{"chunk_id": "c1", "text": "Single evidence snippet."}])
        impact = _impact(evidence_chunk_ids=["c1"], contributing_edge_ids=["e1"])
        return rid, impact

    def test_thin_evidence_sets_low_confidence(self):
        rid, impact = self._make_one_chunk_run()
        fake = _FakeClient(response_args={
            "narrative": "Thin narrative.",
            "sanity_check": "Thin check.",
            "confidence_level": "moderate",   # model tries to return moderate
        })
        result = synthesize_projection_narrative(rid, impact, client=fake, model="x")
        # Even though model returned "moderate", thin-evidence cap must downgrade to "low"
        assert result["confidence_level"] == "low"

    def test_thin_evidence_cap_overrides_high_confidence_too(self):
        """Model returning 'high' must still be overridden when evidence is thin."""
        rid, impact = self._make_one_chunk_run()
        fake = _FakeClient(response_args={
            "narrative": "Over-confident narrative.",
            "sanity_check": "Check.",
            "confidence_level": "high",
        })
        result = synthesize_projection_narrative(rid, impact, client=fake, model="x")
        assert result["confidence_level"] == "low"

    def test_thin_evidence_model_returns_low_remains_low(self):
        """If model correctly returns 'low', it stays 'low'."""
        rid, impact = self._make_one_chunk_run()
        fake = _FakeClient(response_args={
            "narrative": "Low-confidence narrative.",
            "sanity_check": "Correctly cautious.",
            "confidence_level": "low",
        })
        result = synthesize_projection_narrative(rid, impact, client=fake, model="x")
        assert result["confidence_level"] == "low"

    def test_thin_evidence_model_returns_insufficient_preserved(self):
        """If model returns 'insufficient' (even stricter), that is preserved."""
        rid, impact = self._make_one_chunk_run()
        fake = _FakeClient(response_args={
            "narrative": "No confidence.",
            "sanity_check": "No support.",
            "confidence_level": "insufficient",
        })
        result = synthesize_projection_narrative(rid, impact, client=fake, model="x")
        assert result["confidence_level"] == "insufficient"

    def test_sufficient_evidence_allows_non_low_confidence(self):
        """With >= _THIN_EVIDENCE_THRESHOLD chunks, model's confidence is respected."""
        rid = _seed_run(chunks=[
            {"chunk_id": "c1", "text": "Evidence A."},
            {"chunk_id": "c2", "text": "Evidence B."},
        ])
        impact = _impact(evidence_chunk_ids=["c1", "c2"])
        fake = _FakeClient(response_args={
            "narrative": "Well-supported narrative.",
            "sanity_check": "Evidence is adequate.",
            "confidence_level": "moderate",
        })
        result = synthesize_projection_narrative(rid, impact, client=fake, model="x")
        assert result["confidence_level"] == "moderate"

    def test_evidence_thin_flag_in_result(self):
        rid, impact = self._make_one_chunk_run()
        fake = _FakeClient(response_args={
            "narrative": "N.", "sanity_check": "S.", "confidence_level": "low",
        })
        result = synthesize_projection_narrative(rid, impact, client=fake, model="x")
        assert result["evidence_thin"] is True


# ── (1) PATH-ONLY citation: only path chunks reach the LLM prompt ─────────────

class TestPathOnlyCitation:
    """Only chunks in evidence_chunk_ids appear in the LLM's prompt messages."""

    def test_off_path_chunk_text_absent_from_llm_prompt(self):
        """An off-path chunk's text must never appear in any LLM message."""
        rid = _seed_run(chunks=[
            {"chunk_id": "path_chunk", "text": "On-path evidence about tariffs."},
            {"chunk_id": "off_chunk",  "text": "CANARY_OFF_PATH_TEXT_XYZ"},
        ])
        impact = _impact(
            evidence_chunk_ids=["path_chunk"],   # only path_chunk in scope
            contributing_edge_ids=["e1"],
        )
        fake = _FakeClient(response_args={
            "narrative": "Narrative.", "sanity_check": "Check.", "confidence_level": "low",
        })
        synthesize_projection_narrative(rid, impact, client=fake, model="x")

        assert fake.calls, "Expected at least one LLM call"
        all_prompt_text = " ".join(
            msg["content"]
            for call in fake.calls
            for msg in call.get("messages", [])
        )
        assert "CANARY_OFF_PATH_TEXT_XYZ" not in all_prompt_text, (
            "Off-path chunk text must never appear in the LLM prompt"
        )

    def test_path_chunk_text_present_in_llm_prompt(self):
        """The on-path chunk's text IS present in the LLM prompt."""
        rid = _seed_run(chunks=[
            {"chunk_id": "p1", "text": "EXPECTED_ON_PATH_TEXT_ABC"},
        ])
        impact = _impact(evidence_chunk_ids=["p1"])
        fake = _FakeClient(response_args={
            "narrative": "N.", "sanity_check": "S.", "confidence_level": "low",
        })
        synthesize_projection_narrative(rid, impact, client=fake, model="x")

        all_prompt_text = " ".join(
            msg["content"]
            for call in fake.calls
            for msg in call.get("messages", [])
        )
        assert "EXPECTED_ON_PATH_TEXT_ABC" in all_prompt_text

    def test_multiple_off_path_chunks_all_excluded(self):
        """Multiple off-path chunks are all excluded from the prompt."""
        rid = _seed_run(chunks=[
            {"chunk_id": "path1", "text": "Path evidence 1."},
            {"chunk_id": "path2", "text": "Path evidence 2."},
            {"chunk_id": "off1",  "text": "OFFPATH_ALPHA"},
            {"chunk_id": "off2",  "text": "OFFPATH_BETA"},
        ])
        impact = _impact(evidence_chunk_ids=["path1", "path2"])
        fake = _FakeClient(response_args={
            "narrative": "N.", "sanity_check": "S.", "confidence_level": "moderate",
        })
        synthesize_projection_narrative(rid, impact, client=fake, model="x")

        all_prompt_text = " ".join(
            msg["content"]
            for call in fake.calls
            for msg in call.get("messages", [])
        )
        assert "OFFPATH_ALPHA" not in all_prompt_text
        assert "OFFPATH_BETA" not in all_prompt_text


# ── Hermetic: no network (all tests already hermetic via fake client) ──────────

class TestHermetic:
    """Verify the synthesize_projection_narrative() without a real LLM."""

    def test_returns_structured_result(self):
        rid = _seed_run(chunks=[
            {"chunk_id": "c1", "text": "Evidence A."},
            {"chunk_id": "c2", "text": "Evidence B."},
        ])
        impact = _impact(evidence_chunk_ids=["c1", "c2"])
        fake = _FakeClient(response_args={
            "narrative": "The evidence implies a positive projected impact on CO1.",
            "sanity_check": "The evidence is consistent with the projected direction.",
            "confidence_level": "moderate",
        })
        result = synthesize_projection_narrative(rid, impact, client=fake, model="x")
        assert result["narrative"] == "The evidence implies a positive projected impact on CO1."
        assert result["sanity_check"] == "The evidence is consistent with the projected direction."
        assert result["confidence_level"] == "moderate"
        assert result["trigger_id"] == "EV1"
        assert result["company_id"] == "CO1"

    def test_result_contains_all_expected_keys(self):
        rid = _seed_run(chunks=[
            {"chunk_id": "c1", "text": "A."},
            {"chunk_id": "c2", "text": "B."},
        ])
        impact = _impact(evidence_chunk_ids=["c1", "c2"])
        fake = _FakeClient(response_args={
            "narrative": "N.", "sanity_check": "S.", "confidence_level": "moderate",
        })
        result = synthesize_projection_narrative(rid, impact, client=fake, model="x")
        expected_keys = {
            "trigger_id", "trigger_kind", "company_id", "direction", "strength",
            "path_edge_ids", "evidence_chunks", "evidence_thin",
            "narrative", "sanity_check", "confidence_level", "reasoning_chain",
        }
        assert expected_keys <= set(result.keys()), (
            f"Missing keys: {expected_keys - set(result.keys())}"
        )

    def test_think_tag_extracted_as_reasoning_chain(self):
        """<think>...</think> in LLM content is captured in reasoning_chain."""
        rid = _seed_run(chunks=[
            {"chunk_id": "c1", "text": "A."},
            {"chunk_id": "c2", "text": "B."},
        ])
        impact = _impact(evidence_chunk_ids=["c1", "c2"])
        fake = _FakeClient(
            content="<think>My internal reasoning about the impact.</think>",
            response_args={
                "narrative": "N.", "sanity_check": "S.", "confidence_level": "moderate",
            },
        )
        result = synthesize_projection_narrative(rid, impact, client=fake, model="x")
        assert result["reasoning_chain"] == "My internal reasoning about the impact."

    def test_direction_negative_impact(self):
        rid = _seed_run(chunks=[
            {"chunk_id": "c1", "text": "A."},
            {"chunk_id": "c2", "text": "B."},
        ])
        impact = _impact(direction=-1, evidence_chunk_ids=["c1", "c2"])
        fake = _FakeClient(response_args={
            "narrative": "Negative impact projected.",
            "sanity_check": "Evidence supports negative direction.",
            "confidence_level": "moderate",
        })
        result = synthesize_projection_narrative(rid, impact, client=fake, model="x")
        assert result["direction"] == -1
        assert result["narrative"] == "Negative impact projected."

    def test_path_edge_ids_returned(self):
        rid = _seed_run(
            chunks=[{"chunk_id": "c1", "text": "A."}, {"chunk_id": "c2", "text": "B."}],
            edge_explanations=[{"edge_id": "e1", "explanation": "Edge 1 explanation."}],
        )
        impact = _impact(
            evidence_chunk_ids=["c1", "c2"],
            contributing_edge_ids=["e1"],
        )
        fake = _FakeClient(response_args={
            "narrative": "N.", "sanity_check": "S.", "confidence_level": "moderate",
        })
        result = synthesize_projection_narrative(rid, impact, client=fake, model="x")
        assert result["path_edge_ids"] == ["e1"]

    def test_evidence_chunks_returned_in_result(self):
        rid = _seed_run(chunks=[
            {"chunk_id": "c1", "text": "Evidence for CO1."},
            {"chunk_id": "c2", "text": "More evidence."},
        ])
        impact = _impact(evidence_chunk_ids=["c1", "c2"])
        fake = _FakeClient(response_args={
            "narrative": "N.", "sanity_check": "S.", "confidence_level": "moderate",
        })
        result = synthesize_projection_narrative(rid, impact, client=fake, model="x")
        chunk_ids = {ec["chunk_id"] for ec in result["evidence_chunks"]}
        assert chunk_ids == {"c1", "c2"}


# ── Thin-evidence constant sanity ─────────────────────────────────────────────

class TestThresholdConstant:
    def test_threshold_at_least_two(self):
        """_THIN_EVIDENCE_THRESHOLD must be >= 2 (design requirement)."""
        assert _THIN_EVIDENCE_THRESHOLD >= 2
