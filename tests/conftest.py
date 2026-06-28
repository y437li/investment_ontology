"""Shared test setup.

Makes the backend package importable and isolates run output into a temp dir
so tests never write into the real `data/runs/` tree. Environment is set before
`theme_engine` is imported so settings pick it up.
"""

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "app" / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

os.environ.setdefault("RUN_OUTPUT_DIR", tempfile.mkdtemp(prefix="theme_runs_"))
# Pin CONFIG_DIR to an absolute path so the suite resolves configs/ regardless
# of the cwd pytest is invoked from (registry.py reads it relative by default).
os.environ.setdefault("CONFIG_DIR", str(ROOT / "configs"))
