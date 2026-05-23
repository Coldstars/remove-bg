#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
python3 src/batch_remove_bg.py "$@"
