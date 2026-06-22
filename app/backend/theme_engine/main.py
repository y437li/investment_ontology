"""FastAPI application: Milestone 1 run lifecycle.

Exposes the run endpoints from spec section 3. Later milestones add the
data/extraction/graph/exposure/validation/report routers under the same app.
"""

from __future__ import annotations

from fastapi import FastAPI, HTTPException

from . import artifacts as artifacts_mod, chunking, data_cleaning, data_import, extraction, entity_resolution, exposure as exposure_mod, freeze as freeze_mod, graph_build, node_explanation as node_explanation_mod, reasoning as reasoning_mod, report as report_mod, runs, theme_hierarchy as theme_hierarchy_mod, theme_levels as theme_levels_mod, theme_relevance as theme_relevance_mod, themes, validation as validation_mod
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


@app.get("/api/themes/{run_id}/communities/{community_id}/narrative")
def get_theme_narrative(run_id: str, community_id: str, refresh: bool = False):
    """Connect-the-dots narrative + captured reasoning chain for a community (cached)."""
    try:
        return reasoning_mod.get_or_synthesize(run_id, community_id, refresh=refresh)
    except KeyError:
        raise HTTPException(status_code=503, detail="LLM not configured (set LLM_API_KEY/BASE_URL/MODEL)")
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@app.get("/api/themes/{run_id}/levels")
def get_theme_levels(run_id: str):
    """Per-theme factor-level composition (macro/industry/company/idiosyncratic) +
    substantive flag, so themes can be filtered by level and 0-metric noise hidden."""
    try:
        return theme_levels_mod.compute_levels(run_id)
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
