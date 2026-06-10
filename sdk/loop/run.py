"""Run — a handle on one durable loop execution.

Monitoring reads the engine's checkpointed state (workflow variables + output),
so everything you see here is also visible in the Conductor UI for the same id.
"""
import time

TERMINAL = {"COMPLETED", "FAILED", "TERMINATED", "TIMED_OUT"}


class Run:
    def __init__(self, client, workflow_id):
        self.client = client
        self.id = workflow_id

    def get(self, include_tasks=False):
        """The raw execution JSON (status, variables, output, optionally tasks)."""
        return self.client.get_execution(self.id, include_tasks=include_tasks)

    # -- convenience views ---------------------------------------------------
    @property
    def status(self):
        """Conductor execution status: RUNNING, COMPLETED, FAILED, ..."""
        return self.get()["status"]

    @property
    def output(self):
        """The loop's output (status, result, score, decision_log, ...); {} while running."""
        return self.get().get("output") or {}

    @property
    def result(self):
        return self.output.get("result")

    @property
    def loop_status(self):
        """The engine's terminal status: succeeded, stopped_*, escalated."""
        return self.output.get("status")

    @property
    def decision_log(self):
        ex = self.get()
        log = (ex.get("output") or {}).get("decision_log")
        if log is None:  # still running: read the live checkpointed variable
            log = (ex.get("variables") or {}).get("history") or []
        return log

    def is_done(self):
        return self.status in TERMINAL

    # -- blocking helpers -----------------------------------------------------
    def wait(self, timeout=900, poll=2.0):
        """Block until the loop terminates; returns self. Raises on timeout."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.is_done():
                return self
            time.sleep(poll)
        raise TimeoutError(f"loop run {self.id} still {self.status} after {timeout}s")

    def watch(self, timeout=900, poll=2.0, printer=print):
        """Stream each iteration's decision as it happens; returns the final output."""
        printer(f"watching {self.id}")
        seen = 0
        deadline = time.monotonic() + timeout
        while True:
            ex = self.get()
            log = ((ex.get("output") or {}).get("decision_log")
                   or (ex.get("variables") or {}).get("history") or [])
            for e in log[seen:]:
                printer(f"  iter {e.get('iteration')}: {e.get('decision')} "
                        f"(score {e.get('score')}) — {e.get('reason')}")
            seen = len(log)
            if ex["status"] in TERMINAL:
                out = ex.get("output") or {}
                printer(f"done: {ex['status']} / {out.get('status')} "
                        f"(score {out.get('score')}, {out.get('tokens_spent')} tokens)")
                return out
            if time.monotonic() > deadline:
                raise TimeoutError(f"loop run {self.id} still running after {timeout}s")
            time.sleep(poll)

    # -- control ----------------------------------------------------------------
    def signal(self, status="running", feedback=""):
        """Answer a pending human escalation (the engine's HUMAN task).

        status: 'running' to continue with new guidance, 'stopped' to halt.
        """
        ex = self.get(include_tasks=True)
        human = [t for t in ex.get("tasks") or []
                 if t.get("taskType") == "HUMAN" and t.get("status") == "IN_PROGRESS"]
        if not human:
            raise RuntimeError(f"run {self.id} has no pending HUMAN task to signal")
        task = human[-1]
        self.client.update_task({
            "workflowInstanceId": self.id,
            "taskId": task["taskId"],
            "status": "COMPLETED",
            "workerId": "loop-sdk",
            "outputData": {"status": status, "feedback": feedback},
        })
        return self

    def terminate(self, reason="terminated via loop SDK"):
        self.client.terminate_workflow(self.id, reason=reason)
        return self
