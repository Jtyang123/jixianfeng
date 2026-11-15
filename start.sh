#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"
if [ ! -f .env ] && [ -f .env.example ]; then cp .env.example .env; fi
if [ ! -f frontend/dist/index.html ]; then
  command -v npm >/dev/null || { echo "Run ./setup.sh first."; exit 1; }
  (cd frontend && npm run build)
fi
if [ -x "$ROOT/.venv/bin/python" ]; then PYTHON="$ROOT/.venv/bin/python"; else PYTHON="python3"; fi
cd backend
echo ""
echo "Jixianfeng (Rapid Response OS) starting: http://127.0.0.1:${PORT:-8010}"
echo "  Login username : admin"
if [ -f "$ROOT/data/bootstrap-admin-password.txt" ]; then
  echo "  Admin password : see data/bootstrap-admin-password.txt"
else
  echo "  Admin password : generated on first run, shown below and saved to data/bootstrap-admin-password.txt"
fi
echo ""
exec "$PYTHON" -m uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8010}"