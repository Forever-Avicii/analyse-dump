from __future__ import annotations

import unittest

from analyse_dump.agent.planner import create_plan


class AgentPlannerTests(unittest.TestCase):
    def test_default_js_plan_starts_with_chain(self) -> None:
        plan = create_plan("diagnose leak for js", context={}, lang="js")
        self.assertGreaterEqual(len(plan), 3)
        self.assertEqual(plan[0].tool_name, "analyze_chain")
        self.assertEqual(plan[1].tool_name, "find_root_path")
        self.assertEqual(plan[2].tool_name, "inspect_js_props")

    def test_root_path_intent_prioritizes_path_tool(self) -> None:
        plan = create_plan("find root path for object", context={}, lang="js")
        self.assertEqual(plan[0].tool_name, "find_root_path")
        self.assertEqual(plan[1].tool_name, "analyze_chain")


if __name__ == "__main__":
    unittest.main()

