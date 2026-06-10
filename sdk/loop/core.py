"""The Loop class: one Python file = one durable agentic loop.

Decorated functions become Conductor workers. For each role the SDK generates a
sub-workflow honoring the Loop Engine extension-point contract (one SIMPLE task
that calls your worker), registers it, and passes its name to ``loop_engine``.
The engine — not your code — owns retries, replans, budgets, and termination.

Roles (all optional; undecorated roles fall back to the engine defaults):
  @loop.pre_planner  code that runs BEFORE the planner; returns {context, plan_hints}
                     to shape the LLM planner's strategy            (a) in the proposal
  @loop.planner      replaces the LLM planner entirely; returns {plan}
  @loop.actor        produces one attempt; returns {result, summary?, tokens?} or any value
  @loop.evaluator    judges an attempt; returns {passed, score?, feedback?} or a bool

Your functions declare only the contract inputs they care about (by name):
  objective, acceptance_criteria, context, plan, feedback, iteration, history,
  effort, llm_provider, llm_model, extension_params, result, summary
"""
import inspect
import json

from .client import Conductor
from .run import Run

# Contract input keys per role — mirror the Loop Engine extension-point contracts.
ROLE_INPUTS = {
    "pre_planner": ["objective", "acceptance_criteria", "context", "effort",
                    "llm_provider", "llm_model", "extension_params"],
    "planner": ["objective", "acceptance_criteria", "context", "feedback", "history",
                "effort", "llm_provider", "llm_model", "extension_params"],
    "actor": ["objective", "acceptance_criteria", "plan", "context", "feedback",
              "iteration", "history", "effort", "llm_provider", "llm_model",
              "extension_params"],
    "evaluator": ["objective", "acceptance_criteria", "result", "summary", "context",
                  "effort", "llm_provider", "llm_model", "extension_params"],
}
ROLE_OUTPUTS = {
    "pre_planner": ["context", "plan_hints", "tokens"],
    "planner": ["plan", "tokens"],
    "actor": ["result", "summary", "tokens"],
    "evaluator": ["passed", "score", "feedback", "recommend", "checks", "tokens"],
}
# loop_engine input names that select each extension point.
ROLE_ENGINE_INPUT = {
    "pre_planner": "pre_planner_workflow",
    "planner": "planner_workflow",
    "actor": "actor_workflow",
    "evaluator": "evaluator_workflow",
}
ENGINE_KNOBS = ("effort", "max_iterations", "max_retries", "max_replans", "token_budget",
                "enable_human", "escalate_on_limit", "evaluator_llm_model",
                "delegate_actor_workflow")


def _num(v, default=0):
    try:
        return default if v is None else float(v)
    except (TypeError, ValueError):
        return default


def _call_with_supported(fn, payload):
    """Call fn with only the contract kwargs its signature declares."""
    params = inspect.signature(fn).parameters
    if any(p.kind == p.VAR_KEYWORD for p in params.values()):
        return fn(**payload)
    return fn(**{k: v for k, v in payload.items() if k in params})


# -- output adapters: be liberal in what user functions may return ----------
def adapt_actor(out):
    if not isinstance(out, dict) or "result" not in out:
        out = {"result": out}
    result = out.get("result")
    summary = out.get("summary")
    if summary is None:
        text = result if isinstance(result, str) else json.dumps(result, default=str)
        summary = text[:200]
    return {"result": result, "summary": summary, "tokens": _num(out.get("tokens"))}


def adapt_evaluator(out):
    if isinstance(out, bool):
        out = {"passed": out}
    if not isinstance(out, dict):
        raise TypeError("evaluator must return a dict (passed/score/feedback) or a bool, "
                        f"got {type(out).__name__}")
    passed = bool(out.get("passed", False))
    score = out.get("score")
    if score is None:
        score = 1.0 if passed else 0.0
    return {"passed": passed, "score": _num(score),
            "feedback": str(out.get("feedback") or ""),
            "recommend": str(out.get("recommend") or ""),
            "checks": out.get("checks"), "tokens": _num(out.get("tokens"))}


