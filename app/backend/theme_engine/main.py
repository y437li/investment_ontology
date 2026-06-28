"""FastAPI application: Milestone 1 run lifecycle.

Exposes the run endpoints from spec section 3. Later milestones add the
data/extraction/graph/exposure/validation/report routers under the same app.
"""

from __future__ import annotations

from fastapi import FastAPI, HTTPException

from . import artifacts as artifacts_mod, chunking, data_cleaning, data_import, extraction, entity_resolution, exposure as exposure_mod, freeze as freeze_mod, graph_build, macro_adapter, altdata_adapter, concept_resolution, subgraph as subgraph_mod, slice_engine, source as source_mod, walk_forward as walk_forward_mod, node_explanation as node_explanation_mod, reasoning as reasoning_mod, report as report_mod, runs, theme_hierarchy as theme_hierarchy_mod, theme_levels as theme_levels_mod, theme_relevance as theme_relevance_mod, themes, validation as validation_mod, provenance as provenance_mod
from .models import (
    DataImportRequest,
    DataImportResponse,
    DataCleanRequest,
    DataCleanResponse,
    DataChunkRequest,
    DataChunkResponse,
    RunCreateRequest,
    FreezeRequest,
    FreezeResponse,
    ValidationRunRequest,
    ValidationRunResponse,
    RunManifest,
    RunStatus,
    ExtractionRunRequest,
    ExtractionRunResponse,
    ExtractionResolveRequest,
    ExtractionResolveResponse,
    GraphBuildRequest,
    GraphBuildResponse,
    ThemeDiscoverRequest,
    ThemeDiscoverResponse,
    ExposureComputeRequest,
    ExposureComputeResponse,
    ReportGenerateRequest,
    ReportGenerateResponse,
)

app = FastAPI(title="Theme Discovery Engine", version="0.1.0")


def _guard_not_frozen(run_id: str) -> None:
    """Reject mutating a run's discovery artifacts after freeze (audit HIGH)."""
    m = runs.load_manifest(run_id)
    if m is not None and getattr(m, "discovery_frozen", False):
        raise HTTPException(status_code=409,
            detail=f"discovery is frozen for {run_id}; cannot regenerate discovery artifacts")


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/api/artifacts/{run_id}/{artifact_name:path}")
def get_artifact(run_id: str, artifact_name: str):
    """Serve an allowlisted run artifact.

    Artifact names in the allowlist:
      graph.json, communities.json, theme_snapshots.json, theme_lineage.json,
      report.md, theme_metrics.parquet, company_theme_exposure.parquet,
      validation/validation.csv

    Parquet files are returned as JSON records (list of objects).
    JSON/Markdown files are returned as-is with appropriate Content-Type.
    CSV under validation/ is returned as JSON records.

    Security:
      - Only allowlisted names are served; others get 400.
      - Path traversal ('..') or absolute paths get 400.
      - Missing run or artifact gets 404.
    """
    return artifacts_mod.serve_artifact(run_id=run_id, artifact_name=artifact_name)


@app.get("/api/runs")
def list_runs_endpoint() -> list[dict]:
    """List all runs on disk (newest first) for the Home run history."""
    return runs.list_runs()


@app.post("/api/runs/create", response_model=RunManifest)
def create_run(req: RunCreateRequest) -> RunManifest:
    return runs.create_run(req)


@app.get("/api/runs/{run_id}/status", response_model=RunStatus)
def run_status(run_id: str) -> RunStatus:
    status = runs.get_status(run_id)
    if status is None:
        raise HTTPException(status_code=404, detail=f"run not found: {run_id}")
    return status


@app.post("/api/data/import", response_model=DataImportResponse)
def import_data(req: DataImportRequest) -> DataImportResponse:
    raw_documents, quarantined, quarantine_reasons = data_import.import_manifest(
        run_id=req.run_id,
        documents_dir=req.documents_dir,
        source_manifest_path=req.source_manifest_path,
    )
    return DataImportResponse(
        success=True,
        run_id=req.run_id,
        artifacts=["discovery/raw_documents.parquet"],
        raw_documents=raw_documents,
        quarantined=quarantined,
        quarantine_reasons=quarantine_reasons,
    )


