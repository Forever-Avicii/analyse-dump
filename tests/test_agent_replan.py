from __future__ import annotations

import unittest

from analyse_dump.agent.agent_loop import run_agent
from analyse_dump.agent.planner import PlanStep
from analyse_dump.agent.replan import maybe_replan
from analyse_dump.agent.tool_executor import ToolExecutor
from analyse_dump.agent.tool_registry import default_tool_specs
from analyse_dump.agent.types import ToolError, ToolResult


class AgentReplanTests(unittest.TestCase):
    def setUp(self) -> None:
        self.executor = ToolExecutor(default_tool_specs())

    def test_replan_on_chain_inconclusive(self) -> None:
        inserted, reason = maybe_replan(
            lang="js",
            current_tool="analyze_chain",
            result=ToolResult(ok=True, data={"verdict": "inconclusive"}),
            remaining=[],
        )
        self.assertEqual(reason, "chain_inconclusive")
        self.assertEqual(len(inserted), 1)
        self.assertEqual(inserted[0].tool_name, "find_root_path")

    def test_replan_on_tool_failure(self) -> None:
        inserted, reason = maybe_replan(
            lang="js",
            current_tool="analyze_chain",
            result=ToolResult(ok=False, error=ToolError(code="tool_failure", message="x", retryable=True)),
            remaining=[PlanStep("analyze_chain", "test")],
        )
        self.assertEqual(reason, "tool_failure_fallback")
        self.assertEqual(inserted[0].tool_name, "find_root_path")

    def test_loop_records_replan_when_db_is_invalid(self) -> None:
        state = run_agent(
            goal="diagnose js addr 100",
            context={
                "db": "Z:/path/does/not/exist.db",
                "addr": "100",
                "lang": "js",
            },
            executor=self.executor,
            max_steps=3,
        )
        self.assertTrue(state.concluded)
        self.assertEqual(state.conclusion_status, "failed")
        self.assertGreaterEqual(state.replan_count, 1)


if __name__ == "__main__":
    unittest.main()

