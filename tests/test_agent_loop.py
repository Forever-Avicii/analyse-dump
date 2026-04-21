from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from analyse_dump import db
from analyse_dump.agent.agent_loop import run_agent
from analyse_dump.agent.tool_executor import ToolExecutor
from analyse_dump.agent.tool_registry import default_tool_specs
from analyse_dump.const import (
    EDGE_ELEMENT,
    EDGE_FIELD,
    EDGE_PROPERTY,
    LANG_JS,
    LANG_KOTLIN,
    NAME_KIND_ARRAY_INDEX,
    NAME_KIND_FIELD_NAME,
    NAME_KIND_STRING_INDEX,
    REF_KIND_NAPI_REF,
    REF_KIND_STABLE_REF,
)


class AgentLoopTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "t.db"
        conn = db.connect(self.db_path)
        db.init_schema(conn)

        self.js_snapshot_id = db.create_snapshot(conn, "heapsnapshot", "test.heapsnapshot")
        self.kt_snapshot_id = db.create_snapshot(conn, "hprof", "test.hprof")

        db.insert_strings(
            conn,
            [
                (self.js_snapshot_id, 1, "knapi_refs_test"),
                (self.js_snapshot_id, 2, "0x10"),
            ],
        )
        db.insert_objects(
            conn,
            [
                (self.js_snapshot_id, LANG_JS, 100, "object", 0, None, None),
                (self.js_snapshot_id, LANG_JS, 200, "object", 0, None, None),
                (self.js_snapshot_id, LANG_JS, 300, "array", 0, None, None),
                (self.js_snapshot_id, LANG_JS, 400, "string", 0, None, 2),
                (self.kt_snapshot_id, LANG_KOTLIN, 500, "KtOwner", 0, None, None),
                (self.kt_snapshot_id, LANG_KOTLIN, 600, "kotlin.native.internal.StableRef", 0, None, None),
            ],
        )
        db.insert_edges(
            conn,
            [
                (self.js_snapshot_id, 200, 100, EDGE_PROPERTY, NAME_KIND_STRING_INDEX, 1, None),
                (self.js_snapshot_id, 100, 300, EDGE_PROPERTY, NAME_KIND_STRING_INDEX, 1, None),
                (self.js_snapshot_id, 300, 400, EDGE_ELEMENT, NAME_KIND_ARRAY_INDEX, 0, None),
                (self.kt_snapshot_id, 600, 500, EDGE_FIELD, NAME_KIND_FIELD_NAME, None, "referent"),
            ],
        )
        db.insert_object_fields(
            conn,
            [
                (self.kt_snapshot_id, LANG_KOTLIN, 500, "ref", 0x10, "16", "long"),
            ],
        )
        db.insert_roots(
            conn,
            [
                (self.js_snapshot_id, LANG_JS, 200, "user_root_d1", "native_v8_user_root", "high", None),
                (self.kt_snapshot_id, LANG_KOTLIN, 600, "native_hprof_root", "native_hprof_root", "medium", None),
            ],
        )
        conn.executemany(
            """
            INSERT INTO xrefs(snapshot_id, lang, ref_addr, owner_obj_addr, ref_kind)
            VALUES (?, ?, ?, ?, ?)
            """,
            [
                (self.js_snapshot_id, LANG_JS, 0x10, 100, REF_KIND_NAPI_REF),
                (self.kt_snapshot_id, LANG_KOTLIN, 0x20, 600, REF_KIND_STABLE_REF),
                (self.js_snapshot_id, LANG_JS, 0x20, 100, REF_KIND_STABLE_REF),
            ],
        )
        conn.commit()
        conn.close()
        self.executor = ToolExecutor(default_tool_specs())

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_agent_loop_detects_loop(self) -> None:
        state = run_agent(
            goal="Analyze leak for js addr 100",
            context={
                "db": str(self.db_path),
                "addr": "100",
                "lang": "js",
                "js_snapshot_id": self.js_snapshot_id,
                "kt_snapshot_id": self.kt_snapshot_id,
                "roots_mode": "native",
            },
            executor=self.executor,
            max_steps=4,
        )
        self.assertTrue(state.concluded)
        self.assertEqual(state.conclusion_status, "confirmed")
        self.assertEqual(state.confidence, "high")
        self.assertGreaterEqual(len(state.steps), 1)
        self.assertEqual(state.steps[0].tool_name, "analyze_chain")


if __name__ == "__main__":
    unittest.main()

