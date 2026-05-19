#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

PYTHON_BIN="python3"
if [[ -x "${SCRIPT_DIR}/venv/bin/python" ]]; then
  PYTHON_BIN="${SCRIPT_DIR}/venv/bin/python"
elif [[ -x "${SCRIPT_DIR}/../.venv/bin/python" ]]; then
  PYTHON_BIN="${SCRIPT_DIR}/../.venv/bin/python"
fi

echo "Running Phase 6 governance contract suite with ${PYTHON_BIN}..."
"${PYTHON_BIN}" -m pytest \
  test_phase6_platform_env_contract.py \
  test_phase6_runtime_governance_contract.py \
  test_phase6_watchdog_runtime_status_contract.py \
  test_phase6_enterprise_telemetry_contract.py \
  -q
