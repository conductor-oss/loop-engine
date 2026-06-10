"""loop — write durable agentic loops as plain Python.

A Loop is one file: decorate plain functions as the loop's pre_planner / planner /
actor / evaluator, then ``execute()``. The functions run as Conductor workers, the
SDK generates the contract sub-workflows around them, and the durable ``loop_engine``
workflow (see the repo README) owns control: evaluate -> decide -> retry / replan /
delegate / escalate / stop — bounded and observable.

    from loop import Loop

    dispute = Loop(name="credit_card_dispute",
                   objective="Resolve credit card dispute {case_id}.",
                   acceptance_criteria="Ledger reflects the decision.",
                   llm_provider="anthropic", llm_model="claude-opus-4-7")

    @dispute.actor
    def resolve(plan="", feedback="", extension_params=None):
        ...
        return {"result": decision}

    run = dispute.execute(extension_params={"case_id": "D-1001"})
    run.watch()
"""
from .core import Loop
from .run import Run
from .client import Conductor, ConductorError

__version__ = "0.1.0"
__all__ = ["Loop", "Run", "Conductor", "ConductorError"]