def adapt_pre_planner(out):
    if not isinstance(out, dict):
        out = {"context": out if isinstance(out, str) else json.dumps(out, default=str)}
    return {"context": str(out.get("context") or ""),
            "plan_hints": str(out.get("plan_hints") or ""),
            "tokens": _num(out.get("tokens"))}


def adapt_planner(out):
    if not isinstance(out, dict):
        out = {"plan": out if isinstance(out, str) else json.dumps(out, default=str)}
    return {"plan": str(out.get("plan") or ""), "tokens": _num(out.get("tokens"))}


ADAPTERS = {"pre_planner": adapt_pre_planner, "planner": adapt_planner,
            "actor": adapt_actor, "evaluator": adapt_evaluator}


# Workers carry the full contract signature explicitly so any conductor-python
# parameter-mapping strategy finds them; the user's fn gets only what it declared.
def _make_worker(role, fn):
    adapter = ADAPTERS[role]

    if role == "pre_planner":
        def worker(objective=None, acceptance_criteria=None, context=None, effort=None,
                   llm_provider=None, llm_model=None, extension_params=None):
            return adapter(_call_with_supported(fn, dict(
                objective=objective, acceptance_criteria=acceptance_criteria,
                context=context, effort=effort, llm_provider=llm_provider,
                llm_model=llm_model, extension_params=extension_params)))
    elif role == "planner":
        def worker(objective=None, acceptance_criteria=None, context=None, feedback=None,
                   history=None, effort=None, llm_provider=None, llm_model=None,
                   extension_params=None):
            return adapter(_call_with_supported(fn, dict(
                objective=objective, acceptance_criteria=acceptance_criteria,
                context=context, feedback=feedback, history=history, effort=effort,
                llm_provider=llm_provider, llm_model=llm_model,
                extension_params=extension_params)))
    elif role == "actor":
        def worker(objective=None, acceptance_criteria=None, plan=None, context=None,
                   feedback=None, iteration=None, history=None, effort=None,
                   llm_provider=None, llm_model=None, extension_params=None):
            return adapter(_call_with_supported(fn, dict(
                objective=objective, acceptance_criteria=acceptance_criteria, plan=plan,
                context=context, feedback=feedback, iteration=iteration, history=history,
                effort=effort, llm_provider=llm_provider, llm_model=llm_model,
                extension_params=extension_params)))
    else:  # evaluator
        def worker(objective=None, acceptance_criteria=None, result=None, summary=None,
                   context=None, effort=None, llm_provider=None, llm_model=None,
                   extension_params=None):
            return adapter(_call_with_supported(fn, dict(
                objective=objective, acceptance_criteria=acceptance_criteria,
                result=result, summary=summary, context=context, effort=effort,
                llm_provider=llm_provider, llm_model=llm_model,
                extension_params=extension_params)))

    worker.__name__ = fn.__name__
    worker.__doc__ = fn.__doc__
    return worker


