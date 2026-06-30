"""OI-6 R2 — Multi-period theme-discovery panel driver + artifacts.

This module drives the multi-period per-point discovery loop and builds the
run-level cross-point ``panel/`` artifact set from the frozen per-point
``discovery/<as_of>/`` substrate produced by R1.

Three responsibilities:

1. ``run_panel`` — per-point loop over ``runs.list_as_of_points`` running the
   R1 discovery stages (1-11) with ``as_of=t_i`` (re-extract per point;
   rule-based when no LLM env), per-point freeze, then ``build_panel``.

2. ``build_panel`` — write the run-level panel artifacts:
   - ``panel/theme_lineage.json``       (company-membership lineage, schema 2.1)
   - ``panel/exposure_trajectories.parquet`` (per-company cross-point)
   - ``panel/panel_summary.json``       (cached summary read by the endpoint)

3. ``panel_summary`` — read-only ``PanelSummary`` (cached or recomputed live).

Lineage is the deterministic ``company_membership_jaccard_v2`` matcher over
SUBSTANTIVE themes only (``_is_substantive``: size >= 3 with >= 1 company).
Cross-point identity is the set of MEMBER COMPANIES: independent per-point
re-extraction makes concept ids/names drift (so concept-set Jaccard collapses),
but company membership is stable, so company overlap reliably links the same
theme across points. No randomness, no LLM.

Hermetic: with no LLM env the extraction stage uses the rule-based extractor
(``extraction.build_default_extractor``) — no network.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import pyarrow as pa
import pyarrow.parquet as pq

from . import (
    chunking,
    data_cleaning,
    data_import,
    entity_resolution,
    exposure as exposure_mod,
    extraction,
    freeze as freeze_mod,
    graph_build,
    provenance as provenance_mod,
    concept_resolution,
    runs,
    themes,
    theme_hierarchy as theme_hierarchy_mod,
)
from .exposure import EXPOSURE_COLUMNS

# Lineage constants (concept_spine_jaccard_v1)
LINEAGE_METHOD = "company_membership_jaccard_v2"
LINEAGE_MODE = "multi_point_company_membership"
LINEAGE_SCHEMA_VERSION = "2.1"
MATCH_THRESHOLD = 0.5  # tau (company-membership Jaccard)
REVIVE_THRESHOLD = 0.5  # tau_revive

# Substantive-theme gate. Raw communities.json contains many single-entity
# communities (Louvain singletons over a sparse graph) that swamp the lineage
# timeline and trajectories with one-off noise. The panel (lineage, trajectories,
# per-point theme_count) operates on SUBSTANTIVE themes only: a community that
# binds >= MIN_SUBSTANTIVE_SIZE nodes AND has at least one company member.
MIN_SUBSTANTIVE_SIZE = 3


def _is_substantive(comm: dict) -> bool:
    """True if a community is a real theme (not singleton noise)."""
    size = comm.get("size")
    if size is None:
        size = len(comm.get("node_ids", []))
    return size >= MIN_SUBSTANTIVE_SIZE and bool(comm.get("top_companies"))

TRAJECTORY_SCHEMA_VERSION = "1.0"
SUMMARY_SCHEMA_VERSION = "1.0"

# Column order for panel/exposure_trajectories.parquet (exact, per spec §3).
TRAJECTORY_COLUMNS: list[str] = [
    "schema_version",
    "run_id",
    "theme_family_id",
    "company_id",
    "ticker",
    "as_of_date",
    "community_id",
    "theme_snapshot_id",
    "exposure_score",
    "evidence_count",
    "lifecycle_event",
    "calculation_method",
]


@dataclass
class PanelResult:
    """Outcome of a ``run_panel`` invocation."""

    points_run: list[str] = field(default_factory=list)
    points_skipped: list[str] = field(default_factory=list)
    panel_paths: dict[str, str] = field(default_factory=dict)


# --------------------------------------------------------------------------- #
# 1. DRIVER                                                                    #
# --------------------------------------------------------------------------- #


def run_panel(
    run_id: str,
    *,
    documents_dir: str,
    source_manifest_path: str,
    include_weak_signals: bool = False,
    do_fact_extraction: bool = False,
    resume: bool = True,
    extractor=None,
) -> PanelResult:
    """Drive the multi-period per-point discovery loop, then build the panel.

    For each authored ``as_of`` point (ascending), run the R1 discovery stages
    with ``as_of=t_i`` and freeze that point.  When ``resume`` is True, points
    already recorded in ``manifest.discovery_frozen_points`` are skipped.  After
    the loop, ``build_panel`` writes the run-level panel artifacts.

    Re-extraction per point preserves point-in-time discipline: each point sees
    only documents available at <= t_i (the data_import gate is PIT-to-t_i).
    """
    manifest = runs.load_manifest(run_id)
    if manifest is None:
        raise ValueError(f"run not found: {run_id}")

    points = runs.list_as_of_points(run_id)
    if not points:
        raise ValueError(f"run has no as_of points: {run_id}")

    frozen_points = set((manifest.discovery_frozen_points or {}).keys())

    points_run: list[str] = []
    points_skipped: list[str] = []

    for t_i in points:
        if resume and t_i in frozen_points:
            points_skipped.append(t_i)
            print(f"[panel] skip frozen point {t_i}", flush=True)
            continue

        print(f"[panel] === point {t_i} ===", flush=True)

        # R1 stages 1-11, threaded with as_of=t_i.
        data_import.import_manifest(
            run_id, documents_dir, source_manifest_path, as_of=t_i
        )
        data_cleaning.clean_documents(run_id, as_of=t_i)
        chunking.chunk_documents(run_id, as_of=t_i)
        extraction.run_extraction(run_id, extractor=extractor, as_of=t_i)
        if do_fact_extraction:
            extraction.run_fact_extraction(run_id, as_of=t_i)
        entity_resolution.resolve_entities(run_id, as_of=t_i)
        concept_resolution.canonicalize_concepts(run_id, as_of=t_i)
        graph_build.build_graph(run_id, as_of=t_i)
        themes.discover_themes(run_id, as_of=t_i)
        exposure_mod.compute_exposure(
            run_id, include_weak_signals=include_weak_signals, as_of=t_i
        )
        # Main-theme hierarchy (LLM grouping into <=7 main themes) — built BEFORE
        # freeze so the frontend landing has main themes for every point. Gated on
        # an LLM being configured and best-effort, so the loop stays hermetic in
        # tests (no LLM_API_KEY -> skipped instantly, no network).
        if os.environ.get("LLM_API_KEY"):
            try:
                theme_hierarchy_mod.build_hierarchy(run_id, as_of=t_i)
            except Exception as exc:  # noqa: BLE001 - hierarchy is optional
                print(f"[panel] hierarchy skipped for {t_i}: {exc}", flush=True)
        provenance_mod.materialize_provenance(run_id, as_of=t_i)
        freeze_mod.freeze_discovery(run_id, as_of=t_i)

        points_run.append(t_i)
        print(f"[panel] froze point {t_i}", flush=True)

    panel_paths = build_panel(run_id)
    print(f"[panel] built panel: {sorted(panel_paths)}", flush=True)

    return PanelResult(
        points_run=points_run,
        points_skipped=points_skipped,
        panel_paths=panel_paths,
    )


# --------------------------------------------------------------------------- #
# Artifact readers                                                             #
# --------------------------------------------------------------------------- #


def _read_communities(run_id: str, as_of: str) -> Optional[dict]:
    p = runs.discovery_point_dir(run_id, as_of) / "communities.json"
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


def _read_snapshots(run_id: str, as_of: str) -> dict[str, str]:
    """Return a map community_id -> theme_snapshot_id for the given point."""
    p = runs.discovery_point_dir(run_id, as_of) / "theme_snapshots.json"
    if not p.exists():
        return {}
    doc = json.loads(p.read_text(encoding="utf-8"))
    return {
        s["community_id"]: s["theme_snapshot_id"]
        for s in doc.get("snapshots", [])
        if s.get("community_id")
    }


def _read_exposure_rows(run_id: str, as_of: str) -> list[dict]:
    p = runs.discovery_point_dir(run_id, as_of) / "company_theme_exposure.parquet"
    if not p.exists():
        return []
    return pq.read_table(p).to_pylist()


def _present_points(run_id: str) -> list[str]:
    """Authored points that have a communities.json on disk (ascending)."""
    return [
        p for p in runs.list_as_of_points(run_id)
        if (runs.discovery_point_dir(run_id, p) / "communities.json").exists()
    ]


# --------------------------------------------------------------------------- #
# 4. LINEAGE — concept_spine_jaccard_v1                                        #
# --------------------------------------------------------------------------- #


def _jaccard(a: frozenset, b: frozenset) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    if inter == 0:
        return 0.0
    union = len(a | b)
    return inter / union if union else 0.0


class _DSU:
    """Deterministic union-find over hashable node keys."""

    def __init__(self) -> None:
        self.parent: dict = {}

    def add(self, x) -> None:
        self.parent.setdefault(x, x)

    def find(self, x):
        self.add(x)
        root = x
        while self.parent[root] != root:
            root = self.parent[root]
        # path compression
        while self.parent[x] != root:
            self.parent[x], x = root, self.parent[x]
        return root

    def union(self, a, b) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra == rb:
            return
        # Keep the smaller key as root for determinism.
        if rb < ra:
            ra, rb = rb, ra
        self.parent[rb] = ra


def _spine_lineage(run_id: str) -> dict:
    """Compute the concept_spine_jaccard_v1 cross-point lineage.

    Returns a dict with keys: schema_version, run_id, lineage_mode, method,
    match_threshold, points, families, lineages, plus internal helper maps:
      _family_by_node : (as_of, community_id) -> theme_family_id
      _lineage_by_node: (as_of, community_id) -> lineage record
    """
    points = _present_points(run_id)
    point_index = {p: i for i, p in enumerate(points)}

    # Build community nodes per present point.
    # node_key = (point_idx, community_id); node carries spine + snapshot + name.
    nodes: dict[tuple[int, str], dict] = {}
    communities_at_point: dict[int, list[str]] = {}  # point_idx -> sorted cids

    for p in points:
        i = point_index[p]
        comm_doc = _read_communities(run_id, p) or {}
        snap_map = _read_snapshots(run_id, p)
        cids: list[str] = []
        for comm in comm_doc.get("communities", []):
            cid = comm.get("community_id")
            if not cid:
                continue
            if not _is_substantive(comm):
                continue  # skip singleton/noise communities
            # Cross-point identity = COMPANY membership. Independent per-point
            # re-extraction makes concept ids/names drift (diluting concept-set
            # Jaccard), but the set of member companies is stable, so company
            # overlap is what reliably links the same theme across points.
            spine = frozenset(
                str(c).strip().lower() for c in comm.get("top_companies", []) if c
            )
            nodes[(i, cid)] = {
                "point_idx": i,
                "as_of": p,
                "community_id": cid,
                "snapshot_id": snap_map.get(cid, ""),
                "spine": spine,
                "theme_name": comm.get("theme_name", cid),
            }
            cids.append(cid)
        communities_at_point[i] = sorted(cids)

    # ---- STEP 1: consecutive-present-point spine Jaccard links ----
    # link_j[(a_key, b_key)] = J ; parents[b_key] = [a_key,...] ; children[a_key]=[b_key,...]
    link_j: dict[tuple, float] = {}
    parents: dict[tuple, list[tuple]] = {}
    children: dict[tuple, list[tuple]] = {}

    for idx in range(len(points) - 1):
        a_cids = communities_at_point.get(idx, [])
        b_cids = communities_at_point.get(idx + 1, [])
        for a_cid in a_cids:  # already sorted -> deterministic
            a_key = (idx, a_cid)
            spine_a = nodes[a_key]["spine"]
            if not spine_a:
                continue
            for b_cid in b_cids:
                b_key = (idx + 1, b_cid)
                spine_b = nodes[b_key]["spine"]
                if not spine_b:
                    continue
                inter = len(spine_a & spine_b)
                if inter == 0:
                    continue
                j = inter / len(spine_a | spine_b)
                if j >= MATCH_THRESHOLD:
                    link_j[(a_key, b_key)] = j
                    parents.setdefault(b_key, []).append(a_key)
                    children.setdefault(a_key, []).append(b_key)

    # ---- STEP 2: connected components -> preliminary families ----
    dsu = _DSU()
    for key in nodes:
        dsu.add(key)
    for (a_key, b_key) in link_j:
        dsu.union(a_key, b_key)

    def _components() -> dict[tuple, list[tuple]]:
        comps: dict[tuple, list[tuple]] = {}
        for key in nodes:
            comps.setdefault(dsu.find(key), []).append(key)
        return comps

    def _family_point_span(members: list[tuple]) -> tuple[int, int]:
        idxs = [m[0] for m in members]
        return min(idxs), max(idxs)

    def _spine_at(members: list[tuple], point_idx: int) -> frozenset:
        out: set = set()
        for m in members:
            if m[0] == point_idx:
                out |= nodes[m]["spine"]
        return frozenset(out)

    # ---- STEP 3: cross-gap revival merges ----
    revived_nodes: set[tuple] = set()
    comps = _components()
    fam_list = list(comps.values())

    def _fam_meta(members: list[tuple]) -> dict:
        first_idx, last_idx = _family_point_span(members)
        return {
            "members": members,
            "first_idx": first_idx,
            "last_idx": last_idx,
            "spine_first": _spine_at(members, first_idx),
            "spine_last": _spine_at(members, last_idx),
            "min_snapshot": min(
                (nodes[m]["snapshot_id"] for m in members), default=""
            ),
        }

    metas = [_fam_meta(m) for m in fam_list]

    # Enumerate candidate (F, G) gap pairs where G first appears at a true gap
    # after F's last point.
    candidates: list[tuple] = []
    for f in metas:
        for g in metas:
            if f is g:
                continue
            if g["first_idx"] > f["last_idx"] + 1:  # true gap
                j = _jaccard(f["spine_last"], g["spine_first"])
                if j >= REVIVE_THRESHOLD:
                    candidates.append(
                        (
                            f["last_idx"], g["first_idx"], f["min_snapshot"],
                            f, g,
                        )
                    )
    candidates.sort(key=lambda c: (c[0], c[1], c[2]))

    for _li, _fi, _ms, f, g in candidates:
        # Union representative members; mark G's first-appearance as revived.
        dsu.union(f["members"][0], g["members"][0])
        for m in g["members"]:
            if m[0] == g["first_idx"]:
                revived_nodes.add(m)

    # ---- Recompute final families after revival merges ----
    comps = _components()

    # Assign theme_family_id by content hash of sorted member snapshot ids.
    family_id_by_root: dict[tuple, str] = {}
    for root, members in comps.items():
        snap_ids = sorted(nodes[m]["snapshot_id"] for m in members)
        basis = "family:" + run_id + ":" + "|".join(snap_ids)
        fid = "family_" + hashlib.sha256(basis.encode("utf-8")).hexdigest()[:8]
        family_id_by_root[root] = fid

    family_by_node: dict[tuple, str] = {
        key: family_id_by_root[dsu.find(key)] for key in nodes
    }

    # Family first/last present idx (for dormant/absent + revival detection).
    fam_member_idxs: dict[str, set[int]] = {}
    for key in nodes:
        fam_member_idxs.setdefault(family_by_node[key], set()).add(key[0])

    # ---- STEP 4: lifecycle per present community ----
    lineages: list[dict] = []
    lineage_by_node: dict[tuple, dict] = {}

    for key in sorted(nodes, key=lambda k: (k[0], k[1])):
        node = nodes[key]
        fid = family_by_node[key]
        fam_idxs = fam_member_idxs[fid]
        first_idx = min(fam_idxs)
        par = sorted(parents.get(key, []), key=lambda k: (k[0], k[1]))

        if not par:
            if key in revived_nodes or (node["point_idx"] > first_idx):
                event = "revived"
                confidence = 1.0
            else:
                event = "emerged"
                confidence = 1.0
        elif len(par) >= 2:
            event = "merged"
            confidence = max(link_j[(a, key)] for a in par)
        else:
            a = par[0]
            if len(children.get(a, [])) >= 2:
                event = "split"
            else:
                event = "persisted"
            confidence = link_j[(a, key)]

        prior_snaps = sorted(nodes[a]["snapshot_id"] for a in par)
        rec = {
            "theme_family_id": fid,
            "as_of_date": node["as_of"],
            "current_theme_snapshot_id": node["snapshot_id"],
            "current_community_id": node["community_id"],
            "prior_theme_snapshot_ids": prior_snaps,
            "lifecycle_event": event,
            "confidence": round(float(confidence), 6),
            "method": LINEAGE_METHOD,
        }
        lineages.append(rec)
        lineage_by_node[key] = rec

    # ---- Build families[] records ----
    authored = runs.list_as_of_points(run_id)
    families: list[dict] = []
    for root, members in comps.items():
        fid = family_id_by_root[root]
        members_sorted = sorted(members, key=lambda k: (k[0], k[1]))
        present_idxs = sorted({m[0] for m in members})
        first_idx, last_idx = present_idxs[0], present_idxs[-1]
        first_seen = points[first_idx]
        last_seen = points[last_idx]

        member_companies: set = set()
        for m in members:
            member_companies |= nodes[m]["spine"]

        # theme_name: label at last present point (member with min community_id).
        last_members = sorted(
            (m for m in members if m[0] == last_idx), key=lambda k: k[1]
        )
        theme_name = nodes[last_members[0]]["theme_name"] if last_members else fid

        # states_by_point over ALL authored points.
        first_authored = authored.index(first_seen)
        last_authored = authored.index(last_seen)
        present_authored = {nodes[m]["as_of"] for m in members}
        states: dict[str, str] = {}
        for a_idx, ap in enumerate(authored):
            if ap in present_authored:
                # representative event: member at this point with min community_id
                pt_members = sorted(
                    (m for m in members if nodes[m]["as_of"] == ap),
                    key=lambda k: k[1],
                )
                states[ap] = lineage_by_node[pt_members[0]]["lifecycle_event"]
            elif first_authored < a_idx < last_authored:
                states[ap] = "dormant"
            else:
                states[ap] = "absent"

        snapshots = [
            {
                "as_of_date": nodes[m]["as_of"],
                "theme_snapshot_id": nodes[m]["snapshot_id"],
                "community_id": nodes[m]["community_id"],
            }
            for m in members_sorted
        ]
        snapshots.sort(key=lambda s: (s["as_of_date"], s["community_id"]))

        families.append(
            {
                "theme_family_id": fid,
                "theme_name": theme_name,
                "first_seen": first_seen,
                "last_seen": last_seen,
                "member_companies": sorted(member_companies),
                "states_by_point": states,
                "snapshots": snapshots,
            }
        )

    families.sort(key=lambda f: (f["first_seen"], f["theme_family_id"]))
    lineages.sort(key=lambda r: (r["as_of_date"], r["theme_family_id"],
                                 r["current_community_id"]))

    return {
        "schema_version": LINEAGE_SCHEMA_VERSION,
        "run_id": run_id,
        "lineage_mode": LINEAGE_MODE,
        "method": LINEAGE_METHOD,
        "match_threshold": MATCH_THRESHOLD,
        "points": points,
        "families": families,
        "lineages": lineages,
        "_family_by_node": family_by_node,
        "_lineage_by_node": lineage_by_node,
        "_nodes": nodes,
    }


# --------------------------------------------------------------------------- #
# 3. PANEL ARTIFACTS                                                           #
# --------------------------------------------------------------------------- #


def _build_trajectories(run_id: str, lineage: dict) -> list[dict]:
    """Rows for panel/exposure_trajectories.parquet (one per family+company+point)."""
    family_by_node: dict[tuple, str] = lineage["_family_by_node"]
    lineage_by_node: dict[tuple, dict] = lineage["_lineage_by_node"]
    nodes: dict = lineage["_nodes"]
    point_index = {p: i for i, p in enumerate(lineage["points"])}

    rows: list[dict] = []
    for as_of in lineage["points"]:
        i = point_index[as_of]
        for er in _read_exposure_rows(run_id, as_of):
            cid = er.get("community_id") or ""
            key = (i, cid)
            fid = family_by_node.get(key)
            lin = lineage_by_node.get(key)
            if fid is None:
                # Community not in the substantive lineage (singleton/noise) —
                # excluded from trajectories so the panel tracks real themes only.
                continue
            lifecycle = lin["lifecycle_event"] if lin else "emerged"
            rows.append(
                {
                    "schema_version": TRAJECTORY_SCHEMA_VERSION,
                    "run_id": run_id,
                    "theme_family_id": fid,
                    "company_id": er.get("company_id") or "",
                    "ticker": er.get("ticker"),
                    "as_of_date": as_of,
                    "community_id": cid,
                    "theme_snapshot_id": er.get("theme_snapshot_id") or "",
                    "exposure_score": float(er.get("exposure_score") or 0.0),
                    "evidence_count": int(er.get("evidence_count") or 0),
                    "lifecycle_event": lifecycle,
                    "calculation_method": er.get("calculation_method") or "",
                }
            )

    rows.sort(key=lambda r: (r["theme_family_id"], r["company_id"], r["as_of_date"]))
    return rows


def _write_trajectories(rows: list[dict], out_path: Path) -> None:
    schema = pa.schema(
        [
            ("schema_version", pa.string()),
            ("run_id", pa.string()),
            ("theme_family_id", pa.string()),
            ("company_id", pa.string()),
            ("ticker", pa.string()),
            ("as_of_date", pa.string()),
            ("community_id", pa.string()),
            ("theme_snapshot_id", pa.string()),
            ("exposure_score", pa.float64()),
            ("evidence_count", pa.int64()),
            ("lifecycle_event", pa.string()),
            ("calculation_method", pa.string()),
        ]
    )
    cols = {c: [r.get(c) for r in rows] for c in TRAJECTORY_COLUMNS}
    pq.write_table(pa.table(cols, schema=schema), out_path)


def _lineage_summary(lineage: dict) -> dict:
    counts = {
        "family_count": len(lineage["families"]),
        "emerged": 0,
        "persisted": 0,
        "split": 0,
        "merged": 0,
        "revived": 0,
        "dormant": 0,
    }
    for rec in lineage["lineages"]:
        ev = rec["lifecycle_event"]
        if ev in counts:
            counts[ev] += 1
    for fam in lineage["families"]:
        for st in fam["states_by_point"].values():
            if st == "dormant":
                counts["dormant"] += 1
    return counts


def _compute_points_summary(run_id: str, manifest) -> list[dict]:
    frozen_points = manifest.discovery_frozen_points or {}
    out: list[dict] = []
    for p in runs.list_as_of_points(run_id):
        comm_doc = _read_communities(run_id, p)
        present = comm_doc is not None
        theme_count = (
            sum(1 for c in comm_doc.get("communities", []) if _is_substantive(c))
            if present else 0
        )
        pair_count = len(_read_exposure_rows(run_id, p))
        out.append(
            {
                "as_of": p,
                "discovery_present": present,
                "discovery_frozen": p in frozen_points,
                "theme_count": theme_count,
                "company_theme_pair_count": pair_count,
            }
        )
    return out


def build_panel(run_id: str) -> dict[str, str]:
    """Build the run-level panel artifacts from frozen per-point discovery.

    Writes panel/theme_lineage.json, panel/exposure_trajectories.parquet and
    panel/panel_summary.json.  The panel is derived & regenerable; NOT frozen.

    Returns a dict of artifact-name -> path string.
    """
    manifest = runs.load_manifest(run_id)
    if manifest is None:
        raise ValueError(f"run not found: {run_id}")

    pdir = runs.panel_dir(run_id, for_write=True)
    lineage = _spine_lineage(run_id)

    # --- theme_lineage.json (strip internal helper maps) ---
    lineage_doc = {
        k: v for k, v in lineage.items() if not k.startswith("_")
    }
    lineage_path = pdir / "theme_lineage.json"
    lineage_path.write_text(json.dumps(lineage_doc, indent=2), encoding="utf-8")

    # --- exposure_trajectories.parquet ---
    rows = _build_trajectories(run_id, lineage)
    traj_path = pdir / "exposure_trajectories.parquet"
    _write_trajectories(rows, traj_path)
    company_count = len({r["company_id"] for r in rows})

    # --- panel_summary.json ---
    summary = {
        "schema_version": SUMMARY_SCHEMA_VERSION,
        "run_id": run_id,
        "as_of_dates": runs.list_as_of_points(run_id),
        "discovery_frozen": bool(manifest.discovery_frozen),
        "frozen_at": manifest.frozen_at,
        "panel_built": True,
        "points": _compute_points_summary(run_id, manifest),
        "theme_lineage_summary": _lineage_summary(lineage),
        "exposure_trajectory_company_count": company_count,
    }
    summary_path = pdir / "panel_summary.json"
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    return {
        "theme_lineage.json": str(lineage_path),
        "exposure_trajectories.parquet": str(traj_path),
        "panel_summary.json": str(summary_path),
    }


# --------------------------------------------------------------------------- #
# 5. SUMMARY (read-only)                                                       #
# --------------------------------------------------------------------------- #


def panel_summary(run_id: str) -> dict:
    """Return the PanelSummary shape for *run_id* (cached or recomputed live).

    Reads the cached panel/panel_summary.json when present; otherwise recomputes
    live from the run manifest and per-point discovery artifacts (panel_built
    reflects whether the cached artifact exists).  Raises ValueError if the run
    does not exist.
    """
    manifest = runs.load_manifest(run_id)
    if manifest is None:
        raise ValueError(f"run not found: {run_id}")

    cached = runs.panel_dir(run_id) / "panel_summary.json"
    if cached.exists():
        return json.loads(cached.read_text(encoding="utf-8"))

    # Live recompute (panel not built).
    lineage = _spine_lineage(run_id)
    company_count = len(
        {
            er.get("company_id") or ""
            for p in lineage["points"]
            for er in _read_exposure_rows(run_id, p)
        }
    )
    return {
        "schema_version": SUMMARY_SCHEMA_VERSION,
        "run_id": run_id,
        "as_of_dates": runs.list_as_of_points(run_id),
        "discovery_frozen": bool(manifest.discovery_frozen),
        "frozen_at": manifest.frozen_at,
        "panel_built": False,
        "points": _compute_points_summary(run_id, manifest),
        "theme_lineage_summary": _lineage_summary(lineage),
        "exposure_trajectory_company_count": company_count,
    }
