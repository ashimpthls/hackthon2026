#!/usr/bin/env bash
# DFSI API — the trigger the dashboard talks to.
#
# Serves the replay stream (SSE), the rehearsal room, the two agents and the
# decision log on port 8000. Read-only and simulation-only.
#
# Set ANTHROPIC_API_KEY to run the agents on Claude; without it they fall back
# to the offline rule engine and /api/health reports `offline-rules`.
set -eu
cd "$(dirname "$0")/.."
# shellcheck source=scripts/env.sh
. scripts/env.sh

PORT="${DFSI_PORT:-8000}"
echo "DFSI api: http://127.0.0.1:$PORT/docs"
exec "$PYTHON_BIN" -m uvicorn dfsi.api.app:app --host 127.0.0.1 --port "$PORT" --reload
