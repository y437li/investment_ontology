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
        runs.validate_ready_for_validation(req.run_id)
    except RuntimeError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except (PermissionError, ValueError, FileNotFoundError) as exc:
        raise HTTPException(status_code=409, detail=str(exc))

    return ValidationRunResponse(
        success=True,
        validation_status="blocked_not_implemented",
        artifacts=[],
        validated_themes=0,
        message="validation pipeline is not yet implemented; freeze gate passed",
    )
