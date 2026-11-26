#!/bin/bash
set -euo pipefail
cd -- "$(dirname -- "$0")"
umask 077
PYTHON="${FINANCE_PYTHON:-python3.13}"
if ! command -v "$PYTHON" >/dev/null 2>&1; then PYTHON=python3; fi
"$PYTHON" -c 'import sys; assert sys.version_info >= (3,13), "Install Python 3.13 or newer first."'
command -v npm >/dev/null 2>&1 || { echo 'Install Node.js 22.12 or newer first.'; exit 1; }
node -e 'const [a,b]=process.versions.node.split(".").map(Number); if(a<22 || (a===22 && b<12)) throw Error("Node.js 22.12 or newer is required")'
if [ ! -x .venv/bin/python ]; then "$PYTHON" -m venv .venv; fi
.venv/bin/python -m pip install -r requirements.txt
npm ci --prefer-offline
npm run build
echo 'Setup complete. Copy .env.example to .env if needed, then run Start Finance.command.'
