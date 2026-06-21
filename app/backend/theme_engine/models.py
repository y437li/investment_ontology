"""Pydantic schemas for runs.

`RunManifest` is the canonical `run_manifest.json` contract from spec section 8,
plus forward-compatible fields for the run-vs-sweep model (OI-6) and the freeze
flag (OI-3 / Milestone 5).
"""

from __future__ import annotations

import re
from typing import Any, Optional

from pydantic import BaseModel, Field, field_validator

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class RunCreateRequest(BaseModel):
    as_of_date: str = Field(..., description="Point-in-time cutoff, YYYY-MM-DD.")
    universe_config: Optional[str] = None
    pipeline_config: Optional[str] = None
    validation_config: Optional[str] = None
    sweep_parent_id: Optional[str] = None
    model_config: dict[str, Any] | None = None

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
    model_config: dict[str, Any] | None = None
    model_config_hash: str | None = None
    created_at: str
    code_version: str
    input_hash: str
    # Forward-compatible: set by later milestones, present from creation.
    discovery_frozen: bool = False
    discovery_artifact_hashes: Optional[dict[str, str]] = None
    sweep_parent_id: Optional[str] = None


class RunStatus(BaseModel):
    run_id: str
    as_of_date: str
    created_at: str
    discovery_frozen: bool
    artifacts_present: list[str]
    validation_status: str | None = None
    validation_artifacts: list[str] = Field(default_factory=list)


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
    raw_documents_seen: int
    raw_documents_in_discovery: int
    future_excluded: int
    quarantined: int
    quarantine_reasons: list[str]


class DataCollectionRequest(BaseModel):
    source_spec_path: str = Field(..., description="CSV spec of source inputs to collect.")
    documents_dir: str = Field(
        default="data/inputs/documents",
        description="Destination corpus root for collected source files.",
    )
    source_manifest_path: str = Field(
        default="data/inputs/documents/source_manifest.csv",
        description="Manifest file to write for downstream import.",
    )
    run_id: str | None = Field(
        default=None,
        description="Optional run id to namespace collection report.",
    )
    append_manifest: bool = Field(
        default=False,
        description="Append to an existing source manifest instead of overwrite.",
    )


class DataCollectionResponse(BaseModel):
    success: bool
    sources_seen: int
    sources_collected: int
    sources_quarantined: int
    source_manifest_path: str
    report_path: str
    quarantined: int
    quarantine_reasons: list[str]


class DataCleanRequest(BaseModel):
    run_id: str = Field(..., description="Run id for cleaning.")


class DataCleanResponse(BaseModel):
    success: bool
    artifacts: list[str]
    included_documents: int
    quarantined_documents: int


class DataChunkRequest(BaseModel):
    run_id: str = Field(..., description="Run id for chunking.")


class DataChunkResponse(BaseModel):
    success: bool
    artifacts: list[str]
    chunk_count: int


class DataThemeAffinityRequest(BaseModel):
    run_id: str = Field(..., description="Run id for document-theme affinity mapping.")
    max_themes_per_document: int = Field(
        default=20,
        ge=0,
        description="Max themes/community matches per document; 0 means no limit.",
    )


class DataThemeAffinityResponse(BaseModel):
    success: bool
    artifacts: list[str]
    mapped_documents: int
    mapped_pairs: int


class NewsPackageRequest(BaseModel):
    run_id: str = Field(..., description="Run id for news package assembly.")
    max_documents: int = Field(default=100, gt=0, description="Maximum documents in package.")
    max_chunks_per_document: int = Field(
        default=4,
        gt=0,
        description="Max chunks included per document.",
    )
    max_chunk_chars: int = Field(
        default=1200,
        gt=0,
        description="Maximum characters per chunk in package.",
    )
    include_document_types: list[str] = Field(
        default_factory=list,
        description="Filter by exact document_type/source labels when not empty.",
    )
    include_companies: list[str] = Field(
        default_factory=list,
        description="Filter by company_id when not empty.",
    )
    include_macro: bool = Field(default=False, description="Include macro documents.")
    include_affinity: bool = Field(
        default=True,
        description="Attach document-theme affinity rows if present.",
    )


class NewsPackageResponse(BaseModel):
    success: bool
    artifact: str
    artifact_path: str
    package_version: str
    total_documents: int
    total_chunks: int


class FreezeRequest(BaseModel):
    run_id: str


class FreezeResponse(BaseModel):
    success: bool
    discovery_frozen: bool
    discovery_artifact_hashes: dict[str, str]
    manifest_path: str


class ValidationRunRequest(BaseModel):
    run_id: str
    market_data_dir: str | None = None
    fundamentals_data_dir: str | None = None
    include_fundamentals: bool = False


class ValidationRunResponse(BaseModel):
    success: bool
    validation_status: str
    artifacts: list[str]
    validated_themes: int = 0
    message: Optional[str] = None
