from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from analyse_dump import db
from analyse_dump.agent.tool_executor import ToolExecutor
from analyse_dump.agent.tool_registry import default_tool_specs
from analyse_dump.const import EDGE_PROPERTY, LANG_JS, NAME_KIND_STRING_INDEX


class AgentToolProtocolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "t.db"
        conn = db.connect(self.db_path)
        db.init_schema(conn)
        self.js_snapshot_id = db.create_snapshot(conn, "heapsnapshot", "t.heapsnapshot")
        self.kt_snapshot_id = db.create_snapshot(conn, "hprof", "t.hprof")
        db.insert_objects(
            conn,
            [
                (self.js_snapshot_id, LANG_JS, 10, "object", 0, None, None),
                (self.js_snapshot_id, LANG_JS, 20, "object", 0, None, None),
            ],
        )
        db.insert_edges(
            conn,
            [
                (self.js_snapshot_id, 20, 10, EDGE_PROPERTY, NAME_KIND_STRING_INDEX, None, "holder"),
            ],
        )
        db.insert_roots(
            conn,
            [
                (
                    self.js_snapshot_id,
                    LANG_JS,
                    20,
                    "user_root_d1",
                    "native_v8_user_root",
                    "high",
                    None,
                )
            ],
        )
        conn.commit()
        conn.close()
        self.executor = ToolExecutor(default_tool_specs())

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_execute_success_shape(self) -> None:
        out = self.executor.execute(
            "find_root_path",
            {
                "db": str(self.db_path),
                "addr": "10",
                "lang": "js",
                "js_snapshot_id": self.js_snapshot_id,
                "kt_snapshot_id": self.kt_snapshot_id,
                "roots_mode": "native",
            },
        )
        self.assertTrue(out.ok)
        self.assertIn("found", out.data)
        self.assertIn("tool_name", out.metrics)
        self.assertEqual(out.metrics["tool_name"], "find_root_path")

    def test_execute_invalid_args(self) -> None:
        out = self.executor.execute("find_root_path", {"db": str(self.db_path)})
        self.assertFalse(out.ok)
        self.assertIsNotNone(out.error)
        self.assertEqual(out.error.code, "invalid_args")  # type: ignore[union-attr]

    def test_execute_unknown_tool(self) -> None:
        out = self.executor.execute("does_not_exist", {"db": str(self.db_path)})
        self.assertFalse(out.ok)
        self.assertIsNotNone(out.error)
        self.assertEqual(out.error.code, "tool_not_found")  # type: ignore[union-attr]


if __name__ == "__main__":
    unittest.main()
