from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional

from analyse_dump import db
from analyse_dump.const import (
    EDGE_ARRAY_ELEMENT,
    EDGE_ELEMENT,
    EDGE_HIDDEN,
    EDGE_PROPERTY,
    LANG_JS,
    LANG_KOTLIN,
    NAME_KIND_STRING_INDEX,
)


def _parse_num(value: str) -> int:
    s = value.strip().lower()
    if s.startswith("0x"):
        return int(s, 16)
    if s.isdigit() or (s.startswith("-") and s[1:].isdigit()):
        return int(s, 10)
    return int(s, 16)


def _get_string(conn, snapshot_id: int, idx: Optional[int]) -> Optional[str]:
    if idx is None:
        return None
    row = conn.execute(
        "SELECT value FROM heap_strings WHERE snapshot_id = ? AND string_index = ?",
        (snapshot_id, int(idx)),
    ).fetchone()
    return str(row[0]) if row is not None else None


def _get_object_meta(conn, snapshot_id: int, addr: int) -> Dict[str, Optional[object]]:
    row = conn.execute(
        """
        SELECT type_name, name_index
        FROM objects
        WHERE snapshot_id = ?
          AND lang = ?
          AND obj_addr = ?
        LIMIT 1
        """,
        (snapshot_id, LANG_JS, addr),
    ).fetchone()
    if row is None:
        return {"type_name": None, "name_index": None, "name_value": None}
    type_name = str(row[0]) if row[0] is not None else None
    name_index = int(row[1]) if row[1] is not None else None
    name_value = _get_string(conn, snapshot_id, name_index)
    return {"type_name": type_name, "name_index": name_index, "name_value": name_value}


def inspect_js_props(
    db_path: Path,
    addr: str,
    js_snapshot_id: Optional[int] = None,
    max_props: int = 256,
    max_array_elems: int = 128,
) -> Dict[str, object]:
    target_addr = _parse_num(addr)
    conn = db.connect(db_path)
    try:
        if js_snapshot_id is None:
            js_snapshot_id = db.fetch_latest_snapshot_id(conn, "heapsnapshot")
        sid = int(js_snapshot_id)

        obj = _get_object_meta(conn, sid, target_addr)
        if obj["type_name"] is None:
            return {
                "found": False,
                "snapshot_id": sid,
                "addr": target_addr,
                "reason": "js_object_not_found",
            }

        prop_rows = conn.execute(
            """
            SELECT name_num, to_obj_addr
            FROM edges
            WHERE snapshot_id = ?
              AND from_obj_addr = ?
              AND edge_type = ?
              AND name_kind = ?
            LIMIT ?
            """,
            (sid, target_addr, EDGE_PROPERTY, NAME_KIND_STRING_INDEX, max_props),
        ).fetchall()

        props: List[Dict[str, object]] = []

        def _collect_array_values(
            container_addr: int,
            container_type: Optional[str],
            container_name: Optional[str],
        ) -> List[Dict[str, object]]:
            array_addrs: List[int] = []
            if container_type == "array":
                array_addrs.append(container_addr)
            elif container_type == "object" and isinstance(container_name, str) and "JSArray" in container_name:
                # Many JSArray objects expose elements directly via element edges.
                array_addrs.append(container_addr)
                # Some runtimes place elements in backing "(object elements)" array.
                row = conn.execute(
                    """
                    SELECT e.to_obj_addr
                    FROM edges e
                    JOIN heap_strings hs
                      ON hs.snapshot_id = e.snapshot_id
                     AND hs.string_index = e.name_num
                    WHERE e.snapshot_id = ?
                      AND e.from_obj_addr = ?
                      AND e.edge_type = ?
                      AND e.name_kind = ?
                      AND hs.value = '(object elements)'
                    LIMIT 1
                    """,
                    (sid, container_addr, EDGE_PROPERTY, NAME_KIND_STRING_INDEX),
                ).fetchone()
                if row is not None:
                    array_addrs.append(int(row[0]))
            else:
                return []

            arr_values: List[Dict[str, object]] = []
            seen_elem: set[int] = set()
            for array_addr in array_addrs:
                elem_rows = conn.execute(
                    """
                    SELECT to_obj_addr
                    FROM edges
                    WHERE snapshot_id = ?
                      AND from_obj_addr = ?
                      AND edge_type IN (?, ?, ?)
                    LIMIT ?
                    """,
                    (sid, array_addr, EDGE_ELEMENT, EDGE_HIDDEN, EDGE_ARRAY_ELEMENT, max_array_elems),
                ).fetchall()
                for (elem_addr_raw,) in elem_rows:
                    elem_addr = int(elem_addr_raw)
                    if elem_addr in seen_elem:
                        continue
                    seen_elem.add(elem_addr)
                    elem_meta = _get_object_meta(conn, sid, elem_addr)
                    name_text = elem_meta["name_value"]
                    parsed_value = None
                    if isinstance(name_text, str):
                        try:
                            parsed_value = _parse_num(name_text)
                        except Exception:
                            parsed_value = None
                    arr_values.append(
                        {
                            "elem_addr": elem_addr,
                            "elem_type": elem_meta["type_name"],
                            "elem_name": name_text,
                            "parsed_int": parsed_value,
                            "parsed_hex": (f"0x{parsed_value:x}" if isinstance(parsed_value, int) else None),
                        }
                    )
            return arr_values

        for name_num, to_obj_addr in prop_rows:
            name_idx = int(name_num) if name_num is not None else None
            prop_name = _get_string(conn, sid, name_idx) or f"s:{name_idx}"
            child_addr = int(to_obj_addr)
            child_meta = _get_object_meta(conn, sid, child_addr)

            item: Dict[str, object] = {
                "property": prop_name,
                "target_addr": child_addr,
                "target_type": child_meta["type_name"],
                "target_name": child_meta["name_value"],
                "array_values": [],
            }

            item["array_values"] = _collect_array_values(
                child_addr,
                child_meta["type_name"] if child_meta["type_name"] is not None else None,
                child_meta["name_value"] if child_meta["name_value"] is not None else None,
            )

            props.append(item)

        return {
            "found": True,
            "snapshot_id": sid,
            "addr": target_addr,
            "type_name": obj["type_name"],
            "name": obj["name_value"],
            "properties": props,
        }
    finally:
        conn.close()


