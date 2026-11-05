#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

command -v python3 >/dev/null || { echo "Python 3.10+ is required."; exit 1; }
command -v npm >/dev/null || { echo "Node.js 18+ and npm are required."; exit 1; }

if [ ! -x .venv/bin/python ]; then
  echo "Creating Python virtual environment..."
  python3 -m venv .venv
fi
PIP_INDEX="${PIP_INDEX_URL:-https://repo.huaweicloud.com/repository/pypi/simple/}"
.venv/bin/python -m pip install --disable-pip-version-check -i "$PIP_INDEX" -r backend/requirements.txt

cd frontend
if [ -f package-lock.json ]; then
  if ! npm ci --no-audit --no-fund; then
    npm ci --no-audit --no-fund --registry "${NPM_CONFIG_REGISTRY:-https://registry.npmmirror.com}"
  fi
else
  if ! npm install --no-audit --no-fund; then
    npm install --no-audit --no-fund --registry "${NPM_CONFIG_REGISTRY:-https://registry.npmmirror.com}"
  fi
fi
npm run build
cd "$ROOT"

if [ ! -f .env ] && [ -f .env.example ]; then
  cp .env.example .env
  echo "Created .env from .env.example."
fi
echo "Setup complete. Start with: ./start.sh"