"""Unit tests for the loop SDK — no Conductor server required."""
import unittest

from loop.core import (Loop, ROLE_INPUTS, ROLE_OUTPUTS, _call_with_supported,
                       _make_worker, adapt_actor, adapt_evaluator, adapt_planner,
                       adapt_pre_planner)


class FakeClient:
    def __init__(self, has_engine=True):
        self.has_engine = has_engine
        self.registered = []
        self.started = []

    def get_workflow_def(self, name):
        return {"name": name} if (self.has_engine and name == "loop_engine") else None

    def register_workflow_def(self, definition):
        self.registered.append(definition)

    def start_workflow(self, name, input_data, version=1):
        self.started.append((name, input_data))
        return "wf-123"


def make_loop(**kw):
    return Loop(name="cc_dispute", objective="resolve it",
                acceptance_criteria="ledger updated",
                llm_provider="anthropic", llm_model="claude-opus-4-7",
                client=FakeClient(), **kw)


class AdapterTests(unittest.TestCase):
    def test_actor_accepts_bare_values(self):
        out = adapt_actor("hello world")
        self.assertEqual(out["result"], "hello world")
        self.assertEqual(out["summary"], "hello world")
        self.assertEqual(out["tokens"], 0)

    def test_actor_fills_summary_and_tokens(self):
        out = adapt_actor({"result": {"a": 1}})
        self.assertEqual(out["result"], {"a": 1})
        self.assertTrue(out["summary"].startswith('{"a"'))

    def test_evaluator_accepts_bool(self):
        self.assertEqual(adapt_evaluator(True),
                         {"passed": True, "score": 1.0, "feedback": "",
                          "recommend": "", "checks": None, "tokens": 0})
        self.assertEqual(adapt_evaluator(False)["score"], 0.0)

    def test_evaluator_rejects_garbage(self):
        with self.assertRaises(TypeError):
            adapt_evaluator("looks good")

    def test_evaluator_passthrough(self):
        out = adapt_evaluator({"passed": False, "score": 0.4, "feedback": "nope",
                               "checks": {"x": True}})
        self.assertEqual(out["score"], 0.4)
        self.assertEqual(out["checks"], {"x": True})

    def test_pre_planner_accepts_string(self):
        out = adapt_pre_planner("facts here")
        self.assertEqual(out, {"context": "facts here", "plan_hints": "", "tokens": 0})

    def test_planner_accepts_string(self):
        self.assertEqual(adapt_planner("step 1")["plan"], "step 1")


class CallMatchingTests(unittest.TestCase):
    def test_only_declared_params_are_passed(self):
        def fn(feedback="", extension_params=None):
            return (feedback, extension_params)
        got = _call_with_supported(fn, {"objective": "o", "feedback": "f",
                                        "extension_params": {"k": 1}})
        self.assertEqual(got, ("f", {"k": 1}))

    def test_var_kwargs_gets_everything(self):
        def fn(**kw):
            return sorted(kw)
        got = _call_with_supported(fn, {"a": 1, "b": 2})
        self.assertEqual(got, ["a", "b"])

    def test_worker_adapts_and_filters(self):
        worker = _make_worker("evaluator", lambda result=None: result == "ok")
        out = worker(objective="o", result="ok")
        self.assertTrue(out["passed"])
        self.assertEqual(out["score"], 1.0)


