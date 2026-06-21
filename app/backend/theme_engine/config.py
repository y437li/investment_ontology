"""Runtime settings, sourced from environment with local-dev defaults.

Kept dependency-free (plain os.environ) so the backend can boot without a
settings framework. Paths are resolved relative to the repo root unless an
absolute path is provided.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

# Repo root = three levels up from this file: theme_engine/ -> backend/ -> app/ -> root.
REPO_ROOT = Path(__file__).resolve().parents[3]


def _resolve(path: str) -> Path:
    p = Path(path)
    return p if p.is_absolute() else (REPO_ROOT / p)


@dataclass(frozen=True)
class Settings:
    app_env: str = os.environ.get("APP_ENV", "local")
    data_dir: Path = _resolve(os.environ.get("DATA_DIR", "./data"))
    run_output_dir: Path = _resolve(os.environ.get("RUN_OUTPUT_DIR", "./data/runs"))
    config_dir: Path = _resolve(os.environ.get("CONFIG_DIR", "./configs"))

    # Default config artifacts recorded into each run manifest.
    universe_config: str = os.environ.get(
        "UNIVERSE_CONFIG", "configs/universe.example.yml"
    )
    pipeline_config: str = os.environ.get(
        "PIPELINE_CONFIG", "configs/pipeline.example.yml"
    )
    validation_config: str = os.environ.get(
        "VALIDATION_CONFIG", "configs/validation.example.yml"
    )


settings = Settings()
