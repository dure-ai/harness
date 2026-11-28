#!/bin/sh
# run.sh locomo|longmemeval|judge [options...]   (from anywhere; source your env file first, see README.md)
# Runs inside the harness project, so the Mem0/Hindsight adapters (harness.oss) and the pinned
# core (the `core` extra) are the ones the harness itself uses. EXTBENCH_CORE=<core checkout>/src
# puts another core first, for engines newer than the pin.
here=$(cd "$(dirname "$0")" && pwd)
which=$1; shift
cd "$here" && PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$here${EXTBENCH_CORE:+:$EXTBENCH_CORE}" \
  exec uv run --project "$here/../.." --extra core --with nltk python -m "extbench.$which" --root "${EXTBENCH_ROOT:-$here}" "$@"
