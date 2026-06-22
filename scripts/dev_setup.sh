#!/usr/bin/env bash
# One-click local dev setup for investment_ontology.
# Creates a .venv (Python 3.11 preferred), installs deps, and verifies the
# test suite + consistency/leakage gate.
set -euo pipefail
cd "$(dirname "$0")/.."

PY="${PYTHON:-python3.11}"
command -v "$PY" >/dev/null 2>&1 || PY=python3
echo "Using $("$PY" --version)"

"$PY" -m venv .venv
.venv/bin/python -m pip install --quiet --upgrade pip
.venv/bin/python -m pip install --quiet -r requirements.txt -r requirements-dev.txt

echo "Verifying: tests + consistency gate..."
.venv/bin/python -m pytest tests/ -q
.venv/bin/python scripts/ci/check_consistency.py

cat <<'EOF'

Setup complete.
  Activate:      source .venv/bin/activate
  Run tests:     python -m pytest tests/ -q
  Run backend:   uvicorn theme_engine.main:app --app-dir app/backend --reload
EOF
