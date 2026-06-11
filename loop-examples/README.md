# Loop Engine — Examples (built with the `loop` SDK)

Three real-world loops, each **one Python file**, all driven by the same `loop_engine`
control plane. The [`loop` SDK](../sdk/README.md) turns the decorated functions into
Conductor workers, generates the contract sub-workflows, and starts the run — the
engineered loop (plan → act → evaluate → decide → persist → terminate) is reused unchanged.

| File | Actor | Evaluator (the evidence) |
|---|---|---|
| [`coding_agent.py`](coding_agent.py) | `llm_actor` — a prompted LLM writes Python | runs the code against real tests in a sandbox |
| [`data_quality.py`](data_quality.py) | code — cleans the dataset, escalating on feedback | deterministic data contract |
| [`refund_support.py`](refund_support.py) | code — applies refund policy, writes the ledger | **re-reads the actual ledger**, never the claim |

The throughline is loop engineering's first principle — **evidence over self-report**:
the loop closes on what the code/data/ledger *actually shows*, never on the model's claim.

## Setup & run

```bash
# once per server: registers loop_engine (../quickstart.sh from the repo root)
pip install -e ../sdk

python coding_agent.py roman           # LLM writes roman_to_int; tests are the judge
python coding_agent.py payments        # allocate_cents — edge-case heavy
python data_quality.py                 # light clean -> violations -> aggressive -> pass
python refund_support.py in-window     # ORD-5001: refund, capped at the order total
python refund_support.py out-of-window # ORD-5002: 45 days old — escalation is correct
python3 datastore.py reset             # reset the refund ledger between runs
```

Each script prints the workflowId, streams every iteration's decision via `run.watch()`,
and prints the final result. Open the workflowId in the Conductor UI to replay the run.

## 1. Coding agent — *LLM writes code, real tests judge it*

The actor is a prompted LLM (`coding.llm_actor(system_prompt=...)` — the SDK generates
the `LLM_CHAT_COMPLETE` sub-workflow; no worker, no JSON). The evaluator executes the
candidate against operator-supplied test cases (`extension_params.cases`) in a sandboxed
subprocess and feeds the exact failing assertions back. On a failure the LLM fixes those
specific bugs and resubmits until the tests are green or a guardrail trips.

**Security boundary.** The runner ([`code_runner.py`](code_runner.py)) executes arbitrary
code. It is hardened with a subprocess in `python -I` isolated mode, a wall-clock timeout
(SIGKILL), in-child `RLIMIT_CPU`/`RLIMIT_AS`/`RLIMIT_NOFILE`, an isolated temp CWD, a
scrubbed env, and out-of-band nonce'd results the candidate can't spoof. **Adequate for a
TRUSTED coding-agent loop, not for untrusted code** — in production wrap it in a real
sandbox (gVisor / Firecracker / nsjail / a network-less container).

## 2. Data-quality pipeline — *code does the ETL, a contract is the gate*

The actor cleans the dataset — trims, lowercases emails, coerces ages, fills required
text fields, dedupes ids — and **escalates** to dropping irreparable rows once the
evaluator has rejected a pass. The evaluator enforces a deterministic **data contract**
(required fields, email format, age range, unique ids) and returns a graded score plus
the specific violations. Watch the loop converge: light clean → contract fails with named
violations → aggressive clean → pass.

Both roles read **the same operator-supplied contract** (`extension_params.contract`):
field names and bounds are configuration, not code, so tuning the contract (say
`age_max: 65`, or renamed columns) re-targets the cleaner and the gate together. An
out-of-range value is marked invalid, never silently clamped into compliance.

## 3. Refund / support agent — *the ledger is the evidence*

Three roles, one file:

- the **pre-planner** (the engine's code-shapes-the-planner extension point) looks up
  the account, order, and policy facts so the LLM planner strategizes from real data;
- the **actor** applies the refund policy and writes through `issue_refund` — validated
  at write time, **idempotent** on re-delivery (Conductor redelivering a task never
  double-pays), with corrections preserved in a `revisions` audit trail;
- the **evaluator** (`verify_refund`) independently re-reads the **actual ledger and
  policy** — it will not pass a refund the actor merely *claimed*, an over-refund, or an
  unnecessary escalation.

The ledger is durable (deliberately — it is the system of record) and human-inspectable
at `.state/store.json`. The seed ships both policy cases: `ORD-5001` (12 days old —
refund is correct; the customer asks $200 on a $120 order, so over-refund capping is
exercised too) and `ORD-5002` (45 days old — **escalation** is correct).

## What you write vs. what's reused

Every example reuses `loop_engine` — its decision policy, guardrails, state, and
termination. You write only the judgment: a function per role, decorated onto a `Loop`.
The SDK handles task defs, sub-workflow generation, registration, workers, and
monitoring; the engine treats a role that fails or returns garbage as a bounded **infra
failure**, never a crashed run. See the [SDK README](../sdk/README.md) for the role
contracts and `Run` API.

## Tests

All role logic is plain Python with unit tests (no server needed):

```bash
cd loop-examples && python3 -m unittest discover -v
```

Covers the code runner's result-integrity guarantees (early-exit, stdout spoofing,
namespace isolation), the data-quality contract + light→aggressive convergence, and the
refund ledger's idempotency, audit trail, and policy rejections.