def search_kt_by_value(
    db_path: Path,
    value: str,
    kt_snapshot_id: Optional[int] = None,
    limit: int = 500,
) -> Dict[str, object]:
    target_int = _parse_num(value)
    target_text_dec = str(target_int)
    target_text_hex = f"0x{target_int:x}"

    conn = db.connect(db_path)
    try:
        if kt_snapshot_id is None:
            kt_snapshot_id = db.fetch_latest_snapshot_id(conn, "hprof")
        sid = int(kt_snapshot_id)

        rows = conn.execute(
            """
            SELECT f.obj_addr, o.type_name, f.field_name, f.field_value_int, f.field_value_text, f.value_type
            FROM object_fields f
            JOIN objects o
              ON o.snapshot_id = f.snapshot_id
             AND o.lang = f.lang
             AND o.obj_addr = f.obj_addr
            WHERE f.snapshot_id = ?
              AND f.lang = ?
              AND (
                f.field_value_int = ?
                OR f.field_value_text = ?
                OR lower(f.field_value_text) = lower(?)
              )
            LIMIT ?
            """,
            (sid, LANG_KOTLIN, target_int, target_text_dec, target_text_hex, limit),
        ).fetchall()

        out = []
        for obj_addr, type_name, field_name, field_value_int, field_value_text, value_type in rows:
            out.append(
                {
                    "obj_addr": int(obj_addr),
                    "obj_addr_hex": f"0x{int(obj_addr):x}",
                    "type_name": str(type_name) if type_name is not None else None,
                    "field_name": str(field_name),
                    "field_value_int": int(field_value_int) if field_value_int is not None else None,
                    "field_value_text": str(field_value_text) if field_value_text is not None else None,
                    "value_type": str(value_type) if value_type is not None else None,
                }
            )

        return {
            "snapshot_id": sid,
            "query_value_int": target_int,
            "query_value_hex": f"0x{target_int:x}",
            "matches": out,
        }
    finally:
        conn.close()
