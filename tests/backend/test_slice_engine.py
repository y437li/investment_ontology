import json
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[2] / "app" / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from theme_engine import slice_engine, runs
from theme_engine.config import settings
from theme_engine.models import RunCreateRequest


def _seed(graph: dict, as_of_date: str = "2024-06-30") -> str:
    run = runs.create_run(RunCreateRequest(as_of_date=as_of_date))
    d = Path(settings.run_output_dir) / run.run_id / "discovery"
    d.mkdir(parents=True, exist_ok=True)
    graph.setdefault("as_of_date", as_of_date)
    (d / "graph.json").write_text(json.dumps(graph))
    return run.run_id


def _node(eid, etype, label):
    return {"entity_id": eid, "entity_type": etype, "label": label}


def _edge(s, t, etype, w, method="document_stated"):
    return {
        "edge_id": f"{s}-{t}-{etype}",
        "source_entity_id": s,
        "target_entity_id": t,
        "edge_type": etype,
        "weight": w,
        "extraction_method": method,
    }


def test_downstream_out_reaches_company_at_correct_hop():
    run = _seed({
        "nodes": [
            _node("a", "MacroIndicator", "Rates"),
            _node("b", "Sector", "Banks"),
            _node("c", "Company", "RBC"),
        ],
        "edges": [
            _edge("a", "b", "causes", 0.9),
            _edge("b", "c", "benefits", 0.8),
        ],
    })
    s = slice_engine.extract_slice(run, "a", depth=2, direction="out")
    ids = {n["id"] for n in s["nodes"]}
    assert ids == {"a", "b", "c"}
    hop = {n["id"]: n["hop"] for n in s["nodes"]}
    assert hop == {"a": 0, "b": 1, "c": 2}
    assert s["anchor"]["level"] == "macro"
    assert s["edge_count"] == 2
    assert s["truncated"] is False
    assert s["as_of_date"] == "2024-06-30"


def test_direction_in_vs_out_are_distinct():
    graph = {
        "nodes": [
            _node("a", "MacroIndicator", "Rates"),
            _node("b", "Sector", "Banks"),
            _node("c", "Company", "RBC"),
        ],
        "edges": [
            _edge("a", "b", "causes", 0.9),
            _edge("b", "c", "benefits", 0.8),
        ],
    }
    run = _seed(graph)

    s_in_c = slice_engine.extract_slice(run, "c", depth=2, direction="in")
    assert {n["id"] for n in s_in_c["nodes"]} == {"a", "b", "c"}
    assert next(n["hop"] for n in s_in_c["nodes"] if n["id"] == "c") == 0

    s_out_a = slice_engine.extract_slice(run, "a", depth=2, direction="out")
    assert {n["id"] for n in s_out_a["nodes"]} == {"a", "b", "c"}

    s_in_a = slice_engine.extract_slice(run, "a", depth=2, direction="in")
    assert {n["id"] for n in s_in_a["nodes"]} == {"a"}


def test_evidence_and_method_and_weight_admission():
    run = _seed({
        "nodes": [
            _node("a", "MacroIndicator", "Rates"),
            _node("b", "Company", "RBC"),
            _node("z", "Company", "Other"),
            _node("q", "Company", "Guess"),
        ],
        "edges": [
            _edge("a", "b", "benefits", 0.9),
            _edge("a", "b", "mentioned_in", 0.9),
            _edge("a", "z", "benefits", 0.2),
            _edge("a", "q", "benefits", 0.9, method="llm_inferred"),
        ],
    })
    s = slice_engine.extract_slice(run, "a", depth=1, direction="out", min_weight=0.5)
    assert {n["id"] for n in s["nodes"]} == {"a", "b"}
    assert s["edge_count"] == 1
    assert s["edges"][0]["edge_type"] == "benefits"
    assert s["edges"][0]["source"] == "a" and s["edges"][0]["target"] == "b"


def test_level_filter_blocks_relay_and_keeps_anchor():
    graph = {
        "nodes": [
            _node("a", "MacroIndicator", "Rates"),
            _node("b", "Sector", "Banks"),
            _node("c", "Company", "RBC"),
        ],
        "edges": [
            _edge("a", "b", "causes", 0.9),
            _edge("b", "c", "benefits", 0.8),
        ],
    }
    run = _seed(graph)

    s = slice_engine.extract_slice(
        run, "a", depth=3, direction="out", levels=["macro", "company"]
    )
    ids = {n["id"] for n in s["nodes"]}
    assert "c" not in ids  # industry relay b filtered out, cannot bridge
    assert ids == {"a"}

    s2 = slice_engine.extract_slice(
        run, "a", depth=3, direction="out", levels=["industry"]
    )
    ids2 = {n["id"] for n in s2["nodes"]}
    assert "a" in ids2  # anchor always kept even though macro excluded
    assert "b" in ids2


def test_max_nodes_truncates_deterministically():
    weights = {"b1": 0.5, "b2": 0.9, "b3": 0.7, "b4": 0.9, "b5": 0.1}
    run = _seed({
        "nodes": [_node("a", "MacroIndicator", "Rates")]
        + [_node(b, "Company", b.upper()) for b in weights],
        "edges": [_edge("a", b, "benefits", w) for b, w in weights.items()],
    })
    s = slice_engine.extract_slice(run, "a", depth=1, direction="out", max_nodes=3)
    ids = {n["id"] for n in s["nodes"]}
    # anchor + 2 highest ranked: b2(0.9) & b4(0.9) tie on weight -> entity_id tiebreak.
    assert ids == {"a", "b2", "b4"}
    assert s["truncated"] is True
    assert s["node_count"] == 3
    # induced edges only among kept nodes.
    for e in s["edges"]:
        assert e["source"] in ids and e["target"] in ids
    assert s["edge_count"] == 2


def test_anchor_resolution_and_errors():
    run = _seed({
        "nodes": [
            _node("a", "MacroIndicator", "Interest Rates"),
            _node("z", "MacroIndicator", "Rates Swap"),
        ],
        "edges": [],
    })

    # exact label, case-insensitive.
    s = slice_engine.extract_slice(run, "interest rates", depth=1, direction="out")
    assert s["anchor"]["id"] == "a"

    # ambiguous substring 'rate' -> matches both labels.
    with pytest.raises(slice_engine.AnchorAmbiguous) as ai:
        slice_engine.extract_slice(run, "rate", depth=1, direction="out")
    cand_ids = {c["entity_id"] for c in ai.value.candidates}
    assert {"a", "z"} <= cand_ids

    # not found.
    with pytest.raises(slice_engine.AnchorNotFound) as nf:
        slice_engine.extract_slice(run, "nonexistent", depth=1, direction="out")
    assert nf.value.candidates  # carries candidates

    # invalid direction.
    with pytest.raises(ValueError):
        slice_engine.extract_slice(run, "a", direction="sideways")

    # depth=0 -> anchor-only slice.
    s0 = slice_engine.extract_slice(run, "a", depth=0, direction="out")
    assert {n["id"] for n in s0["nodes"]} == {"a"}
    assert s0["edges"] == []
    assert s0["truncated"] is False
