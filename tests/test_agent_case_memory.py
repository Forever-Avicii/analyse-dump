from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from analyse_dump import db
from analyse_dump.agent.agent_loop import run_agent
from analyse_dump.agent.tool_executor import ToolExecutor
from analyse_dump.agent.tool_registry import default_tool_specs
from analyse_dump.const import LANG_JS


class AgentCaseMemoryTests(unittest.TestCase):
    def test_case_memory_persists_run_record(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        db_path = Path(tmp.name) / "case.db"

        conn = db.connect(db_path)
        db.init_schema(conn)
        js_snapshot_id = db.create_snapshot(conn, "heapsnapshot", "x.heapsnapshot")
        kt_snapshot_id = db.create_snapshot(conn, "hprof", "x.hprof")
        db.insert_objects(conn, [(js_snapshot_id, LANG_JS, 10, "object", 0, None, None)])
        conn.commit()
        conn.close()

        state = run_agent(
            goal="diagnose js addr 10",
            context={
                "db": str(db_path),
                "addr": "10",
                "lang": "js",
                "js_snapshot_id": js_snapshot_id,
                "kt_snapshot_id": kt_snapshot_id,
                "persist_case_memory": True,
            },
            executor=ToolExecutor(default_tool_specs()),
            max_steps=3,
        )
        self.assertIsNotNone(state.case_id)
        conn = db.connect(db_path)
        row = conn.execute("SELECT goal, conclusion_status FROM agent_cases WHERE id = ?", (state.case_id,)).fetchone()
        conn.close()
        self.assertIsNotNone(row)
        self.assertEqual(str(row[0]), "diagnose js addr 10")


if __name__ == "__main__":
    unittest.main()