@app.post("/api/data/clean", response_model=DataCleanResponse)
def clean_data(req: DataCleanRequest) -> DataCleanResponse:
    included, quarantined, quarantine_reasons = data_cleaning.clean_documents(
        run_id=req.run_id,
        documents_dir=req.documents_dir,
    )
    return DataCleanResponse(
        success=True,
        run_id=req.run_id,
        artifacts=[
            "discovery/documents.parquet",
            "discovery/document_cleaning_log.parquet",
        ],
        included_documents=included,
        quarantined_documents=quarantined,
        quarantine_reasons=quarantine_reasons,
    )


@app.post("/api/data/chunk", response_model=DataChunkResponse)
def chunk_data(req: DataChunkRequest) -> DataChunkResponse:
    chunk_count = chunking.chunk_documents(run_id=req.run_id)
    return DataChunkResponse(
        success=True,
        run_id=req.run_id,
        artifacts=["discovery/chunks.parquet"],
        chunk_count=chunk_count,
    )


@app.post("/api/extraction/run", response_model=ExtractionRunResponse)
def extraction_run(req: ExtractionRunRequest) -> ExtractionRunResponse:
    entity_count, edge_count = extraction.run_extraction(run_id=req.run_id)
    return ExtractionRunResponse(
        success=True,
        run_id=req.run_id,
        artifacts=[
            "discovery/entities.parquet",
            "discovery/edges.parquet",
            "discovery/edge_explanations.parquet",
        ],
        entity_count=entity_count,
        edge_count=edge_count,
    )


@app.post("/api/extraction/resolve", response_model=ExtractionResolveResponse)
def extraction_resolve(req: ExtractionResolveRequest) -> ExtractionResolveResponse:
    alias_count = entity_resolution.resolve_entities(run_id=req.run_id)
    return ExtractionResolveResponse(
        success=True,
        run_id=req.run_id,
        artifacts=["discovery/entity_aliases.parquet"],
        alias_count=alias_count,
    )


