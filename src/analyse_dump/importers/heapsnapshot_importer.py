from __future__ import annotations

from array import array
from pathlib import Path
from typing import List, Optional, Tuple

import ijson

from analyse_dump import db
from analyse_dump.const import (
    EDGE_ARRAY_ELEMENT,
    EDGE_ELEMENT,
    EDGE_HIDDEN,
    EDGE_PROPERTY,
    EDGE_SHORTCUT,
    EDGE_TYPE_BY_NAME,
    EDGE_UNKNOWN,
    EDGE_WEAK,
    LANG_JS,
    NAME_KIND_ARRAY_INDEX,
    NAME_KIND_STRING_INDEX,
)


def _read_snapshot_header(snapshot_path: Path) -> dict:
    with snapshot_path.open("rb") as f:
        for item in ijson.items(f, "snapshot"):
            return item
    raise ValueError(f"Invalid heapsnapshot file: missing 'snapshot' object in {snapshot_path}")


def _edge_type_code(name: str) -> int:
    return EDGE_TYPE_BY_NAME.get(name, EDGE_UNKNOWN)


def import_heapsnapshot(
    db_path: Path,
    snapshot_path: Path,
    batch_size: int = 10000,
    store_strings: bool = True,
) -> int:
    conn = db.connect(db_path)
    db.init_schema(conn)

    snapshot = _read_snapshot_header(snapshot_path)
    meta = snapshot.get("meta", {})
    node_fields: List[str] = list(meta.get("node_fields", []))
    edge_fields: List[str] = list(meta.get("edge_fields", []))
    node_types: List[list] = list(meta.get("node_types", []))
    edge_types: List[list] = list(meta.get("edge_types", []))

    if not node_fields or not edge_fields:
        raise ValueError("Invalid heapsnapshot metadata: node_fields / edge_fields are required")

    n_stride = len(node_fields)
    e_stride = len(edge_fields)

    node_type_names = node_types[0] if node_types and isinstance(node_types[0], list) else []
    edge_type_names = edge_types[0] if edge_types and isinstance(edge_types[0], list) else []

    field_ix = {name: idx for idx, name in enumerate(node_fields)}
    edge_ix = {name: idx for idx, name in enumerate(edge_fields)}

    node_type_i = field_ix.get("type", 0)
    node_name_i = field_ix.get("name", 1)
    node_id_i = field_ix.get("id", 2)
    node_self_size_i = field_ix.get("self_size", 3)
    node_edge_count_i = field_ix.get("edge_count", 4)

    edge_type_i = edge_ix.get("type", 0)
    edge_name_i = edge_ix.get("name_or_index", 1)
    edge_to_node_i = edge_ix.get("to_node", 2)

    snapshot_id = db.create_snapshot(
        conn,
        snapshot_type="heapsnapshot",
        source_path=str(snapshot_path),
        meta={
            "parser": "ijson",
            "node_stride": n_stride,
            "edge_stride": e_stride,
            "node_count": snapshot.get("node_count"),
            "edge_count": snapshot.get("edge_count"),
        },
    )

    if store_strings:
        batch: List[Tuple[int, int, str]] = []
        with snapshot_path.open("rb") as f:
            for idx, value in enumerate(ijson.items(f, "strings.item")):
                batch.append((snapshot_id, idx, str(value)))
                if len(batch) >= batch_size:
                    db.insert_strings(conn, batch)
                    batch.clear()
        if batch:
            db.insert_strings(conn, batch)
        conn.commit()

    node_ids = array("Q")
    node_edge_counts = array("I")

    objects_batch: List[Tuple[int, int, int, Optional[str], Optional[int], Optional[int], Optional[int]]] = []
    chunk: List[int] = []
    with snapshot_path.open("rb") as f:
        for num in ijson.items(f, "nodes.item"):
            chunk.append(int(num))
            if len(chunk) < n_stride:
                continue

            node_type_num = chunk[node_type_i]
            node_type = (
                node_type_names[node_type_num]
                if 0 <= node_type_num < len(node_type_names)
                else f"type_{node_type_num}"
            )
            node_name_index = chunk[node_name_i]
            node_id = chunk[node_id_i]
            self_size = chunk[node_self_size_i] if node_self_size_i < len(chunk) else None
            edge_count = chunk[node_edge_count_i] if node_edge_count_i < len(chunk) else 0

            objects_batch.append((snapshot_id, LANG_JS, int(node_id), node_type, self_size, None, int(node_name_index)))

            node_ids.append(int(node_id))
            node_edge_counts.append(int(edge_count))

            if len(objects_batch) >= batch_size:
                db.insert_objects(conn, objects_batch)
                objects_batch.clear()

            chunk.clear()

    if chunk:
        raise ValueError("Malformed heapsnapshot: trailing node payload")

    if objects_batch:
        db.insert_objects(conn, objects_batch)

    edge_batch: List[Tuple[int, int, int, int, int, Optional[int], Optional[str]]] = []
    chunk = []
    from_node_index = 0
    remaining_from_edges = int(node_edge_counts[0]) if len(node_edge_counts) > 0 else 0

    with snapshot_path.open("rb") as f:
        for num in ijson.items(f, "edges.item"):
            chunk.append(int(num))
            if len(chunk) < e_stride:
                continue

            while from_node_index < len(node_edge_counts) and remaining_from_edges == 0:
                from_node_index += 1
                if from_node_index < len(node_edge_counts):
                    remaining_from_edges = int(node_edge_counts[from_node_index])

            if from_node_index >= len(node_ids):
                break

            edge_type_num = chunk[edge_type_i]
            edge_type_name = (
                edge_type_names[edge_type_num]
                if 0 <= edge_type_num < len(edge_type_names)
                else f"type_{edge_type_num}"
            )
            edge_type_code = _edge_type_code(edge_type_name)
            edge_name_or_index = int(chunk[edge_name_i])
            to_node_offset = chunk[edge_to_node_i]

            from_addr = int(node_ids[from_node_index])
            to_node_index = to_node_offset // n_stride
            if 0 <= to_node_index < len(node_ids):
                to_addr = int(node_ids[to_node_index])
            else:
                to_addr = 0

            if edge_type_code in (EDGE_ELEMENT, EDGE_HIDDEN):
                name_kind = NAME_KIND_ARRAY_INDEX
            else:
                name_kind = NAME_KIND_STRING_INDEX

            edge_batch.append((snapshot_id, from_addr, to_addr, edge_type_code, name_kind, edge_name_or_index, None))

            remaining_from_edges = max(remaining_from_edges - 1, 0)

            if len(edge_batch) >= batch_size:
                db.insert_edges(conn, edge_batch)
                edge_batch.clear()

            chunk.clear()

    if chunk:
        raise ValueError("Malformed heapsnapshot: trailing edge payload")

    if edge_batch:
        db.insert_edges(conn, edge_batch)

    # Persist root seeds extracted from snapshot-level semantics.
    root_rows: List[Tuple[int, int, int, Optional[str], Optional[str]]] = []
    js_root_types = ("synthetic", "native", "handle")
    type_rows = conn.execute(
        """
        SELECT DISTINCT obj_addr, type_name
        FROM objects
        WHERE snapshot_id = ?
          AND lang = ?
          AND type_name IN (?, ?, ?)
        """,
        (snapshot_id, LANG_JS, js_root_types[0], js_root_types[1], js_root_types[2]),
    ).fetchall()
    for obj_addr, type_name in type_rows:
        root_rows.append((snapshot_id, LANG_JS, int(obj_addr), str(type_name), "heapsnapshot_type"))

    pseudo_rows = conn.execute(
        """
        SELECT DISTINCT obj_addr
        FROM objects
        WHERE snapshot_id = ?
          AND lang = ?
          AND obj_addr IN (0, 1)
        """,
        (snapshot_id, LANG_JS),
    ).fetchall()
    for (obj_addr,) in pseudo_rows:
        root_rows.append((snapshot_id, LANG_JS, int(obj_addr), "pseudo_root", "heapsnapshot_pseudo"))

    if root_rows:
        db.insert_roots(conn, root_rows)

    conn.commit()
    conn.close()
    return snapshot_id
