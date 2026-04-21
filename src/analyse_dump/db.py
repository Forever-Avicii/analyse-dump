from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Iterable, Optional, Tuple

SCHEMA_PATH = Path(__file__).resolve().parent / "sql" / "schema.sql"


def connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    _migrate_legacy_roots_table(conn)
    conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))


def fetch_latest_snapshot_id(
    conn: sqlite3.Connection,
    snapshot_type: str,
    *,
    required: bool = True,
) -> Optional[int]:
    row = conn.execute(
        "SELECT id FROM snapshots WHERE type = ? ORDER BY id DESC LIMIT 1",
        (snapshot_type,),
    ).fetchone()
    if row is None:
        if required:
            raise ValueError(f"No snapshot found for type={snapshot_type}")
        return None
    return int(row[0])


def _migrate_legacy_roots_table(conn: sqlite3.Connection) -> None:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='roots'"
    ).fetchone()
    if row is None:
        return

    cols = conn.execute("PRAGMA table_info(roots)").fetchall()
    col_names = {str(c[1]) for c in cols}
    if (
        "lang" in col_names
        and "root_kind" in col_names
        and "source" in col_names
        and "confidence" in col_names
        and "meta_json" in col_names
    ):
        return

    conn.execute("ALTER TABLE roots RENAME TO roots_legacy")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS roots (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          snapshot_id INTEGER NOT NULL,
          lang INTEGER NOT NULL,
          obj_addr INTEGER NOT NULL,
          root_kind TEXT,
          source TEXT,
          confidence TEXT,
          meta_json TEXT,
          FOREIGN KEY(snapshot_id) REFERENCES snapshots(id)
        )
        """
    )
    legacy_cols = {str(c[1]) for c in conn.execute("PRAGMA table_info(roots_legacy)").fetchall()}
    has_root_type = "root_type" in legacy_cols
    has_root_kind = "root_kind" in legacy_cols
    has_source = "source" in legacy_cols
    has_confidence = "confidence" in legacy_cols
    has_meta_json = "meta_json" in legacy_cols
    root_kind_expr = "rl.root_kind" if has_root_kind else ("rl.root_type" if has_root_type else "NULL")
    source_expr = "rl.source" if has_source else "'legacy_migrated'"
    confidence_expr = "rl.confidence" if has_confidence else "'low'"
    meta_expr = "rl.meta_json" if has_meta_json else "NULL"
    conn.execute(
        f"""
        INSERT INTO roots(snapshot_id, lang, obj_addr, root_kind, source, confidence, meta_json)
        SELECT rl.snapshot_id,
               CASE
                 WHEN s.type = 'heapsnapshot' THEN 0
                 WHEN s.type = 'hprof' THEN 1
                 ELSE -1
               END AS lang,
               rl.obj_addr,
               {root_kind_expr},
               {source_expr},
               {confidence_expr},
               {meta_expr}
        FROM roots_legacy rl
        LEFT JOIN snapshots s
          ON s.id = rl.snapshot_id
        """
    )
    conn.execute("DROP TABLE roots_legacy")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_roots_snapshot_lang_addr ON roots(snapshot_id, lang, obj_addr)"
    )


def create_snapshot(
    conn: sqlite3.Connection,
    snapshot_type: str,
    source_path: str,
    captured_at: Optional[str] = None,
    meta: Optional[dict] = None,
) -> int:
    cur = conn.execute(
        """
        INSERT INTO snapshots(type, path, captured_at, meta_json)
        VALUES (?, ?, ?, ?)
        """,
        (snapshot_type, source_path, captured_at, json.dumps(meta, ensure_ascii=True) if meta else None),
    )
    return int(cur.lastrowid)


def insert_objects(
    conn: sqlite3.Connection,
    rows: Iterable[Tuple[int, int, int, Optional[str], Optional[int], Optional[int], Optional[int]]],
) -> None:
    conn.executemany(
        """
        INSERT INTO objects(
          snapshot_id, lang, obj_addr, type_name, shallow_size, heap_index, name_index
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )


def insert_edges(
    conn: sqlite3.Connection,
    rows: Iterable[Tuple[int, int, int, int, int, Optional[int], Optional[str]]],
) -> None:
    conn.executemany(
        """
        INSERT INTO edges(
          snapshot_id, from_obj_addr, to_obj_addr, edge_type, name_kind, name_num, name_text
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )


def insert_object_fields(
    conn: sqlite3.Connection,
    rows: Iterable[Tuple[int, int, int, str, Optional[int], Optional[str], Optional[str]]],
) -> None:
    conn.executemany(
        """
        INSERT INTO object_fields(
          snapshot_id, lang, obj_addr, field_name, field_value_int, field_value_text, value_type
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )


def insert_strings(
    conn: sqlite3.Connection,
    rows: Iterable[Tuple[int, int, str]],
) -> None:
    conn.executemany(
        """
        INSERT INTO heap_strings(snapshot_id, string_index, value)
        VALUES (?, ?, ?)
        """,
        rows,
    )


def insert_roots(
    conn: sqlite3.Connection,
    rows: Iterable[Tuple[int, int, int, Optional[str], Optional[str], Optional[str], Optional[str]]],
) -> None:
    conn.executemany(
        """
        INSERT INTO roots(snapshot_id, lang, obj_addr, root_kind, source, confidence, meta_json)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )


def fetch_string(conn: sqlite3.Connection, snapshot_id: int, index: int) -> Optional[str]:
    row = conn.execute(
        "SELECT value FROM heap_strings WHERE snapshot_id = ? AND string_index = ?",
        (snapshot_id, index),
    ).fetchone()
    if row is None:
        return None
    return str(row[0])
