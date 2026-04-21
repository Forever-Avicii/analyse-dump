from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from analyse_dump import db
from analyse_dump.chain_analyzer import analyze_chain
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
from analyse_dump.root_distance import build_root_distance
from analyse_dump.root_path_finder import find_root_path


class AnalysisFlowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "t.db"
        conn = db.connect(self.db_path)
        db.init_schema(conn)

        self.js_snapshot_id = db.create_snapshot(conn, "heapsnapshot", "test.heapsnapshot")
        self.kt_snapshot_id = db.create_snapshot(conn, "hprof", "test.hprof")

        # JS side nodes
        # 100(start) -> 200(root)
        # 100 has knapi_refs_test property -> 300(array) -> 400(string "0x10")
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
            ],
        )
        db.insert_edges(
            conn,
            [
                (self.js_snapshot_id, 200, 100, EDGE_PROPERTY, NAME_KIND_STRING_INDEX, 1, None),
                (self.js_snapshot_id, 100, 300, EDGE_PROPERTY, NAME_KIND_STRING_INDEX, 1, None),
                (self.js_snapshot_id, 300, 400, EDGE_ELEMENT, NAME_KIND_ARRAY_INDEX, 0, None),
            ],
        )
        db.insert_roots(
            conn,
            [
                (
                    self.js_snapshot_id,
                    LANG_JS,
                    200,
                    "user_root_d1",
                    "native_v8_user_root",
                    "high",
                    None,
                )
            ],
        )

        # Kotlin side nodes
        # 600(root) -> 500(owner)
        db.insert_objects(
            conn,
            [
                (self.kt_snapshot_id, LANG_KOTLIN, 500, "KtOwner", 0, None, None),
                (self.kt_snapshot_id, LANG_KOTLIN, 600, "kotlin.native.internal.StableRef", 0, None, None),
            ],
        )
        db.insert_edges(
            conn,
            [
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
                (
                    self.kt_snapshot_id,
                    LANG_KOTLIN,
                    600,
                    "native_hprof_root",
                    "native_hprof_root",
                    "medium",
                    None,
                )
            ],
        )

        # Cross-language xrefs for bridge loop:
        # JS start(100) --napi_ref(0x10)--> KT owner(500)
        # KT root(600) --stable_ref(0x20)--> JS start(100)
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

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_find_root_path_js(self) -> None:
        result = find_root_path(
            db_path=self.db_path,
            addr="100",
            lang="js",
            js_snapshot_id=self.js_snapshot_id,
            kt_snapshot_id=self.kt_snapshot_id,
            roots_mode="native",
            max_depth=8,
            max_fanout=64,
            use_cache=False,
        )
        self.assertTrue(result["found"])
        self.assertEqual(result["root"].addr, 200)  # type: ignore[attr-defined]
        self.assertEqual(len(result["path"]), 1)

    def test_build_root_distance_js(self) -> None:
        out = build_root_distance(
            db_path=self.db_path,
            lang="js",
            snapshot_id=self.js_snapshot_id,
            roots_mode="native",
            engine="memory",
            profile="test_profile",
            max_fanout=64,
        )
        self.assertEqual(out["roots"], 1)
        conn = db.connect(self.db_path)
        row_start = conn.execute(
            """
            SELECT dist
            FROM root_distance_cache
            WHERE snapshot_id=? AND lang=? AND profile=? AND obj_addr=?
            """,
            (self.js_snapshot_id, LANG_JS, "test_profile", 100),
        ).fetchone()
        row_root = conn.execute(
            """
            SELECT dist
            FROM root_distance_cache
            WHERE snapshot_id=? AND lang=? AND profile=? AND obj_addr=?
            """,
            (self.js_snapshot_id, LANG_JS, "test_profile", 200),
        ).fetchone()
        conn.close()
        self.assertIsNotNone(row_start)
        self.assertIsNotNone(row_root)
        self.assertEqual(int(row_start[0]), 1)  # type: ignore[index]
        self.assertEqual(int(row_root[0]), 0)  # type: ignore[index]

    def test_analyze_chain_loop_detected(self) -> None:
        result = analyze_chain(
            db_path=self.db_path,
            addr="100",
            lang="js",
            js_snapshot_id=self.js_snapshot_id,
            kt_snapshot_id=self.kt_snapshot_id,
            roots_mode="native",
            max_steps=4,
            max_depth=8,
            max_fanout=64,
            top_k=1,
        )
        self.assertEqual(result["verdict"], "loop_detected")
        bridges = result.get("bridges", [])
        self.assertGreaterEqual(len(bridges), 2)


if __name__ == "__main__":
    unittest.main()

