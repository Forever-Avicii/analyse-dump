from __future__ import annotations

import unittest

from analyse_dump.agent.state import AgentState, AgentStep
from analyse_dump.agent.types import ToolResult
from analyse_dump.agent.verifier import verify_state


class AgentVerifierTests(unittest.TestCase):
    def test_confirmed_by_loop_detected(self) -> None:
        state = AgentState(goal="g", context={})
        state.concluded = True
        state.conclusion_status = "inconclusive"
        state.steps.append(
            AgentStep(
                step=1,
                tool_name="analyze_chain",
                args={},
                result=ToolResult(ok=True, data={"verdict": "loop_detected"}),
            )
        )
        verify_state(state)
        self.assertEqual(state.conclusion_status, "confirmed")
        self.assertEqual(state.confidence, "high")

    def test_hypothesis_by_js_property_evidence(self) -> None:
        state = AgentState(goal="g", context={})
        state.concluded = True
        state.conclusion_status = "inconclusive"
        state.steps.append(
            AgentStep(
                step=1,
                tool_name="inspect_js_props",
                args={},
                result=ToolResult(ok=True, data={"found": True}),
            )
        )
        verify_state(state)
        self.assertEqual(state.conclusion_status, "hypothesis")
        self.assertEqual(state.confidence, "medium")

    def test_failed_status_is_kept(self) -> None:
        state = AgentState(goal="g", context={})
        state.concluded = True
        state.conclusion_status = "failed"
        verify_state(state)
        self.assertEqual(state.conclusion_status, "failed")


if __name__ == "__main__":
    unittest.main()