class WrapperWorkflowTests(unittest.TestCase):
    def test_shape_matches_contract(self):
        loop = make_loop()
        loop.actor(lambda **kw: "x")
        wf = loop.wrapper_workflow("actor")
        self.assertEqual(wf["name"], "cc_dispute_actor")
        self.assertEqual(wf["inputParameters"], ROLE_INPUTS["actor"])
        (task,) = wf["tasks"]
        self.assertEqual(task["type"], "SIMPLE")
        self.assertEqual(task["name"], "cc_dispute_actor_task")
        self.assertEqual(task["inputParameters"]["plan"], "${workflow.input.plan}")
        self.assertEqual(set(wf["outputParameters"]), set(ROLE_OUTPUTS["actor"]))
        self.assertEqual(wf["outputParameters"]["result"], "${work.output.result}")

    def test_duplicate_role_rejected(self):
        loop = make_loop()
        loop.actor(lambda **kw: "x")
        with self.assertRaises(ValueError):
            loop.actor(lambda **kw: "y")

    def test_llm_actor_generates_chat_complete_workflow(self):
        loop = make_loop()
        loop.llm_actor(system_prompt="Output ONLY Python.", temperature=0.1,
                       max_tokens=2500)
        wf = loop.llm_actor_workflow()
        self.assertEqual(wf["name"], "cc_dispute_actor")
        gen = wf["tasks"][0]
        self.assertEqual(gen["type"], "LLM_CHAT_COMPLETE")
        self.assertEqual(gen["inputParameters"]["maxTokens"], 2500)
        self.assertEqual(gen["inputParameters"]["messages"][0]["message"],
                         "Output ONLY Python.")
        self.assertIn("${workflow.input.feedback}",
                      gen["inputParameters"]["messages"][1]["message"])
        self.assertEqual(wf["outputParameters"]["tokens"], "${generate.output.tokenUsed}")

    def test_llm_actor_conflicts_with_code_actor(self):
        loop = make_loop()
        loop.llm_actor(system_prompt="s")
        with self.assertRaises(ValueError):
            loop.actor(lambda **kw: "x")
        loop2 = make_loop()
        loop2.actor(lambda **kw: "x")
        with self.assertRaises(ValueError):
            loop2.llm_actor(system_prompt="s")


class ExecuteTests(unittest.TestCase):
    def test_engine_input_wires_defined_roles_only(self):
        loop = make_loop(max_iterations=4, enable_human=True)
        loop.pre_planner(lambda **kw: "ctx")
        loop.actor(lambda **kw: "x")
        loop.evaluator(lambda **kw: True)
        inp = loop.engine_input(extension_params={"case_id": "D-1"})
        self.assertEqual(inp["pre_planner_workflow"], "cc_dispute_pre_planner")
        self.assertEqual(inp["actor_workflow"], "cc_dispute_actor")
        self.assertEqual(inp["evaluator_workflow"], "cc_dispute_evaluator")
        self.assertNotIn("planner_workflow", inp)  # undecorated -> engine default
        self.assertEqual(inp["max_iterations"], 4)
        self.assertEqual(inp["enable_human"], True)
        self.assertEqual(inp["extension_params"], {"case_id": "D-1"})

    def test_llm_actor_wired_into_engine_input_and_registration(self):
        loop = make_loop()
        loop.llm_actor(system_prompt="s")
        loop.evaluator(lambda **kw: True)
        inp = loop.engine_input()
        self.assertEqual(inp["actor_workflow"], "cc_dispute_actor")
        loop.execute(start_workers=False)
        self.assertEqual(sorted(d["name"] for d in loop.client.registered),
                         ["cc_dispute_actor", "cc_dispute_evaluator"])

    def test_execute_registers_and_starts(self):
        loop = make_loop()
        loop.actor(lambda **kw: "x")
        run = loop.execute(start_workers=False, max_iterations=2)
        self.assertEqual(run.id, "wf-123")
        self.assertEqual([d["name"] for d in loop.client.registered], ["cc_dispute_actor"])
        name, inp = loop.client.started[0]
        self.assertEqual(name, "loop_engine")
        self.assertEqual(inp["max_iterations"], 2)

    def test_execute_fails_clearly_without_engine(self):
        loop = Loop(name="x", objective="o", acceptance_criteria="c",
                    llm_provider="p", llm_model="m", client=FakeClient(has_engine=False))
        with self.assertRaisesRegex(RuntimeError, "quickstart"):
            loop.execute(start_workers=False)

    def test_unknown_knob_rejected_at_definition(self):
        with self.assertRaisesRegex(TypeError, "max_iteration "):
            make_loop(**{"max_iteration ": 5})


if __name__ == "__main__":
    unittest.main()
