from __future__ import annotations

from pathlib import Path
import json
from typing import Dict, Optional

from analyse_dump import db
from analyse_dump.const import LANG_JS, LANG_KOTLIN


def _fetch_latest_snapshot_id(conn, snapshot_type: str) -> Optional[int]:
    row = conn.execute(
        "SELECT id FROM snapshots WHERE type = ? ORDER BY id DESC LIMIT 1",
        (snapshot_type,),
    ).fetchone()
    if row is None:
        return None
    return int(row[0])


def build_roots(
    db_path: Path,
    js_snapshot_id: Optional[int] = None,
    kt_snapshot_id: Optional[int] = None,
    roots_mode: str = "mixed",
) -> Dict[str, object]:
    mode = roots_mode.strip().lower()
    if mode not in {"native", "heuristic", "mixed"}:
        raise ValueError("--roots-mode must be native, heuristic, or mixed")

    conn = db.connect(db_path)
    try:
        db.init_schema(conn)

        if js_snapshot_id is None:
            js_snapshot_id = _fetch_latest_snapshot_id(conn, "heapsnapshot")
        if kt_snapshot_id is None:
            kt_snapshot_id = _fetch_latest_snapshot_id(conn, "hprof")

        inserted_js = 0
        inserted_kt = 0

        if js_snapshot_id is not None:
            conn.execute("DELETE FROM roots WHERE snapshot_id = ? AND lang = ?", (int(js_snapshot_id), LANG_JS))

            # Heuristic JS roots by type (legacy fallback / mixed supplement).
            js_rows = conn.execute(
                """
                SELECT DISTINCT obj_addr, type_name
                FROM objects
                WHERE snapshot_id = ?
                  AND lang = ?
                  AND type_name IN ('synthetic', 'native', 'handle')
                """,
                (int(js_snapshot_id), LANG_JS),
            ).fetchall()

            # Native-style JS user roots:
            # is_user_root(node) := (not synthetic) OR (synthetic and name == '(Document DOM trees)')
            # and distance-to-runtime-anchor == 1, approximated as incoming edge from anchor 0/1.
            js_user_root_rows = conn.execute(
                """
                SELECT DISTINCT o.obj_addr, o.type_name, NULL AS name_value
                FROM edges e
                JOIN objects o
                  ON o.snapshot_id = e.snapshot_id
                 AND o.lang = ?
                 AND o.obj_addr = e.to_obj_addr
                WHERE e.snapshot_id = ?
                  AND e.from_obj_addr IN (0, 1)
                  AND o.type_name != 'synthetic'
                UNION
                SELECT DISTINCT o.obj_addr, o.type_name, hs.value AS name_value
                FROM edges e
                JOIN objects o
                  ON o.snapshot_id = e.snapshot_id
                 AND o.lang = ?
                 AND o.obj_addr = e.to_obj_addr
                JOIN heap_strings hs
                  ON hs.snapshot_id = o.snapshot_id
                 AND hs.string_index = o.name_index
                WHERE e.snapshot_id = ?
                  AND e.from_obj_addr IN (0, 1)
                  AND o.type_name = 'synthetic'
                  AND hs.value = '(Document DOM trees)'
                """,
                (LANG_JS, int(js_snapshot_id), LANG_JS, int(js_snapshot_id)),
            ).fetchall()

            rows = []
            if mode in {"heuristic", "mixed"}:
                rows.extend(
                    (
                        int(js_snapshot_id),
                        LANG_JS,
                        int(obj_addr),
                        str(type_name),
                        "heuristic_type",
                        "low",
                        None,
                    )
                    for obj_addr, type_name in js_rows
                )
            if mode in {"native", "mixed"}:
                rows.extend(
                    (
                        int(js_snapshot_id),
                        LANG_JS,
                        int(obj_addr),
                        "user_root_d1",
                        "native_v8_user_root",
                        "high",
                        json.dumps(
                            {
                                "is_user_root": True,
                                "distance_from_runtime_anchor": 1,
                                "raw_type": (str(type_name) if type_name is not None else None),
                                "name": (str(name_val) if name_val is not None else None),
                            },
                            ensure_ascii=True,
                        ),
                    )
                    for (obj_addr, type_name, name_val) in js_user_root_rows
                )
            if rows:
                db.insert_roots(conn, rows)
                inserted_js = len(rows)

        if kt_snapshot_id is not None:
            conn.execute(
                "DELETE FROM roots WHERE snapshot_id = ? AND lang = ?",
                (int(kt_snapshot_id), LANG_KOTLIN),
            )

            kt_rows = conn.execute(
                """
                SELECT DISTINCT obj_addr, type_name
                FROM objects
                WHERE snapshot_id = ?
                  AND lang = ?
                  AND type_name LIKE ?
                """,
                (int(kt_snapshot_id), LANG_KOTLIN, "%kotlin.native.internal.StableRef%"),
            ).fetchall()
            rows = [
                (
                    int(kt_snapshot_id),
                    LANG_KOTLIN,
                    int(obj_addr),
                    str(type_name),
                    ("native_hprof_root" if mode in {"native", "mixed"} else "heuristic_type"),
                    ("medium" if mode in {"native", "mixed"} else "low"),
                    None,
                )
                for obj_addr, type_name in kt_rows
            ]
            if rows:
                db.insert_roots(conn, rows)
                inserted_kt = len(rows)

        conn.commit()
        return {
            "js_snapshot_id": int(js_snapshot_id) if js_snapshot_id is not None else -1,
            "kt_snapshot_id": int(kt_snapshot_id) if kt_snapshot_id is not None else -1,
            "js_roots": inserted_js,
            "kt_roots": inserted_kt,
            "roots_mode": mode,  # type: ignore[typeddict-item]
        }
    finally:
        conn.close()
