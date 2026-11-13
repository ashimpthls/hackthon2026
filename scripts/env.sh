#!/usr/bin/env bash
# Shared environment for every DFSI script.
#
# PySpark 3.5 cannot run on JDK 23+ (javax.security.auth.Subject.getSubject was
# removed, which Hadoop's UserGroupInformation still calls). Pin JDK 17 — the
# same major version netsec-ddos-shared-state-2 itself builds against.
#
# Safe to `source` from bash or zsh, and safe to execute. zsh does not set
# BASH_SOURCE when sourcing, so the script's own path is resolved from
# whichever of the two the shell provides.
set -eu

if [ -n "${BASH_SOURCE:-}" ]; then
  _dfsi_self="${BASH_SOURCE[0]}"
elif [ -n "${ZSH_VERSION:-}" ]; then
  # shellcheck disable=SC2296
  _dfsi_self="${(%):-%x}"
else
  _dfsi_self="$0"
fi

DFSI_ROOT="$(cd "$(dirname "$_dfsi_self")/.." && pwd)"
unset _dfsi_self
export DFSI_ROOT
export DFSI_HOME="${DFSI_HOME:-$DFSI_ROOT}"
export PYTHON_BIN="${PYTHON_BIN:-$DFSI_ROOT/.venv/bin/python}"

if [[ -z "${JAVA_HOME:-}" ]] || ! "${JAVA_HOME}/bin/java" -version 2>&1 | grep -qE '"(1\.8|11|17|21)'; then
  for candidate in \
    "$(/usr/libexec/java_home -v 17 2>/dev/null || true)" \
    "$(/usr/libexec/java_home -v 21 2>/dev/null || true)" \
    "$(/usr/libexec/java_home -v 11 2>/dev/null || true)"; do
    if [[ -n "$candidate" && -x "$candidate/bin/java" ]]; then
      export JAVA_HOME="$candidate"
      break
    fi
  done
fi

if [[ -z "${JAVA_HOME:-}" ]]; then
  echo "DFSI: no JDK 11/17/21 found. PySpark 3.5 does not support JDK 23+." >&2
  echo "      Install one, e.g. 'brew install openjdk@17'." >&2
  exit 1
fi

export PATH="$JAVA_HOME/bin:$PATH"
# Spark on recent JDKs needs these opens for its unsafe memory access.
export PYSPARK_PYTHON="$PYTHON_BIN"
export PYSPARK_DRIVER_PYTHON="$PYTHON_BIN"
