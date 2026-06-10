#!/usr/bin/env bash
# Idempotently register everything needed for the examples:
#   - core loop-engine workflows (../workflows)
#   - example task definitions (taskdefs/)
#   - example actor/evaluator sub-workflows (*/workflows)
# Safe to re-run: create first, fall back to update if it already exists.
# Exits non-zero if ANY registration fails, so CI can gate on it.
set -uo pipefail
cd "$(dirname "$0")"

failures=0
reg_wf() { conductor workflow create "$1" >/dev/null 2>&1 && echo "  created  $1" \
           || { conductor workflow update "$1" >/dev/null 2>&1 && echo "  updated  $1" \
                || { echo "  FAILED   $1"; failures=$((failures+1)); }; }; }
reg_td() { conductor task create "$1" >/dev/null 2>&1 && echo "  created  $1" \
           || { conductor task update "$1" >/dev/null 2>&1 && echo "  updated  $1" \
                || { echo "  FAILED   $1"; failures=$((failures+1)); }; }; }

echo "== core loop-engine workflows =="
for f in ../workflows/*.json; do reg_wf "$f"; done

echo "== task definitions =="
for f in taskdefs/*.json; do reg_td "$f"; done

echo "== example sub-workflows =="
for f in */workflows/*.json; do reg_wf "$f"; done

if [ "$failures" -gt 0 ]; then
  echo "Done with $failures FAILED registration(s)." >&2
  exit 1
fi
echo "Done. Start workers with: (cd workers && python run_workers.py)"
