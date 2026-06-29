"""OI-6 R2: multi-period panel loop + concept-spine lineage + trajectories.

Hermetic (rule-based extractor, no network):
  - test_loop_produces_per_point_discovery: run_panel drives a 2-point loop;
    each point's discovery/<as_of>/ is isolated (PIT-gated corpus) and carries
    the correct as_of_date; the run-level flag flips once both points freeze.
  - test_concept_spine_lineage_links_theme_across_points: a theme family spans
    T1+T2 via shared concept spine even though point-local community_ids differ
    and company membership changes; lifecycle persisted, confidence >= 0.5.
  - test_exposure_trajectory_per_company: two ordered rows per (family, company)
    across as_of with differing exposure_score.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from theme_engine import (
    discovery_panel,
    exposure as exposure_mod,
    graph_build,
    runs,
    run_cache,
    themes,
)
from theme_engine.extraction import ENTITIES_COLUMNS, EDGES_COLUMNS
from theme_engine.data_import import REQUIRED_MANIFEST_COLUMNS
from theme_engine.models import RunCreateRequest

T1 = "2024-03-31"
T2 = "2024-06-30"


# --------------------------------------------------------------------------- #
# Direct-seed helpers (lineage + trajectory tests)                            #
# --------------------------------------------------------------------------- #


def _ent(eid: str, etype: str, first_seen: str = "2024-01-01") -> dict:
    r = {c: "" for c in ENTITIES_COLUMNS}
    r.update(
        entity_id=eid, entity_type=etype, name=eid, canonical_name=eid,
        first_seen_at=first_seen, confidence="0.9",
        extraction_method="document_stated", review_status="pending",
    )
    return r


def _edge(eid: str, src: str, tgt: str, etype: str,
          first_seen: str = "2024-01-01", confidence: str = "0.8") -> dict:
    r = {c: "" for c in EDGES_COLUMNS}
    r.update(
        edge_id=eid, source_entity_id=src, target_entity_id=tgt,
        edge_type=etype, first_seen_at=first_seen, confidence=confidence,
        extraction_method="document_stated",
    )
    return r


def _seed_point(run_id: str, as_of: str, ents: list[dict], edges: list[dict]) -> None:
    """Write entities/edges and run graph -> themes -> exposure for one point."""
    d = runs.discovery_point_dir(run_id, as_of, for_write=True)
    pq.write_table(pa.Table.from_pylist(ents), d / "entities.parquet")
    pq.write_table(pa.Table.from_pylist(edges), d / "edges.parquet")
    graph_build.build_graph(run_id, as_of=as_of)
    themes.discover_themes(run_id, as_of=as_of)
    exposure_mod.compute_exposure(run_id, as_of=as_of)


# --------------------------------------------------------------------------- #
# Full-pipeline document fixture (loop test)                                  #
# --------------------------------------------------------------------------- #


def _write_doc(docs_dir: Path, name: str, text: str) -> None:
    (docs_dir / name).write_text(text, encoding="utf-8")


def _manifest_row(source_id: str, company_id: str, raw_path: str,
                  available_at: str) -> dict:
    return {
        "source": "news",
        "source_id": source_id,
        "title": source_id,
        "document_type": "news",
        "company_id": company_id,
        "raw_path": raw_path,
        "published_at": available_at,
        "available_at": available_at,
        "vintage": f"{available_at}T00:00:00Z",
        "language": "en",
        "source_url": f"https://example.com/{source_id}",
        "license": "public",
        "confidentiality": "public",
        "notes": "seed",
    }


def _write_manifest(path: Path, rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8", newline="") as fp:
        w = csv.DictWriter(fp, fieldnames=REQUIRED_MANIFEST_COLUMNS)
        w.writeheader()
        for r in rows:
            w.writerow({c: r.get(c, "") for c in REQUIRED_MANIFEST_COLUMNS})


def test_loop_produces_per_point_discovery(tmp_path: Path):
    run_cache.clear()
    run_cache.clear_frozen_cache()

    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()
    # doc1 available before T1; doc2 available only before T2.  Cameco and
    # Hydro One are rule-based company entities that survive the universe gate
    # and produce bipartite company<->concept edges.
    _write_doc(docs_dir, "d1.txt",
               "Cameco is exposed to uranium and datacenter power demand.")
    _write_doc(docs_dir, "d2.txt",
               "Hydro One is exposed to copper and datacenter power demand.")
    manifest = docs_dir / "source_manifest.csv"
    _write_manifest(
        manifest,
        [
            _manifest_row("n1", "CCO", "d1.txt", "2024-03-15"),
            _manifest_row("n2", "H", "d2.txt", "2024-05-15"),
        ],
    )

    run = runs.create_run(RunCreateRequest(as_of_date=T2, as_of_dates=[T1, T2]))
    rid = run.run_id

    result = discovery_panel.run_panel(
        rid,
        documents_dir=str(docs_dir),
        source_manifest_path=str(manifest),
    )
    assert result.points_run == [T1, T2]
    assert result.points_skipped == []

    # Per-point discovery artifacts exist and carry the correct as_of_date.
    comm1 = json.loads(
        (runs.discovery_point_dir(rid, T1) / "communities.json").read_text()
    )
    comm2 = json.loads(
        (runs.discovery_point_dir(rid, T2) / "communities.json").read_text()
    )
    assert comm1["as_of_date"] == T1
    assert comm2["as_of_date"] == T2

    # PIT isolation: T1 sees only doc1's entities; T2 sees doc1+doc2.  Beta only
    # becomes available at T2, so it must be absent from the T1 entity set.
    ents1 = {
        (e.get("canonical_name") or e.get("name") or "")
        for e in pq.read_table(
            runs.discovery_point_dir(rid, T1) / "entities.parquet"
        ).to_pylist()
    }
    ents2 = {
        (e.get("canonical_name") or e.get("name") or "")
        for e in pq.read_table(
            runs.discovery_point_dir(rid, T2) / "entities.parquet"
        ).to_pylist()
    }
    hydro_t1 = {e for e in ents1 if "hydro" in e.lower()}
    hydro_t2 = {e for e in ents2 if "hydro" in e.lower()}
    assert not hydro_t1, f"Hydro One must not be visible at T1 (PIT): {hydro_t1}"
    assert hydro_t2, "Hydro One must be visible at T2"
    assert len(ents2) > len(ents1)

    # Per-point exposure isolation: T1 has fewer company-theme pairs than T2.
    exp1 = pq.read_table(
        runs.discovery_point_dir(rid, T1) / "company_theme_exposure.parquet"
    ).num_rows
    exp2 = pq.read_table(
        runs.discovery_point_dir(rid, T2) / "company_theme_exposure.parquet"
    ).num_rows
    assert exp1 >= 1
    assert exp2 > exp1

    # Run-level flag flips once both points freeze.
    run_cache.clear_frozen_cache()
    m = runs.load_manifest(rid)
    assert m.discovery_frozen is True
    assert set(m.discovery_frozen_points or {}) == {T1, T2}

    # Panel artifacts were built.
    assert (runs.panel_dir(rid) / "theme_lineage.json").exists()
    assert (runs.panel_dir(rid) / "exposure_trajectories.parquet").exists()
    assert (runs.panel_dir(rid) / "panel_summary.json").exists()


def test_concept_spine_lineage_links_theme_across_points():
    run_cache.clear()
    run = runs.create_run(RunCreateRequest(as_of_date=T2, as_of_dates=[T1, T2]))
    rid = run.run_id

    # T1: one community with spine {ec1}, companies {c1, c2}.
    _seed_point(
        rid, T1,
        [_ent("c1", "Company"), _ent("c2", "Company"),
         _ent("ec1", "EconomicConcept")],
        [_edge("e1", "c1", "ec1", "exposed_to"),
         _edge("e2", "c2", "ec1", "exposed_to")],
    )
    # T2: a decoy community (eca) is seeded FIRST so the shared-spine community
    # lands at a DIFFERENT point-local index (community_001) than at T1
    # (community_000).  Company membership also changes (c2 -> c5) to prove the
    # link is by concept spine, not by company membership.
    _seed_point(
        rid, T2,
        [_ent("a3", "Company"), _ent("a4", "Company"),
         _ent("eca", "EconomicConcept"),
         _ent("c1", "Company"), _ent("c5", "Company"),
         _ent("ec1", "EconomicConcept")],
        [_edge("e3", "a3", "eca", "exposed_to"),
         _edge("e4", "a4", "eca", "exposed_to"),
         _edge("e1", "c1", "ec1", "exposed_to"),
         _edge("e5", "c5", "ec1", "exposed_to")],
    )

    discovery_panel.build_panel(rid)
    lineage = json.loads(
        (runs.panel_dir(rid) / "theme_lineage.json").read_text()
    )
    assert lineage["schema_version"] == "2.0"
    assert lineage["lineage_mode"] == "multi_point_concept_spine"
    assert lineage["method"] == "concept_spine_jaccard_v1"

    # Find the family whose spine is {ec1} and that spans both points.
    fam = next(
        f for f in lineage["families"]
        if f["concept_spine_union"] == ["ec1"]
        and {s["as_of_date"] for s in f["snapshots"]} == {T1, T2}
    )
    snaps_by_point = {s["as_of_date"]: s for s in fam["snapshots"]}
    # Point-local community_ids DIFFER across points, but the spine matches.
    assert snaps_by_point[T1]["community_id"] != snaps_by_point[T2]["community_id"]
    assert snaps_by_point[T1]["theme_snapshot_id"] != snaps_by_point[T2]["theme_snapshot_id"]
    assert fam["states_by_point"][T1] == "emerged"
    assert fam["states_by_point"][T2] == "persisted"

    # The T2 lineage record is a 'persisted' continuation with confidence >= tau.
    t2_rec = next(
        r for r in lineage["lineages"]
        if r["theme_family_id"] == fam["theme_family_id"] and r["as_of_date"] == T2
    )
    assert t2_rec["lifecycle_event"] == "persisted"
    assert t2_rec["confidence"] >= 0.5
    assert t2_rec["prior_theme_snapshot_ids"] == [snaps_by_point[T1]["theme_snapshot_id"]]


def test_exposure_trajectory_per_company():
    run_cache.clear()
    run = runs.create_run(RunCreateRequest(as_of_date=T2, as_of_dates=[T1, T2]))
    rid = run.run_id

    # Same spine {ec1} at both points; edge confidence changes so the per-company
    # exposure_score differs across points.
    _seed_point(
        rid, T1,
        [_ent("c1", "Company"), _ent("c2", "Company"),
         _ent("ec1", "EconomicConcept")],
        [_edge("e1", "c1", "ec1", "exposed_to", confidence="0.40"),
         _edge("e2", "c2", "ec1", "exposed_to", confidence="0.40")],
    )
    _seed_point(
        rid, T2,
        [_ent("c1", "Company"), _ent("c2", "Company"),
         _ent("ec1", "EconomicConcept")],
        [_edge("e1", "c1", "ec1", "exposed_to", confidence="0.95"),
         _edge("e2", "c2", "ec1", "exposed_to", confidence="0.95")],
    )

    discovery_panel.build_panel(rid)
    rows = pq.read_table(
        runs.panel_dir(rid) / "exposure_trajectories.parquet"
    ).to_pylist()

    # Group by (family, company) -> trajectory over as_of.
    from collections import defaultdict
    traj: dict[tuple, list[dict]] = defaultdict(list)
    for r in rows:
        traj[(r["theme_family_id"], r["company_id"])].append(r)

    # c1 must have exactly two ordered rows across the two points.
    c1_keys = [k for k in traj if k[1] == "c1"]
    assert len(c1_keys) == 1, f"c1 should map to a single family: {c1_keys}"
    c1_rows = sorted(traj[c1_keys[0]], key=lambda r: r["as_of_date"])
    assert [r["as_of_date"] for r in c1_rows] == [T1, T2]
    # Differing exposure_score across the trajectory.
    assert c1_rows[0]["exposure_score"] != c1_rows[1]["exposure_score"]
    # Rows are globally sorted by (family, company, as_of).
    sort_key = [(r["theme_family_id"], r["company_id"], r["as_of_date"]) for r in rows]
    assert sort_key == sorted(sort_key)
