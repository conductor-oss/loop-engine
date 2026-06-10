# Loop Engine — Production Examples

Three real-world loops, all driven by the same `loop_engine` control plane (see
[`../README.md`](../README.md)). Each example only supplies its own **actor** and
**evaluator** sub-workflows and an input file — the engineered loop (plan → act →
evaluate → decide → persist → terminate) is unchanged. Two examples do real work in
**Python workers**; one has the **LLM write code that a generic Python runner executes**.

| # | Example | Actor | Evaluator (the evidence) | Python workers |
|---|---------|-------|--------------------------|----------------|
| 1 | **Coding agent** | `coding_actor` (LLM writes Python) | `coding_evaluator` → runs real tests | `python_code_runner` |
| 2 | **Data-quality pipeline** | `data_cleaning_actor` (worker cleans) | `data_quality_evaluator` → contract check | `clean_dataset`, `data_quality_check` |
| 3 | **Refund / support agent** | `refund_actor` (LLM decides, worker acts) | `refund_evaluator` → **verifies the ledger** | `account_lookup`, `issue_refund`, `verify_refund` |

The throughline is loop engineering's first principle — **evidence over self-report**:
the loop closes on what the code/data/ledger *actually shows*, never on the model's claim.

## Setup

```bash
# 1. Python workers (SDK already vendored in this env; otherwise:)
cd examples/workers && pip install -r requirements.txt

# 2. Register task definitions (retries/timeouts) and example workflows
cd examples && ./register.sh        # idempotent: create-or-update

# 3. Start the workers (long-running; Ctrl-C to stop)
cd examples/workers
export CONDUCTOR_SERVER_URL=http://localhost:8080/api
python run_workers.py
```

`run_workers.py` starts six pollers: `python_code_runner`, `clean_dataset`,
`data_quality_check`, `account_lookup`, `issue_refund`, `verify_refund`. The engine
and its default `loop_planner` are already registered (see `../workflows/`).

## Run

```bash
cd examples
conductor workflow start -w loop_engine -f 01-coding-agent/inputs/roman-numerals.json
conductor workflow start -w loop_engine -f 01-coding-agent/inputs/split-payment.json      # payment splitting; edge-case heavy
conductor workflow start -w loop_engine -f 02-data-quality/inputs/clean-customers.json
conductor workflow start -w loop_engine -f 03-refund-support/inputs/refund-case.json          # in-window: refund is correct
conductor workflow start -w loop_engine -f 03-refund-support/inputs/refund-out-of-window.json # 45 days old: escalation is correct
conductor workflow get-execution <workflowId>
```

The refund ledger is durable (deliberately — it is the system of record the evaluator
verifies). Reset it between demo runs:

```bash
(cd workers && python3 -m conductor_loop_workers.reset)
```

---

## 1. Coding agent — *LLM writes code, a generic runner executes it* (requirement c)

The actor is an LLM that emits raw Python. The evaluator calls the **generic
`python_code_runner` worker**, which executes the code against operator-supplied test
cases in a sandboxed subprocess and returns deterministic pass/fail plus the failing
assertions. On a failure the loop feeds those exact assertions back; the LLM fixes the
bug and resubmits until the tests are green or a guardrail trips.

- The runner is reusable for **any** coding task — the spec and the `cases` come from the
  loop input (`extension_params.cases`), not from the worker.
- Evidence is the actual test run, not the model saying "this works."

**Security boundary.** `python_code_runner` executes arbitrary code. It is hardened with a
subprocess in `python -I` isolated mode, a wall-clock timeout (SIGKILL), in-child
`RLIMIT_CPU`/`RLIMIT_AS`/`RLIMIT_NOFILE`, an isolated temp CWD, and a scrubbed env (no
inherited secrets). **This is adequate for a TRUSTED coding-agent loop, not for untrusted
code.** For untrusted input in production, run the candidate inside a real sandbox
(gVisor / Firecracker / nsjail / a network-less container) — a subprocess is not a
security boundary. See the header of `workers/conductor_loop_workers/code_runner.py`.

## 2. Data-quality pipeline — *Python workers do the ETL* (requirement b)