class Loop:
    """A durable agentic loop defined in code. See the module docstring."""

    def __init__(self, name, objective, acceptance_criteria,
                 llm_provider=None, llm_model=None, context=None,
                 owner_email="loop-sdk@conductoross.org", role_timeout_seconds=600,
                 client=None, server_url=None, **knobs):
        bad = set(knobs) - set(ENGINE_KNOBS)
        if bad:
            raise TypeError(f"unknown loop knob(s): {', '.join(sorted(bad))} "
                            f"(valid: {', '.join(ENGINE_KNOBS)})")
        self.name = name
        self.objective = objective
        self.acceptance_criteria = acceptance_criteria
        self.llm_provider = llm_provider
        self.llm_model = llm_model
        self.context = context
        self.owner_email = owner_email
        self.role_timeout_seconds = role_timeout_seconds
        self.knobs = knobs
        self.client = client or Conductor(server_url=server_url)
        self._roles = {}            # role -> user fn
        self._workers_registered = False
        self._handler = None        # conductor-python TaskHandler

    # -- decorators ----------------------------------------------------------
    def pre_planner(self, fn):
        return self._add_role("pre_planner", fn)

    def planner(self, fn):
        return self._add_role("planner", fn)

    def actor(self, fn):
        return self._add_role("actor", fn)

    def evaluator(self, fn):
        return self._add_role("evaluator", fn)

    def _add_role(self, role, fn):
        if role in self._roles:
            raise ValueError(f"loop '{self.name}' already has a {role}")
        self._roles[role] = fn
        return fn

    # -- generated artifacts ---------------------------------------------------
    def task_name(self, role):
        return f"{self.name}_{role}_task"

    def workflow_name(self, role):
        return f"{self.name}_{role}"

    def wrapper_workflow(self, role):
        """The contract sub-workflow wrapping this role's worker task."""
        inputs = ROLE_INPUTS[role]
        return {
            "name": self.workflow_name(role),
            "description": f"Generated by the loop SDK: Python worker "
                           f"'{self.task_name(role)}' as the {role.upper()} "
                           f"for loop '{self.name}'.",
            "version": 1,
            "schemaVersion": 2,
            "ownerEmail": self.owner_email,
            "timeoutPolicy": "TIME_OUT_WF",
            "timeoutSeconds": self.role_timeout_seconds,
            "inputParameters": list(inputs),
            "tasks": [{
                "name": self.task_name(role),
                "taskReferenceName": "work",
                "type": "SIMPLE",
                "inputParameters": {k: "${workflow.input.%s}" % k for k in inputs},
            }],
            "outputParameters": {k: "${work.output.%s}" % k for k in ROLE_OUTPUTS[role]},
        }

    def engine_input(self, extension_params=None, context=None, **overrides):
        """The loop_engine input this Loop resolves to (also unit-testable)."""
        inp = {
            "objective": overrides.pop("objective", self.objective),
            "acceptance_criteria": overrides.pop("acceptance_criteria",
                                                 self.acceptance_criteria),
            "llm_provider": self.llm_provider,
            "llm_model": self.llm_model,
            "context": context if context is not None else self.context,
            "extension_params": extension_params,
        }
        for role in self._roles:
            inp[ROLE_ENGINE_INPUT[role]] = self.workflow_name(role)
        inp.update(self.knobs)
        inp.update(overrides)
        return {k: v for k, v in inp.items() if v is not None}

    # -- workers ---------------------------------------------------------------
    def _register_workers(self):
        if self._workers_registered:
            return
        from conductor.client.worker.worker_task import worker_task
        for role, fn in self._roles.items():
            worker_task(task_definition_name=self.task_name(role),
                        register_task_def=True, thread_count=2)(_make_worker(role, fn))
        self._workers_registered = True

    def start_workers(self, join=False):
        """Start polling workers for this process's decorated tasks."""
        self._register_workers()
        if self._handler is None:
            from conductor.client.automator.task_handler import TaskHandler
            from conductor.client.configuration.configuration import Configuration
            self._handler = TaskHandler(configuration=Configuration(),
                                        scan_for_annotated_workers=True)
            self._handler.start_processes()
        if join:
            self._handler.join_processes()

    def stop_workers(self):
        if self._handler is not None:
            self._handler.stop_processes()
            self._handler = None

    # -- run ---------------------------------------------------------------------
    def register(self):
        """Register the generated sub-workflows (idempotent)."""
        if self.client.get_workflow_def("loop_engine") is None:
            raise RuntimeError(
                "loop_engine is not registered on the Conductor server. "
                "From the conductor-loop repo run: ./quickstart.sh")
        for role in self._roles:
            self.client.register_workflow_def(self.wrapper_workflow(role))

    def execute(self, extension_params=None, context=None, wait=False,
                start_workers=True, **overrides):
        """Register everything, start the workers and the loop; returns a Run.

        ``overrides`` may set any loop_engine input (objective, max_iterations,
        token_budget, enable_human, ...) for this execution only.
        """
        self.register()
        if start_workers and self._roles:
            self.start_workers()
        workflow_id = self.client.start_workflow(
            "loop_engine", self.engine_input(extension_params=extension_params,
                                             context=context, **overrides))
        run = Run(self.client, workflow_id)
        if wait:
            run.wait()
        return run
