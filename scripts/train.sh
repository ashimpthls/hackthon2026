#!/usr/bin/env bash
# Forecaster — gradient-boosted trees + MLflow tracking.
#
# Trains on the feature table, calibrates the decision threshold against the
# false-alarm budget, evaluates against the fixed-threshold baseline, and
# registers a model version. Pure pandas/sklearn — no JDK needed.
set -eu
cd "$(dirname "$0")/.."
# shellcheck source=scripts/env.sh
. scripts/env.sh

if [ ! -d "$DFSI_HOME/data/features" ]; then
  echo "DFSI: no feature table. Run scripts/build_memory.sh first." >&2
  exit 1
fi

exec "$PYTHON_BIN" -m dfsi.forecaster.train "$@"
