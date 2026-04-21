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
            pseudo_rows = conn.execute(
                """
                SELECT DISTINCT obj_addr
                FROM objects
                WHERE snapshot_id = ?
                  AND lang = ?
                  AND obj_addr IN (0, 1)
                """,
                (int(js_snapshot_id), LANG_JS),
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
                        "pseudo_root",
                        "native_v8_anchor",
                        "medium",
                        json.dumps({"anchor_addr": int(obj_addr)}, ensure_ascii=True),
                    )
                    for (obj_addr,) in pseudo_rows
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
