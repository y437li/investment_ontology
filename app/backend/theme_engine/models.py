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
    sweep_parent_id: Optional[str] = None


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
    extraction_failed: int
