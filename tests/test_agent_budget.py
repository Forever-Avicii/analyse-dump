from __future__ import annotations

import unittest

from analyse_dump.agent.agent_loop import run_agent
from analyse_dump.agent.tool_executor import ToolExecutor
from analyse_dump.agent.tool_registry import default_tool_specs


class AgentBudgetTests(unittest.TestCase):
    def setUp(self) -> None:
        self.executor = ToolExecutor(default_tool_specs())

    def test_stop_by_time_budget(self) -> None:
        state = run_agent(
            goal="diagnose js addr 100",
            context={"db": "placeholder.db", "addr": "100", "lang": "js"},
            executor=self.executor,
            max_steps=10,
            max_seconds=0.0,
        )
        self.assertTrue(state.concluded)
        self.assertEqual(state.conclusion_status, "inconclusive")
        self.assertIn("time budget", state.summary.lower())
        self.assertEqual(len(state.steps), 0)

    def test_stop_by_step_budget(self) -> None:
        state = run_agent(
            goal="diagnose js addr 100",
            context={"db": "placeholder.db", "addr": "100", "lang": "js"},
            executor=self.executor,
            max_steps=1,
            max_seconds=10.0,
        )
        self.assertTrue(state.concluded)
        self.assertEqual(state.conclusion_status, "inconclusive")
        self.assertIn("step budget", state.summary.lower())


if __name__ == "__main__":
    unittest.main()

