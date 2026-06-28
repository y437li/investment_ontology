"""Pydantic schemas for runs.

`RunManifest` is the canonical `run_manifest.json` contract from spec section 8,
plus forward-compatible fields for the run-vs-sweep model (OI-6) and the freeze
flag (OI-3 / Milestone 5).
"""

from __future__ import annotations

import re
from typing import Optional

from pydantic import BaseModel, Field, field_validator

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class RunCreateRequest(BaseModel):
    as_of_date: str = Field(..., description="Point-in-time cutoff, YYYY-MM-DD.")
    universe_config: Optional[str] = None
    pipeline_config: Optional[str] = None
    validation_config: Optional[str] = None
    sweep_parent_id: Optional[str] = None

    @field_validator("as_of_date")
    @classmethod
    def _valid_date(cls, v: str) -> str:
        if not _DATE_RE.match(v):
            raise ValueError("as_of_date must be YYYY-MM-DD")
        return v


class RunManifest(BaseModel):
    run_id: str
    as_of_date: str
    universe_config: str
    pipeline_config: str
    validation_config: str
    created_at: str
    code_version: str
    input_hash: str
    # Forward-compatible: set by later milestones, present from creation.
    discovery_frozen: bool = False
    discovery_artifact_hashes: Optional[dict[str, str]] = None
    sweep_parent_id: Optional[str] = None
    # M5: set by freeze endpoint
    frozen_at: Optional[str] = None


class RunStatus(BaseModel):
    run_id: str
    as_of_date: str
    created_at: str
    discovery_frozen: bool
    artifacts_present: list[str]


class DataImportRequest(BaseModel):
    run_id: str
    documents_dir: str = Field(..., description="Directory containing source documents.")
    source_manifest_path: str = Field(
        ...,
        description="Path to source manifest CSV describing raw documents.",
    )


class DataImportResponse(BaseModel):
    success: bool
    run_id: str
    artifacts: list[str]
    raw_documents: int
    quarantined: int
    quarantine_reasons: list[str]


class DataCleanRequest(BaseModel):
    run_id: str
    documents_dir: Optional[str] = Field(
        default=None,
        description="Optional document input root used to resolve raw_path; "
        "defaults to resolving raw_path against the repo root.",
    )


class DataCleanResponse(BaseModel):
    success: bool
    run_id: str
    artifacts: list[str]
    included_documents: int
    quarantined_documents: int
    quarantine_reasons: list[str]


class DataChunkRequest(BaseModel):
    run_id: str


class DataChunkResponse(BaseModel):
    success: bool
    run_id: str
    artifacts: list[str]
    chunk_count: int


class FreezeRequest(BaseModel):
    run_id: str


class FreezeResponse(BaseModel):
    success: bool
    discovery_frozen: bool
    discovery_artifact_hashes: dict[str, str]
    manifest_path: str


class ValidationRunRequest(BaseModel):
    run_id: str


class ValidationRunResponse(BaseModel):
    success: bool
    validation_status: str
    backtest_status: Optional[str] = None
    artifacts: list[str]
    validated_themes: int = 0
    message: Optional[str] = None
    # OI-1 illustrative guard: single-snapshot always true/false; walk-forward may differ.
    illustrative: Optional[bool] = None
    claim_supported: Optional[bool] = None
    # Populated when validation_status == 'blocked_insufficient_forward_data'
    missing_ranges: Optional[list[str]] = None
    as_of_date: Optional[str] = None
    holding_window: Optional[str] = None
    required_end: Optional[str] = None


class ExtractionRunRequest(BaseModel):
    """Request body for POST /api/extraction/run."""

    run_id: str


class ExtractionRunResponse(BaseModel):
    """Response body for POST /api/extraction/run."""

    success: bool
    run_id: str
    artifacts: list[str]
    entity_count: int
    edge_count: int


class ExtractionResolveRequest(BaseModel):
    """Request body for POST /api/extraction/resolve."""

    run_id: str


class ExtractionResolveResponse(BaseModel):
    """Response body for POST /api/extraction/resolve."""

    success: bool
    run_id: str
    artifacts: list[str]
    alias_count: int


class GraphBuildRequest(BaseModel):
    """Request body for POST /api/graph/build."""

    run_id: str


class GraphBuildResponse(BaseModel):
    """Response body for POST /api/graph/build (io_contracts §24)."""

    success: bool
    artifacts: list[str]
    node_count: int
    edge_count: int


class ThemeDiscoverRequest(BaseModel):
    """Request body for POST /api/themes/discover."""

    run_id: str


class ThemeDiscoverResponse(BaseModel):
    """Response body for POST /api/themes/discover (io_contracts §24)."""

    success: bool
    artifacts: list[str]
    community_count: int


class ExposureComputeRequest(BaseModel):
    """Request body for POST /api/exposure/compute (io_contracts §24)."""

    run_id: str
    include_weak_signals: bool = False


class ExposureComputeResponse(BaseModel):
    """Response body for POST /api/exposure/compute (io_contracts §24)."""

    success: bool
    artifacts: list[str]
    theme_count: int
    company_theme_pair_count: int


class ReportGenerateRequest(BaseModel):
    """Request body for POST /api/report/generate (io_contracts §24)."""

    run_id: str


class ReportGenerateResponse(BaseModel):
    """Response body for POST /api/report/generate (io_contracts §24).

    Note: 'model_config' field name is FORBIDDEN (Pydantic v2 reserved).
    """

    success: bool
    artifact: str
    report_path: str
