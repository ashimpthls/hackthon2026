#!/usr/bin/env bash
# The whole demo path, from nothing to a dashboard.
#
# Memory (PySpark) → Forecaster (MLflow) → API → UI. Skips the expensive
# stages if their outputs already exist; pass --rebuild to force them.
set -eu
cd "$(dirname "$0")/.."
# shellcheck source=scripts/env.sh
. scripts/env.sh

REBUILD=0
[ "${1:-}" = "--rebuild" ] && REBUILD=1

if [ "$REBUILD" = 1 ] || [ ! -d "$DFSI_HOME/data/features" ]; then
  echo "==> Memory layer (PySpark; a few minutes)"
  scripts/build_memory.sh
else
  echo "==> Memory layer present; skipping (--rebuild to force)"
fi

if [ "$REBUILD" = 1 ] || [ ! -f "$DFSI_HOME/models/forecaster.pkl" ]; then
  echo "==> Forecaster"
  scripts/train.sh
else
  echo "==> Model present; skipping (--rebuild to force)"
fi

if [ ! -d ui/node_modules ]; then
  echo "==> Installing UI dependencies"
  (cd ui && npm install)
fi

echo "==> Starting API on 8000"
scripts/serve_api.sh &
API_PID=$!
# The UI proxies /api to 8000, so the API must be answering before Vite opens.
trap 'kill $API_PID 2>/dev/null || true' EXIT INT TERM
for _ in $(seq 1 40); do
  if curl -sf http://127.0.0.1:8000/api/health >/dev/null 2>&1; then break; fi
  sleep 0.5
done

echo "==> Starting UI on 5173"
(cd ui && npm run dev)
