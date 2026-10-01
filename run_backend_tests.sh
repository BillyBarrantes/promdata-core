#!/bin/bash
set -euo pipefail

cd "$(dirname "$0")/backend"
venv/bin/pytest -q "$@"
