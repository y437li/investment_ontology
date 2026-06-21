"""FastAPI application: Milestone 1 run lifecycle.

Exposes the run endpoints from spec section 3. Later milestones add the
data/extraction/graph/exposure/validation/report routers under the same app.
"""

from __future__ import annotations

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse

from . import data_collection, data_cleaning, data_import, news_package, runs, theme_affinity, validation
from .models import (
    DataImportRequest,
    DataImportResponse,
    DataCollectionRequest,
    DataCollectionResponse,
    DataCleanRequest,
    DataCleanResponse,
    DataChunkRequest,
    DataChunkResponse,
    DataThemeAffinityRequest,
    DataThemeAffinityResponse,
    NewsPackageRequest,
    NewsPackageResponse,
    RunCreateRequest,
    FreezeRequest,
    FreezeResponse,
    ValidationRunRequest,
    ValidationRunResponse,
    RunManifest,
    RunStatus,
)

app = FastAPI(title="Theme Discovery Engine", version="0.1.0")


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/api/runs/create", response_model=RunManifest)
def create_run(req: RunCreateRequest) -> RunManifest:
    return runs.create_run(req)


@app.get("/api/runs/{run_id}/status", response_model=RunStatus)
def run_status(run_id: str) -> RunStatus:
    status = runs.get_status(run_id)
    if status is None:
        raise HTTPException(status_code=404, detail=f"run not found: {run_id}")
    return status


@app.get("/api/artifacts/{run_id}/{artifact_name:path}")
def get_artifact(run_id: str, artifact_name: str) -> FileResponse:
    path = runs.resolve_artifact_path(run_id, artifact_name)
    if path is None:
        raise HTTPException(
            status_code=404,
            detail=f"artifact not found: {artifact_name}",
        )
    return FileResponse(path)


@app.post("/api/data/import", response_model=DataImportResponse)
def import_data(req: DataImportRequest) -> DataImportResponse:
    (
        raw_documents_seen,
        raw_documents,
        raw_documents_in_discovery,
        future_excluded,
        quarantine_reasons,
    ) = data_import.import_manifest(
        run_id=req.run_id,
        documents_dir=req.documents_dir,
        source_manifest_path=req.source_manifest_path,
    )
    return DataImportResponse(
        success=True,
        run_id=req.run_id,
        artifacts=["discovery/raw_documents.parquet"],
        raw_documents=raw_documents,
        raw_documents_seen=raw_documents_seen,
        raw_documents_in_discovery=raw_documents_in_discovery,
        future_excluded=future_excluded,
        quarantined=len(quarantine_reasons),
        quarantine_reasons=quarantine_reasons,
    )


@app.post("/api/data/collect", response_model=DataCollectionResponse)
def collect_data(req: DataCollectionRequest) -> DataCollectionResponse:
    (
        seen,
        collected,
        quarantined,
        _,
        quarantine_reasons,
        manifest_path,
        report_path,
    ) = data_collection.collect_sources(
        spec_path=req.source_spec_path,
        documents_dir=req.documents_dir,
        source_manifest_path=req.source_manifest_path,
        run_id=req.run_id,
        append_manifest=req.append_manifest,
    )
    return DataCollectionResponse(
        success=True,
        sources_seen=seen,
        sources_collected=collected,
        sources_quarantined=quarantined,
        source_manifest_path=manifest_path,
        report_path=report_path,
        quarantined=quarantined,
        quarantine_reasons=quarantine_reasons,
    )


@app.post("/api/data/clean", response_model=DataCleanResponse)
def clean_data(req: DataCleanRequest) -> DataCleanResponse:
    included_documents, quarantined_documents, artifacts = data_cleaning.clean_documents(req.run_id)
    return DataCleanResponse(
        success=True,
        artifacts=artifacts,
        included_documents=included_documents,
        quarantined_documents=quarantined_documents,
    )


@app.post("/api/data/chunk", response_model=DataChunkResponse)
def chunk_data(req: DataChunkRequest) -> DataChunkResponse:
    chunk_count, artifacts = data_cleaning.chunk_documents(req.run_id)
    return DataChunkResponse(
        success=True,
        artifacts=artifacts,
        chunk_count=chunk_count,
    )


@app.post("/api/themes/document-affinity", response_model=DataThemeAffinityResponse)
def map_documents_to_themes(req: DataThemeAffinityRequest) -> DataThemeAffinityResponse:
    mapped_documents, mapped_pairs, artifacts = theme_affinity.compute_document_theme_affinity(
        run_id=req.run_id,
        max_themes_per_document=req.max_themes_per_document,
    )
    return DataThemeAffinityResponse(
        success=True,
        artifacts=artifacts,
        mapped_documents=mapped_documents,
        mapped_pairs=mapped_pairs,
    )


@app.post("/api/reporting/news-package", response_model=NewsPackageResponse)
def news_package_api(req: NewsPackageRequest) -> NewsPackageResponse:
    artifact_path, total_documents, total_chunks = news_package.create_news_package(
        run_id=req.run_id,
        max_documents=req.max_documents,
        max_chunks_per_document=req.max_chunks_per_document,
        max_chunk_chars=req.max_chunk_chars,
        include_document_types=req.include_document_types,
        include_companies=req.include_companies,
        include_macro=req.include_macro,
        include_affinity=req.include_affinity,
    )
    return NewsPackageResponse(
        success=True,
        artifact="news_report_package.json",
        artifact_path=artifact_path,
        package_version="1.0",
        total_documents=total_documents,
        total_chunks=total_chunks,
    )


@app.post("/api/reporting/research-package", response_model=NewsPackageResponse)
def research_package_api(req: NewsPackageRequest) -> NewsPackageResponse:
    include_document_types = req.include_document_types or ["research"]
    artifact_path, total_documents, total_chunks = news_package.create_news_package(
        run_id=req.run_id,
        max_documents=req.max_documents,
        max_chunks_per_document=req.max_chunks_per_document,
        max_chunk_chars=req.max_chunk_chars,
        include_document_types=include_document_types,
        include_companies=req.include_companies,
        include_macro=req.include_macro,
        include_affinity=req.include_affinity,
    )
    return NewsPackageResponse(
        success=True,
        artifact="news_report_package.json",
        artifact_path=artifact_path,
        package_version="1.0",
        total_documents=total_documents,
        total_chunks=total_chunks,
    )


@app.post("/api/discovery/freeze", response_model=FreezeResponse)
def discovery_freeze(req: FreezeRequest) -> FreezeResponse:
    try:
        manifest = runs.freeze_discovery(req.run_id)
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
    try:
        result = validation.run_validation(
            req.run_id,
            market_data_dir=req.market_data_dir,
            fundamentals_data_dir=req.fundamentals_data_dir,
            include_fundamentals=req.include_fundamentals,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except (PermissionError, ValueError, FileNotFoundError) as exc:
        raise HTTPException(status_code=409, detail=str(exc))

    return ValidationRunResponse(
        success=True,
        validation_status=result.validation_status,
        artifacts=result.artifacts,
        validated_themes=result.validated_themes,
        message="validation pipeline completed",
    )
