"""FastAPI application: Milestone 1 run lifecycle.

Exposes the run endpoints from spec section 3. Later milestones add the
data/extraction/graph/exposure/validation/report routers under the same app.
"""

from __future__ import annotations

from fastapi import FastAPI, HTTPException

from . import data_import, runs
from .models import (
    DataImportRequest,
    DataImportResponse,
    RunCreateRequest,
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


@app.post("/api/data/import", response_model=DataImportResponse)
def import_data(req: DataImportRequest) -> DataImportResponse:
    raw_documents, extraction_failed = data_import.import_manifest(
        run_id=req.run_id,
        documents_dir=req.documents_dir,
        source_manifest_path=req.source_manifest_path,
    )
    return DataImportResponse(
        success=True,
        run_id=req.run_id,
        artifacts=["raw_documents.parquet"],
        raw_documents=raw_documents,
        extraction_failed=extraction_failed,
    )