`clean_dataset` (worker) normalizes the dataset — trims, lowercases emails, coerces ages,
fills required text fields, dedupes ids — and **escalates** to dropping irreparable rows
once the evaluator has rejected a pass. `data_quality_check` (worker) enforces a
deterministic **data contract** (required fields, email format, age range, unique ids)
and returns a graded score + the specific violations. The loop iterates: light clean →
contract fails with violations → targeted aggressive clean → contract passes.

Both workers read **the same operator-supplied contract** (`extension_params.contract`):
field names and bounds are configuration, not code, so tuning the contract (say
`age_max: 65`, or renamed columns) re-targets the cleaner and the gate together — the
loop still converges. (An out-of-range value is marked invalid, never silently clamped
into compliance.)

## 3. Refund / support agent — *Python workers + verify the transaction* (requirement b)

The actor looks up the order (`account_lookup` worker), an LLM decides
`issue_refund | escalate | request_info`, and on a refund the `issue_refund` worker
writes to a ledger (**idempotent** on re-delivery; a corrected amount updates the entry).
The evaluator's `verify_refund` worker reads the **actual ledger and policy** (refund
window, max-refundable) — it will not pass a refund the agent merely *claimed*. If the
agent over-refunds or escalates a refundable case, verification fails with a precise
reason and the loop corrects.

The ledger lives in `workers/.state/store.json` — open it to *see* the recorded refund.

The seed data ships **both policy cases**: `ORD-5001` (12 days old — refunding is correct;
the customer asks for $200 on a $120 order, so over-refund protection is exercised too) and
`ORD-5002` (45 days old — **escalation** is correct; `verify_refund` fails a refund and
passes only escalation). Reset between runs with `python3 -m conductor_loop_workers.reset`.

---

## Verified runs

All inputs were run end-to-end against a live server with the workers polling:

| Input | Outcome | Iterations | Evidence |
|-------|---------|-----------|----------|
| Coding: roman-numerals | `succeeded`, score 1.0 | 1 | `python_code_runner` ran 6 test cases — all passed |
| Coding: split-payment | `succeeded`, score 1.0 | 1 | all 8 largest-remainder cases passed, incl. the cent-conservation property checks |
| Data-quality | `succeeded`, score 1.0 | 2 (`retry`→`accept`) | light clean → contract fails 0.40 with named violations → aggressive clean → 1.0 |
| Refund: in-window (customer asked $200 on a $120 order) | `succeeded`, score 1.0 | 1 | refund RF-0001 of **$120 (capped at order total)** recorded in the ledger and verified |
| Refund: out-of-window (45 days) | `succeeded`, score 1.0 | 1 | agent **escalated instead of refunding**; verifier confirms escalation is the policy-correct action; ledger untouched |

The refund loop also demonstrates **correction** when the agent over-refunds: an over-refund
fails `verify_refund` ("exceeds order total"), the feedback drives a corrected amount, and the
idempotent `issue_refund` updates the ledger entry — the loop converges instead of getting stuck.

## What you plug in vs. what's reused

Every example **reuses** `loop_engine` and its decision policy, guardrails, state, and
termination logic. To add your own loop you write only:

1. an **actor** sub-workflow — input `{objective, acceptance_criteria, plan, context,
   feedback, iteration, history, llm_provider, llm_model, extension_params}`, output
   `{result, summary, tokens}`;
2. an **evaluator** sub-workflow — input `{objective, acceptance_criteria, result,
   summary, context, ...}`, output `{passed, score, feedback, recommend, checks, tokens}`;
3. (optionally) **Python workers** for the SIMPLE tasks those sub-workflows call, plus
   their task definitions in `taskdefs/`.

Then point the engine at them via the input file's `actor_workflow` / `evaluator_workflow`.

The engine also passes an `effort` input (`default` | `medium` | `high`) to every extension point —
custom actors/evaluators may use it to scale their own work, or ignore it. A sub-workflow that
fails (or returns no `result` / an all-null verdict) is handled as a bounded **infra failure** by
the engine; it never kills the run.

## Tests

All six workers have unit tests (no server needed):

```bash
cd workers && python3 -m unittest discover -v
```

Covers the code runner's result-integrity guarantees (early-exit, stdout spoofing, namespace
isolation), the data-quality contract + light→aggressive convergence, and the refund ledger's
idempotency, audit trail, and policy rejections.
