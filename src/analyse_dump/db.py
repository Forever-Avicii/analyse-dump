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
    conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))


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


def fetch_string(conn: sqlite3.Connection, snapshot_id: int, index: int) -> Optional[str]:
    row = conn.execute(
        "SELECT value FROM heap_strings WHERE snapshot_id = ? AND string_index = ?",
        (snapshot_id, index),
    ).fetchone()
    if row is None:
        return None
    return str(row[0])
