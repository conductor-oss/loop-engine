# Loop Engine

**Durable, bounded, self-correcting loops for AI agents — built on open-source
[Conductor](https://github.com/conductor-oss/conductor).**

Running an agent once and hoping is not a system. The reliable pattern is a **loop**: act,
evaluate against real evidence, feed back, retry — until the work passes or a guardrail says
stop. Today that loop is usually a human babysitting a chat window. Loop Engine turns it into
software.

```
Goal
  ↓
Planner            (sub-workflow — swap your own)
  ↓
┌──────────────── control loop (durable, capped) ──────────────────┐
│  Actor          (sub-workflow — swap your own)                   │
│    ↓                                                             │
│  Evaluate       (sub-workflow — swap your own)                   │
│    ↓            → verdict {passed, score, feedback}              │
│  Decide         (deterministic policy + guardrails — no LLM)     │
│    ↓                                                             │
│  Route ── accept · retry · replan · delegate · escalate · stop   │
└──────────────────────────────────────────────────────────────────┘
  ↓
{ status, result, score, decision_log, tokens_spent }
```

*Use agents for judgment; use workflows for control.* The planner, actor, and evaluator are
**extension points** — sub-workflows you swap by name at runtime. The control logic stays
deterministic in the engine.

## Why a workflow, not a `while` loop

- **Durable** — every iteration is a checkpoint. Kill the server mid-run; the loop resumes where
  it left off, state intact.
- **Bounded** — iteration cap, token budget, retry/replan limits, wall-clock timeout. Bad input
  is clamped; a runaway loop is structurally impossible.
- **Evidence over self-report** — an independent evaluator judges the work; the actor never marks
  its own work complete. Deterministic checks **gate** the LLM judge: if the judge loves the
  tagline but the character count says 240 against a 200 limit, it fails, and the loop retries
  with machine-measured feedback ("Too long: 240 chars, limit 200 (cut 40)").
- **Observable** — every decision (and why) lands in a `decision_log`; every run is replayable in
  the Conductor UI.

## Quickstart

```bash
# 1. Export an LLM key — the server picks it up at startup
export ANTHROPIC_API_KEY=sk-ant-...     # demos default to Anthropic
# export OPENAI_API_KEY=sk-...          # or OpenAI: set llm_provider/llm_model in the input file

# 2. Start a Conductor server (needs Java 21+; skip if you have one)
conductor server start                  # or: export CONDUCTOR_SERVER_URL=https://your-server/api

# 3. Register the workflows + sanity-check the setup (idempotent)
./quickstart.sh

# 4. Run your first loop, then watch every iteration and decision
conductor workflow start -w loop_engine -f inputs/demo-minimal.json
conductor workflow get-execution <workflowId>
```

Output:

```json
{
  "status": "succeeded",
  "result": "<the best deliverable produced>",
  "score": 0.92,
  "iterations": 3,
  "decision_log": [ { "iteration": 0, "decision": "retry", "reason": "...", "feedback": "..." } ],
  "tokens_spent": 12345
}
```

Terminal statuses: `succeeded`, `stopped_no_progress`, `stopped_budget`, `stopped_max_iterations`,
`stopped_infra_failure`, `escalated` — every run ends with an explicit reason, never a hang.

## Demos — each proves a loop behavior

| `conductor workflow start -w loop_engine -f ...` | Proves | Outcome |
|---|---|---|
| `inputs/demo-bounded-stop.json` | **No infinite loops.** Impossible constraint (5 keywords in 30 chars) | `retry → retry → retry → replan → retry`, then halts at the iteration guardrail |
| `inputs/demo-tagline.json` | Deterministic evidence gates the LLM judge (≤120 chars) | `succeeded` the moment evidence confirms the criteria |
| `inputs/demo-length-window.json` | The "models can't count" case (exactly 150–170 chars) | `succeeded` at 163 — the length check is authoritative, not the model |
| `inputs/demo-infra-failure.json` | **Survives outages.** Actor points at a nonexistent workflow | bounded infra retries, then clean `stopped_infra_failure` |
| `inputs/demo-generic.json` | The evaluator extension point is swappable | `succeeded` with the generic LLM judge |

## Plug in your own agent

Each extension point is a Conductor sub-workflow resolved **by name at runtime**. Register a
workflow with the matching contract, pass its name as input — the engine is unchanged:

- **Planner** (`planner_workflow`) — in: objective, criteria, feedback, history → out: `{ plan, tokens }`
- **Actor** (`actor_workflow`) — in: objective, plan, feedback, iteration → out: `{ result, summary, tokens }`
- **Evaluator** (`evaluator_workflow`) — in: objective, criteria, result → out: `{ passed, score, feedback, tokens }`

A custom extension that fails or returns garbage is treated as an infra failure with bounded
retries — it degrades the run, it doesn't kill it. Set `enable_human: true` to escalate to a
`HUMAN` task instead of stopping; resume with `conductor task signal`. Full contracts (every
field, plus `extension_params` passthrough) are in the [design notes](docs/design.md).

## Production examples ([`examples/`](examples/README.md))

Three real loops that reuse the engine unchanged — each supplies only an actor, an evaluator,
and Python workers for the real work:

| Example | Evidence the loop closes on |
|---|---|
| **Coding agent** — LLM writes Python, a worker executes it | real test pass/fail (sandboxed subprocess) |
| **Data-quality pipeline** — clean → check workers | deterministic data contract |
| **Refund/support agent** — lookup → refund → verify workers | the actual refund ledger, not the model's claim |

```bash
cd examples && ./register.sh
(cd workers && python run_workers.py) &
conductor workflow start -w loop_engine -f 01-coding-agent/inputs/roman-numerals.json
```

## Built on Conductor

Everything here is open-source Conductor doing the heavy lifting — the loop is the *pattern*,
Conductor is the *runtime*. Authored with the
[Conductor skills](https://github.com/conductor-oss/conductor-skills).

| What it demonstrates | Conductor primitive |
|---|---|
| Durable, restart-surviving control loop | `DO_WHILE` + workflow `variables` as checkpointed state |
| Deterministic decisions & routing | `INLINE` + `SWITCH` — no LLM in the control path |
| Swappable planner / actor / evaluator | `SUB_WORKFLOW` with a dynamically resolved name |
| LLM calls without HTTP plumbing | built-in `LLM_CHAT_COMPLETE` task |
| Real work behind the agents | SDK workers (`conductor-python`) |
| Survives its own infrastructure | `optional` tasks + `failureWorkflow` |
| Human-in-the-loop | `HUMAN` task + task signal |

## Going deeper

- **[Design notes](docs/design.md)** — the full decision policy, effort presets, guardrail
  reference, failure handling, flow diagram, and Conductor specifics.
- **Tests** — the decision policy is plain, unit-tested code (`src/decide.js`), inlined into the
  workflow JSON by `scripts/build.mjs`:

```bash
node --test 'tests/*.test.cjs'                          # policy, config, guards, JSON sync
(cd examples/workers && python3 -m unittest discover)   # the example workers
```

---

Loop Engine is the *pattern*. **[Conductor](https://github.com/conductor-oss/conductor)** is the
*runtime* — if durable agent loops are your problem, that's the repo to star.
