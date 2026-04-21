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


def _build_case(db_path: Path, with_loop: bool) -> tuple[int, int]:
    conn = db.connect(db_path)
    db.init_schema(conn)
    js_snapshot_id = db.create_snapshot(conn, "heapsnapshot", "test.heapsnapshot")
    kt_snapshot_id = db.create_snapshot(conn, "hprof", "test.hprof")

    db.insert_strings(
        conn,
        [
            (js_snapshot_id, 1, "knapi_refs_test"),
            (js_snapshot_id, 2, "0x10"),
        ],
    )
    db.insert_objects(
        conn,
        [
            (js_snapshot_id, LANG_JS, 100, "object", 0, None, None),
            (js_snapshot_id, LANG_JS, 200, "object", 0, None, None),
            (js_snapshot_id, LANG_JS, 300, "array", 0, None, None),
            (js_snapshot_id, LANG_JS, 400, "string", 0, None, 2),
            (kt_snapshot_id, LANG_KOTLIN, 500, "KtOwner", 0, None, None),
            (kt_snapshot_id, LANG_KOTLIN, 600, "kotlin.native.internal.StableRef", 0, None, None),
        ],
    )
    db.insert_edges(
        conn,
        [
            (js_snapshot_id, 200, 100, EDGE_PROPERTY, NAME_KIND_STRING_INDEX, 1, None),
            (js_snapshot_id, 100, 300, EDGE_PROPERTY, NAME_KIND_STRING_INDEX, 1, None),
            (js_snapshot_id, 300, 400, EDGE_ELEMENT, NAME_KIND_ARRAY_INDEX, 0, None),
            (kt_snapshot_id, 600, 500, EDGE_FIELD, NAME_KIND_FIELD_NAME, None, "referent"),
        ],
    )
    db.insert_object_fields(
        conn,
        [
            (kt_snapshot_id, LANG_KOTLIN, 500, "ref", 0x10, "16", "long"),
        ],
    )
    db.insert_roots(
        conn,
        [
            (js_snapshot_id, LANG_JS, 200, "user_root_d1", "native_v8_user_root", "high", None),
            (kt_snapshot_id, LANG_KOTLIN, 600, "native_hprof_root", "native_hprof_root", "medium", None),
        ],
    )

    xrefs = [
        (js_snapshot_id, LANG_JS, 0x10, 100, REF_KIND_NAPI_REF),
    ]
    if with_loop:
        xrefs.extend(
            [
                (kt_snapshot_id, LANG_KOTLIN, 0x20, 600, REF_KIND_STABLE_REF),
                (js_snapshot_id, LANG_JS, 0x20, 100, REF_KIND_STABLE_REF),
            ]
        )
    conn.executemany(
        """
        INSERT INTO xrefs(snapshot_id, lang, ref_addr, owner_obj_addr, ref_kind)
        VALUES (?, ?, ?, ?, ?)
        """,
        xrefs,
    )
    conn.commit()
    conn.close()
    return js_snapshot_id, kt_snapshot_id


class AgentBaselineScenarioTests(unittest.TestCase):
    def setUp(self) -> None:
        self.executor = ToolExecutor(default_tool_specs())

    def test_loop_detected_case(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        db_path = Path(tmp.name) / "loop.db"
        js_snapshot_id, kt_snapshot_id = _build_case(db_path, with_loop=True)
        state = run_agent(
            goal="diagnose leak for js addr 100",
            context={
                "db": str(db_path),
                "addr": "100",
                "lang": "js",
                "js_snapshot_id": js_snapshot_id,
                "kt_snapshot_id": kt_snapshot_id,
            },
            executor=self.executor,
            max_steps=4,
        )
        self.assertEqual(state.conclusion_status, "confirmed")
        self.assertIn("loop", state.summary.lower())

    def test_terminal_root_case(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        db_path = Path(tmp.name) / "terminal.db"
        js_snapshot_id, kt_snapshot_id = _build_case(db_path, with_loop=False)
        state = run_agent(
            goal="diagnose leak for js addr 100",
            context={
                "db": str(db_path),
                "addr": "100",
                "lang": "js",
                "js_snapshot_id": js_snapshot_id,
                "kt_snapshot_id": kt_snapshot_id,
            },
            executor=self.executor,
            max_steps=4,
        )
        self.assertEqual(state.conclusion_status, "confirmed")
        self.assertIn("terminal root", state.summary.lower())

    def test_inconclusive_without_addr_lang(self) -> None:
        state = run_agent(
            goal="help me debug memory",
            context={"db": "placeholder.db"},
            executor=self.executor,
            max_steps=3,
        )
        self.assertEqual(state.conclusion_status, "inconclusive")
        self.assertEqual(len(state.steps), 0)


if __name__ == "__main__":
    unittest.main()

