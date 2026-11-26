#!/bin/bash
set -euo pipefail
cd -- "$(dirname -- "$0")"
umask 077
if [ ! -x .venv/bin/python ] || [ ! -f dist/index.html ]; then
  echo 'Run Setup Finance.command first.'
  exit 1
fi
exec .venv/bin/python -m finance
