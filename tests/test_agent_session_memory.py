from __future__ import annotations

import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch

from analyse_dump.agent.agent_loop import run_agent
from analyse_dump.agent.memory.session_memory import SessionMemory
from analyse_dump.agent.planner import PlanStep
from analyse_dump.agent.tool_executor import ToolExecutor
from analyse_dump.agent.tool_registry import default_tool_specs
from analyse_dump import db


class SessionMemoryTests(unittest.TestCase):
    def test_fingerprint_stability(self) -> None:
        mem = SessionMemory()
        args1 = {"a": 1, "b": 2}
        args2 = {"b": 2, "a": 1}
        mem.record("tool_x", args1)
        self.assertTrue(mem.has_seen("tool_x", args2))

    def test_agent_skips_duplicate_tool_calls(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        db_path = Path(tmp.name) / "m.db"
        conn = db.connect(db_path)
        db.init_schema(conn)
        js_snapshot_id = db.create_snapshot(conn, "heapsnapshot", "x.heapsnapshot")
        kt_snapshot_id = db.create_snapshot(conn, "hprof", "x.hprof")
        conn.commit()
        conn.close()

        with patch(
            "analyse_dump.agent.agent_loop.create_plan",
            return_value=[
                PlanStep("find_root_path", "first"),
                PlanStep("find_root_path", "duplicate"),
            ],
        ):
            state = run_agent(
                goal="diagnose kotlin addr 100",
                context={
                    "db": str(db_path),
                    "addr": "100",
                    "lang": "kotlin",
                    "js_snapshot_id": js_snapshot_id,
                    "kt_snapshot_id": kt_snapshot_id,
                },
                executor=ToolExecutor(default_tool_specs()),
                max_steps=5,
            )
        self.assertTrue(state.concluded)
        self.assertGreaterEqual(state.dedup_skips, 1)


if __name__ == "__main__":
    unittest.main()