@app.post("/api/extraction/canonicalize-concepts")
def canonicalize_concepts_endpoint(req: GraphBuildRequest):
    """Merge synonym concept/event nodes (run after extraction, before graph/build).
    No-op without an LLM configured. Blocked once discovery is frozen."""
    _guard_not_frozen(req.run_id)
    try:
        return concept_resolution.canonicalize_concepts(req.run_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except (FileNotFoundError, RuntimeError) as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@app.post("/api/macro/integrate")
def macro_integrate(req: GraphBuildRequest):
    """Integrate point-in-time macro series as MacroIndicator nodes + structural
    edges to sensitive-sector companies. Run after extraction/resolve, before graph/build."""
    _guard_not_frozen(req.run_id)
    try:
        return macro_adapter.integrate_macro(req.run_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except (FileNotFoundError, RuntimeError) as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@app.post("/api/altdata/integrate")
def altdata_integrate(req: GraphBuildRequest):
    """Integrate alt/structured-data series (configs/altdata.yml) as PIT nodes +
    structural edges to sensitive-sector companies. After extraction, before graph/build."""
    _guard_not_frozen(req.run_id)
    try:
        return altdata_adapter.integrate_altdata(req.run_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except (FileNotFoundError, RuntimeError) as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@app.post("/api/graph/build", response_model=GraphBuildResponse)
def graph_build_endpoint(req: GraphBuildRequest) -> GraphBuildResponse:
    node_count, edge_count = graph_build.build_graph(run_id=req.run_id)
    return GraphBuildResponse(
        success=True,
        artifacts=["discovery/graph.json"],
        node_count=node_count,
        edge_count=edge_count,
    )


@app.post("/api/themes/discover", response_model=ThemeDiscoverResponse)
def themes_discover_endpoint(req: ThemeDiscoverRequest) -> ThemeDiscoverResponse:
    _guard_not_frozen(req.run_id)
    community_count = themes.discover_themes(run_id=req.run_id)
    return ThemeDiscoverResponse(
        success=True,
        artifacts=[
            "discovery/communities.json",
            "discovery/theme_snapshots.json",
            "discovery/theme_lineage.json",
            "discovery/theme_metrics.parquet",
        ],
        community_count=community_count,
    )


@app.get("/api/themes/{run_id}/hierarchy")
def get_theme_hierarchy(run_id: str):
    """Main-theme hierarchy (macro->industry->company->idiosyncratic grouping)."""
    h = theme_hierarchy_mod.load_hierarchy(run_id)
    if h is None:
        raise HTTPException(status_code=404, detail="theme hierarchy not built; POST .../hierarchy/build")
    return h


@app.post("/api/themes/{run_id}/hierarchy/build")
def build_theme_hierarchy(run_id: str):
    """Group sub-themes into main themes (LLM). Requires LLM config."""
    try:
        return theme_hierarchy_mod.build_hierarchy(run_id)
    except KeyError:
        raise HTTPException(status_code=503, detail="LLM not configured (set LLM_API_KEY/BASE_URL/MODEL)")


@app.get("/api/themes/{run_id}/main-narrative")
def get_main_narrative(run_id: str, communities: str = "", refresh: bool = False):
    """One STORY for a whole main theme: connect-the-dots narrative + ordered 推演
    over the union of the given communities. `communities` = comma-separated ids."""
    ids = [c for c in communities.split(",") if c]
    if not ids:
        raise HTTPException(status_code=400, detail="provide ?communities=cid1,cid2,...")
    try:
        return reasoning_mod.synthesize_main_narrative(run_id, ids, refresh=refresh)
    except KeyError:
        raise HTTPException(status_code=503, detail="LLM not configured (set LLM_API_KEY/BASE_URL/MODEL)")


@app.get("/api/themes/{run_id}/communities/{community_id}/narrative")
def get_theme_narrative(run_id: str, community_id: str, refresh: bool = False):
    """Connect-the-dots narrative + captured reasoning chain for a community (cached)."""
    try:
        return reasoning_mod.get_or_synthesize(run_id, community_id, refresh=refresh)
    except KeyError:
        raise HTTPException(status_code=503, detail="LLM not configured (set LLM_API_KEY/BASE_URL/MODEL)")
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@app.get("/api/themes/{run_id}/slice")
def get_slice(run_id: str, anchor: str = "", depth: int = 2, direction: str = "both",
              edge_types: str = "", levels: str = "", methods: str = "",
              min_weight: float = 0.0, max_nodes: int = 200):
    """Anchored slice: the connected structural subgraph reachable from an anchor node
    (entity_id or name) within `depth` hops along selected edge types/levels."""
    if not anchor or not anchor.strip():
        raise HTTPException(status_code=400, detail="provide ?anchor=<entity_id or name>")
    et = [c for c in edge_types.split(",") if c.strip()] or None
    lv = [c for c in levels.split(",") if c.strip()] or None
    mth = [c for c in methods.split(",") if c.strip()] or None
    try:
        return slice_engine.extract_slice(run_id, anchor.strip(), depth=depth, direction=direction,
            edge_types=et, levels=lv, extraction_methods=mth, min_weight=min_weight, max_nodes=max_nodes)
    except slice_engine.AnchorAmbiguous as exc:
        raise HTTPException(status_code=409, detail={"message": str(exc), "candidates": getattr(exc, "candidates", [])})
    except slice_engine.AnchorNotFound as exc:
        raise HTTPException(status_code=404, detail={"message": str(exc), "candidates": getattr(exc, "candidates", [])})
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.get("/api/themes/{run_id}/chunks/{chunk_id}")
def get_chunk_source(run_id: str, chunk_id: str):
    """Full-text source for an evidence chunk: full chunk + whole source document + attribution."""
    try:
        return source_mod.chunk_source(run_id, chunk_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@app.get("/api/themes/{run_id}/subgraph")
def get_subgraph(run_id: str, communities: str = ""):
    """Union structural subgraph for a set of communities (a whole main theme).
    `communities` is a comma-separated list of community_ids."""
    ids = [c for c in communities.split(",") if c]
    if not ids:
        raise HTTPException(status_code=400, detail="provide ?communities=cid1,cid2,...")
    try:
        return subgraph_mod.community_subgraph(run_id, ids)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@app.get("/api/themes/{run_id}/levels")
def get_theme_levels(run_id: str):
    """Per-theme factor-level composition (macro/industry/company/idiosyncratic) +
    substantive flag, so themes can be filtered by level and 0-metric noise hidden."""
    try:
        return theme_levels_mod.compute_levels(run_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@app.get("/api/themes/{run_id}/trajectories")
def get_trajectories(run_id: str):
    """Monthly walk-forward (§12): each theme's size trajectory over month-end PIT
    snapshots + emergence month + momentum. Deterministic (no LLM)."""
    try:
        return walk_forward_mod.theme_trajectories(run_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@app.get("/api/themes/{run_id}/relevance")
def get_theme_relevance(run_id: str, window_days: int = 90):
    """Temporal relevance/state per theme at as_of (recency of evidence). Deterministic."""
    try:
        return theme_relevance_mod.compute_relevance(run_id, window_days=window_days)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@app.get("/api/themes/{run_id}/nodes/{entity_id}/profile")
def get_node_profile(run_id: str, entity_id: str):
    """Node Explanation (§13): what it is, why it's in the graph, why it matters (deterministic)."""
    try:
        return node_explanation_mod.node_profile(run_id, entity_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@app.get("/api/themes/{run_id}/nodes/{entity_id}/explain")
def explain_node_endpoint(run_id: str, entity_id: str, refresh: bool = False):
    """Node profile + an optional cached LLM prose explanation (requires LLM config)."""
    try:
        return node_explanation_mod.explain_node(run_id, entity_id, refresh=refresh)
    except KeyError:
        raise HTTPException(status_code=503, detail="LLM not configured (set LLM_API_KEY/BASE_URL/MODEL)")
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@app.post("/api/exposure/compute", response_model=ExposureComputeResponse)
def exposure_compute(req: ExposureComputeRequest) -> ExposureComputeResponse:
    """Compute company-theme exposure scores (M5, io_contracts §18)."""
    _guard_not_frozen(req.run_id)
    try:
        pair_count = exposure_mod.compute_exposure(
            run_id=req.run_id,
            include_weak_signals=req.include_weak_signals,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    # Count unique themes in the output artifact
    run_dir = runs.get_run_dir(req.run_id)
    theme_count = 0
    exposure_path = run_dir / "discovery" / "company_theme_exposure.parquet"
    if exposure_path.exists():
        import pyarrow.parquet as pq  # noqa: PLC0415
        tbl = pq.read_table(exposure_path)
        if tbl.num_rows > 0:
            import pyarrow.compute as pc  # noqa: PLC0415
            community_col = tbl.column("community_id")
            theme_count = len(pc.unique(community_col).to_pylist())

    return ExposureComputeResponse(
        success=True,
        artifacts=["discovery/company_theme_exposure.parquet"],
        theme_count=theme_count,
        company_theme_pair_count=pair_count,
    )


@app.post("/api/discovery/freeze", response_model=FreezeResponse)
def discovery_freeze(req: FreezeRequest) -> FreezeResponse:
    """Freeze all discovery artifacts (M5, OI-3, io_contracts §2).

    Creates validation/ directory, computes sha256 hashes of all discovery
    artifacts, updates run_manifest.json with discovery_artifact_hashes and
    discovery_frozen=true. Idempotent.
    """
    try:
        manifest = freeze_mod.freeze_discovery(req.run_id)
    except RuntimeError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except (FileNotFoundError, ValueError, PermissionError) as exc:
        raise HTTPException(status_code=409, detail=str(exc))

    manifest_path = f"data/runs/{req.run_id}/{runs.MANIFEST_NAME}"
    return FreezeResponse(
        success=True,
        discovery_frozen=manifest.discovery_frozen,
        discovery_artifact_hashes=manifest.discovery_artifact_hashes or {},
        manifest_path=manifest_path,
    )


@app.post("/api/validation/run", response_model=ValidationRunResponse)
def validation_run(req: ValidationRunRequest) -> ValidationRunResponse:
    """Run M6 freeze-gated forward-return validation (io_contracts §24).

    Precondition (OI-3): discovery must be frozen and hashes must match.
    Reads validation/market_prices.parquet (future data).
    Writes validation/portfolio_baskets.parquet + validation/validation.csv.
    """
    try:
        result = validation_mod.run_validation(req.run_id)
    except RuntimeError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except (PermissionError, ValueError, FileNotFoundError) as exc:
        raise HTTPException(status_code=409, detail=str(exc))

    return ValidationRunResponse(
        success=result.get("success", False),
        validation_status=result.get("validation_status", "failed"),
        backtest_status=result.get("backtest_status"),
        artifacts=result.get("artifacts", []),
        validated_themes=result.get("validated_themes", 0),
        message=result.get("message"),
        missing_ranges=result.get("missing_ranges"),
        as_of_date=result.get("as_of_date"),
        holding_window=result.get("holding_window"),
        required_end=result.get("required_end"),
    )


@app.post("/api/provenance/materialize")
def provenance_materialize(req: GraphBuildRequest):
    """Materialize EG-E provenance artifacts (E2 + E3) for a run.

    E2: theme_document_evidence.parquet — community_id -> chunk_ids / document_ids.
        Requires: communities.json, theme_snapshots.json, edges.parquet, chunks.parquet.

    E3: company_theme_document_evidence.parquet — (company_id, theme_snapshot_id,
        community_id) -> chunk_ids / document_ids.
        Requires: company_theme_exposure.parquet, chunks.parquet.

    company_id in E3 is the Company ENTITY id (not document.company_id).

    Must be called AFTER /api/exposure/compute.
    """
    try:
        result = provenance_mod.materialize_provenance(req.run_id)
    except HTTPException:
        raise
    except (FileNotFoundError, RuntimeError) as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return {
        "success": True,
        "run_id": req.run_id,
        "artifacts": [
            "discovery/theme_document_evidence.parquet",
            "discovery/company_theme_document_evidence.parquet",
        ],
        "theme_rows": result["theme_rows"],
        "company_theme_rows": result["company_theme_rows"],
    }


@app.get("/api/themes/{run_id}/communities/{community_id}/documents")
def get_theme_documents(run_id: str, community_id: str):
    """E2 provenance: source documents for a theme community (single read).

    Returns chunk_ids and document_ids that form the evidence base for
    this community.  Requires theme_document_evidence.parquet to exist;
    call POST /api/provenance/materialize first.
    """
    try:
        return provenance_mod.get_theme_documents(run_id, community_id)
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@app.get("/api/themes/{run_id}/companies/{company_id}/documents")
def get_company_theme_documents(run_id: str, company_id: str):
    """E3 provenance: per-theme source documents for a company (entity-based join).

    Returns a list of records — one per (theme_snapshot_id, community_id) —
    each with the DISTINCT chunk_ids and document_ids behind THAT specific
    company-theme exposure.  Evidence groups never bleed across themes.

    company_id must be a Company ENTITY id (ent_...); it is NOT document.company_id.
    Requires company_theme_document_evidence.parquet; call
    POST /api/provenance/materialize first.
    """
    try:
        return provenance_mod.get_company_theme_documents(run_id, company_id)
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@app.post("/api/report/generate", response_model=ReportGenerateResponse)
def report_generate(req: ReportGenerateRequest) -> ReportGenerateResponse:
    """Generate a research report from existing run artifacts (M7, io_contracts §23).

    Assembles report.md deterministically from:
      - run_manifest.json
      - discovery/communities.json
      - discovery/theme_snapshots.json
      - discovery/theme_metrics.parquet
      - discovery/company_theme_exposure.parquet
      - validation/validation.csv (optional)

    No new discovery or validation computation is performed.
    Every key claim references a specific artifact.
    Carries the single-snapshot / illustrative caveat per spec §2.
    """
    try:
        report_path = report_mod.generate_report(run_id=req.run_id)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    relative_path = f"data/runs/{req.run_id}/report.md"
    return ReportGenerateResponse(
        success=True,
        artifact="report.md",
        report_path=relative_path,
    )
