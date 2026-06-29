"""Runtime settings, sourced from environment with local-dev defaults.

Kept dependency-free (plain os.environ) so the backend can boot without a
settings framework. Paths are resolved relative to the repo root unless an
absolute path is provided.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

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


# ---------------------------------------------------------------------------
# Issue #29: per-task LLM model resolution.
# ---------------------------------------------------------------------------
#
# The model used by each LLM stage is selectable per task via the ``llm_models``
# block of ``<CONFIG_DIR>/pipeline.yml`` (template: pipeline.example.yml). No
# provider model string is ever hardcoded in code; call sites resolve via
# ``model_for(task)``. Resolution order is: task config > default config > env
# ``LLM_MODEL_NAME`` > None. ``None`` means "no model configured" and callers
# fall back to rule-based / no-op behaviour.


def _load_llm_models() -> dict:
    """Tolerant read of the ``llm_models`` block from ``<CONFIG_DIR>/pipeline.yml``.

    Missing file / no pyyaml / parse error -> ``{}`` (falls through the
    resolution chain). Not cached (cheap, called once per stage; keeps tests
    trivially monkeypatchable).
    """
    p = settings.config_dir / "pipeline.yml"
    if not p.exists():
        return {}
    try:
        import yaml  # noqa: PLC0415

        doc = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except Exception as exc:  # malformed config: warn loudly, fall through
        import logging  # noqa: PLC0415

        logging.getLogger(__name__).warning(
            "failed to parse %s: %s — falling back to env/default model", p, exc
        )
        return {}
    block = doc.get("llm_models") or {}
    return block if isinstance(block, dict) else {}


def _coerce_model(val: object) -> Optional[str]:
    """Normalize one ``llm_models`` entry to a usable model string, else None.

    Accepts either a bare string (``"gpt-4o"``) or the issue's nested form
    (``{model: gpt-4o, max_tokens: 4096}``) and extracts the model name. An
    unexpanded ``${VAR}`` placeholder (config is read with plain ``yaml.safe_load``
    — no env expansion) is treated as "not configured" so it falls through to the
    env fallback rather than being sent to the API verbatim.
    """
    if isinstance(val, dict):
        val = val.get("model")
    if not isinstance(val, str):
        return None
    val = val.strip()
    if not val or (val.startswith("${") and val.endswith("}")):
        return None
    return val


def model_for(task: str) -> Optional[str]:
    """Resolve the LLM model for a task.

    Order: ``llm_models[task]`` > ``llm_models['default']`` > env
    ``LLM_MODEL_NAME`` > ``None``. ``None`` means 'no model configured' ->
    caller falls back to rule-based / no-op. Entries may be a bare string or a
    ``{model: ...}`` dict; unexpanded ``${VAR}`` placeholders fall through.
    """
    cfg = _load_llm_models()
    val = _coerce_model(cfg.get(task))
    if val:
        return val
    default = _coerce_model(cfg.get("default"))
    if default:
        return default
    return os.environ.get("LLM_MODEL_NAME") or None
