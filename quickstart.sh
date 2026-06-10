#!/usr/bin/env bash
# Loop Engine quickstart:
#   1. verify the Conductor server is reachable
#   2. verify an LLM provider key is exported (Anthropic or OpenAI)
#   3. register the Loop Engine workflows (idempotent: create, fall back to update)
# Safe to re-run. Exits non-zero if the server is down or any registration fails.
set -uo pipefail
cd "$(dirname "$0")"

SERVER="${CONDUCTOR_SERVER_URL:-http://localhost:8080/api}"
BASE="${SERVER%/api}"

echo "== Conductor server =="
if curl -sf -o /dev/null --max-time 5 "$BASE/health"; then
  echo "  ok       $BASE"
else
  echo "  UNREACHABLE  $BASE"
  echo
  echo "  Start one locally:    conductor server start"
  echo "  Or point elsewhere:   export CONDUCTOR_SERVER_URL=https://your-server/api"
  exit 1
fi

echo "== LLM provider keys =="
found_key=0
[ -n "${ANTHROPIC_API_KEY:-}" ] && { echo "  found    ANTHROPIC_API_KEY (demos use this by default)"; found_key=1; }
[ -n "${OPENAI_API_KEY:-}" ]    && { echo "  found    OPENAI_API_KEY"; found_key=1; }
if [ "$found_key" -eq 0 ]; then
  echo "  WARNING  no ANTHROPIC_API_KEY or OPENAI_API_KEY in this shell."
  echo "           The server needs a key at startup for LLM_CHAT_COMPLETE tasks:"
  echo "             export ANTHROPIC_API_KEY=sk-ant-...   # then: conductor server start"
  echo "             export OPENAI_API_KEY=sk-..."
  echo "           Continuing with registration anyway."
fi

echo "== Registering Loop Engine workflows =="
failures=0
for f in workflows/*.json; do
  if conductor workflow create "$f" >/dev/null 2>&1; then
    echo "  created  $f"
  elif out=$(conductor workflow update "$f" 2>&1); then
    echo "  updated  $f"
  else
    echo "  FAILED   $f"
    failures=$((failures + 1))
    case "$out" in *[Tt]oken*|*[Aa]uth*)
      echo
      echo "  Server requires authentication. Set credentials and re-run:"
      echo "    export CONDUCTOR_AUTH_TOKEN=...   (or CONDUCTOR_AUTH_KEY / CONDUCTOR_AUTH_SECRET)"
      echo "  or: conductor config save"
      exit 1
    esac
  fi
done
[ "$failures" -gt 0 ] && { echo "Done with $failures FAILED registration(s)." >&2; exit 1; }

echo
echo "Ready. Run your first loop:"
echo "  conductor workflow start -w loop_engine -f inputs/demo-minimal.json"
echo "  conductor workflow get-execution <workflowId>"
echo
echo "Demo inputs default to Anthropic (llm_provider: anthropic). For OpenAI, set"
echo '  "llm_provider": "openai", "llm_model": "<your model>"  in the input file.'
