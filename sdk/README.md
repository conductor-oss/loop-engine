# loop — durable agentic loops as plain Python

A **loop** is an agentic program: resolve a credit-card dispute, review a PR, onboard a
customer. You write the judgment-specific parts as plain Python functions in one file; the
durable control loop — retries, replans, token budgets, escalation, termination — is the
[Loop Engine](../README.md) running on Conductor. Your functions execute as Conductor
workers, so every attempt and decision is durable and visible in the Conductor UI.

```bash
pip install -e sdk/          # from the repo root
./quickstart.sh              # registers loop_engine (once per server)
python sdk/examples/credit_card.py
```

## The shape of a loop

```python
from loop import Loop

dispute = Loop(
    name="credit_card_dispute",
    objective="Resolve the dispute identified by extension_params.case_id.",
    acceptance_criteria="The ledger reflects a policy-correct decision.",
    llm_provider="anthropic", llm_model="claude-opus-4-7",
    max_iterations=4,                      # any engine knob can be set here
)

@dispute.pre_planner                       # code that shapes the LLM planner
def gather_case(extension_params=None):
    return {"context": "...case facts...", "plan_hints": "...constraints..."}

@dispute.actor                             # does the work; runs as a worker
def resolve(plan="", feedback="", extension_params=None):
    ...
    return {"result": decision}            # or just `return decision`

@dispute.evaluator                         # judges system state, not claims
def verify(extension_params=None):
    return {"passed": ok, "score": 1.0 if ok else 0.5, "feedback": "..."}

run = dispute.execute(extension_params={"case_id": "D-1001"})
out = run.watch()                          # live decision log until terminal
```

Every role is optional. Undecorated roles fall back to the engine defaults (LLM planner,
LLM actor, LLM judge) — so the smallest loop is just a `Loop(...)` plus `execute()`.

## Roles and what they return

| Decorator | Runs | Return (adapters accept simpler forms) |
|---|---|---|
| `@loop.pre_planner` | before every plan/replan; **influences** the LLM planner | `{context, plan_hints}` or a string |
| `@loop.planner` | **replaces** the LLM planner | `{plan}` or a string |
| `@loop.actor` | once per iteration | `{result, summary?, tokens?}` or any value |
| `@loop.evaluator` | judges each attempt | `{passed, score?, feedback?, checks?}` or a bool |

Functions declare only the contract inputs they want, by name: `objective`,
`acceptance_criteria`, `context`, `plan`, `feedback`, `iteration`, `history`, `effort`,
`llm_provider`, `llm_model`, `extension_params` (+ `result`, `summary` for evaluators).
`feedback` carries the evaluator's last verdict — that is the loop closing.

## Running and monitoring

```python
run = loop.execute(extension_params={...},   # free-form params, reach every role
                   wait=False,               # True blocks until terminal
                   max_iterations=6, ...)    # per-run engine knob overrides

run.id              # Conductor workflowId (open it in the UI)
run.status          # RUNNING / COMPLETED / FAILED / ...
run.loop_status     # succeeded / stopped_no_progress / stopped_budget / escalated ...
run.result          # best deliverable so far
run.decision_log    # every iteration's decision + reason (live while running)
run.watch()         # stream decisions to stdout; returns the final output
run.wait()          # block until terminal
run.signal(status="running", feedback="try X")   # answer a human escalation
run.terminate()
```

`execute()` is idempotent on the metadata side: it (re)registers the generated
sub-workflows and task definitions, starts polling workers in-process, then starts the
workflow. For production, split the two halves — run `loop.start_workers(join=True)` in a
dedicated worker deployment, and call `execute(start_workers=False)` from wherever runs
are triggered.

## Configuration

The SDK reads the same environment as the conductor CLI:

```bash
export CONDUCTOR_SERVER_URL=http://localhost:8080/api   # default
export CONDUCTOR_AUTH_TOKEN=...                          # only if your server needs it
```

## How it works (no magic)

For each decorated role the SDK: (1) registers your function as a Conductor worker task
named `{loop}_{role}_task`, (2) generates and registers a sub-workflow `{loop}_{role}`
that satisfies the engine's extension-point contract, and (3) passes that name to
`loop_engine` as `{role}_workflow`. Everything is inspectable on the server — the
generated workflows are ordinary Conductor metadata, and a run of your loop is an
ordinary durable execution.
