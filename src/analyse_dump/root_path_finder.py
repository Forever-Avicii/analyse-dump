from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Set, Tuple

from analyse_dump import db
from analyse_dump.const import (
    LANG_JS,
    LANG_KOTLIN,
    NAME_KIND_ARRAY_INDEX,
    NAME_KIND_FIELD_NAME,
    NAME_KIND_STRING_INDEX,
)

EDGE_TYPE_NAME = {
    0: "unknown",
    1: "field",
    2: "array_element",
    3: "property",
    4: "element",
    5: "hidden",
    6: "shortcut",
    7: "weak",
    8: "internal",
}

LANG_BY_NAME = {
    "js": LANG_JS,
    "kotlin": LANG_KOTLIN,
}

LANG_NAME = {
    LANG_JS: "js",
    LANG_KOTLIN: "kotlin",
}

DEFAULT_JS_ROOT_TYPES = {"synthetic", "native", "handle"}
DEFAULT_KT_ROOT_TYPES = {"kotlin.native.internal.StableRef"}
DEFAULT_JS_PSEUDO_ROOT_ADDRS = {0, 1}


@dataclass(frozen=True)
class Node:
    lang: int
    addr: int


@dataclass
class RetainEdge:
    holder: Node
    held: Node
    detail: str


def _parse_addr(addr: str) -> int:
    s = addr.strip().lower()
    if s.startswith("0x"):
        return int(s, 16)
    if s.isdigit() or (s.startswith("-") and s[1:].isdigit()):
        return int(s, 10)
    return int(s, 16)


def _fetch_latest_snapshot_id(conn, snapshot_type: str) -> int:
    row = conn.execute(
        "SELECT id FROM snapshots WHERE type = ? ORDER BY id DESC LIMIT 1",
        (snapshot_type,),
    ).fetchone()
    if row is None:
        raise ValueError(f"No snapshot found for type={snapshot_type}")
    return int(row[0])


def _split_csv(values: Optional[str]) -> Optional[Set[str]]:
    if values is None:
        return None
    out = {v.strip() for v in values.split(",") if v.strip()}
    return out if out else None


def _node_type(conn, snapshot_id: int, node: Node) -> Optional[str]:
    row = conn.execute(
        """
        SELECT type_name
        FROM objects
        WHERE snapshot_id = ?
          AND lang = ?
          AND obj_addr = ?
        LIMIT 1
        """,
        (snapshot_id, node.lang, node.addr),
    ).fetchone()
    if row is None:
        return None
    if row[0] is None:
        return None
    return str(row[0])


def _node_exists(conn, snapshot_id: int, node: Node) -> bool:
    row = conn.execute(
        """
        SELECT 1
        FROM objects
        WHERE snapshot_id = ?
          AND lang = ?
          AND obj_addr = ?
        LIMIT 1
        """,
        (snapshot_id, node.lang, node.addr),
    ).fetchone()
    return row is not None


def _edge_label(
    conn,
    snapshot_id: int,
    edge_type: int,
    name_kind: int,
    name_num: Optional[int],
    name_text: Optional[str],
) -> str:
    edge_name = EDGE_TYPE_NAME.get(edge_type, f"edge_{edge_type}")
    if name_kind == NAME_KIND_FIELD_NAME:
        return f"{edge_name}:{name_text}" if name_text else edge_name
    if name_kind == NAME_KIND_ARRAY_INDEX:
        return f"{edge_name}:[{name_num}]" if name_num is not None else f"{edge_name}:[]"
    if name_kind == NAME_KIND_STRING_INDEX:
        if name_num is None:
            return edge_name
        row = conn.execute(
            "SELECT value FROM heap_strings WHERE snapshot_id = ? AND string_index = ?",
            (snapshot_id, int(name_num)),
        ).fetchone()
        if row is None:
            return f"{edge_name}:s:{name_num}"
        return f"{edge_name}:{row[0]}"
    return edge_name


def _incoming_edges(
    conn,
    snapshot_id: int,
    node: Node,
    max_fanout: int,
    include_weak: bool,
) -> List[RetainEdge]:
    if include_weak:
        rows = conn.execute(
            """
            SELECT from_obj_addr, edge_type, name_kind, name_num, name_text
            FROM edges
            WHERE snapshot_id = ?
              AND to_obj_addr = ?
            LIMIT ?
            """,
            (snapshot_id, node.addr, max_fanout),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT from_obj_addr, edge_type, name_kind, name_num, name_text
            FROM edges
            WHERE snapshot_id = ?
              AND to_obj_addr = ?
              AND edge_type != 7
            LIMIT ?
            """,
            (snapshot_id, node.addr, max_fanout),
        ).fetchall()

    out: List[RetainEdge] = []
    for holder_addr, edge_type, name_kind, name_num, name_text in rows:
        holder = Node(node.lang, int(holder_addr))
        detail = _edge_label(
            conn,
            snapshot_id,
            int(edge_type),
            int(name_kind),
            int(name_num) if name_num is not None else None,
            str(name_text) if name_text is not None else None,
        )
        out.append(RetainEdge(holder=holder, held=node, detail=detail))
    return out


def _is_root(
    conn,
    snapshot_id: int,
    node: Node,
    js_root_types: Set[str],
    kt_root_types: Set[str],
) -> bool:
    # In ArkTS snapshots, pseudo root entry nodes commonly appear as addr 0/1.
    # Treat them as root anchors to align with "distance to GC root" semantics.
    if node.lang == LANG_JS and node.addr in DEFAULT_JS_PSEUDO_ROOT_ADDRS:
        return True

    tname = _node_type(conn, snapshot_id, node)
    if tname is None:
        return False
    if node.lang == LANG_JS:
        return tname in js_root_types
    return tname in kt_root_types


def _reconstruct_path(
    end_node: Node,
    parent: Dict[Node, Tuple[Node, str]],
) -> List[Tuple[Node, Node, str]]:
    # Output path as: held <- holder (from start towards root)
    chain: List[Tuple[Node, Node, str]] = []
    cur = end_node
    while cur in parent:
        prev, detail = parent[cur]
        chain.append((cur, prev, detail))
        cur = prev
    chain.reverse()
    return chain


def find_root_path(
    db_path: Path,
    addr: str,
    lang: Optional[str] = None,
    js_snapshot_id: Optional[int] = None,
    kt_snapshot_id: Optional[int] = None,
    max_depth: int = 16,
    max_fanout: int = 512,
    include_weak: bool = False,
    js_root_types_csv: Optional[str] = None,
    kt_root_types_csv: Optional[str] = None,
) -> Dict[str, object]:
    target_addr = _parse_addr(addr)

    conn = db.connect(db_path)
    try:
        if js_snapshot_id is None:
            js_snapshot_id = _fetch_latest_snapshot_id(conn, "heapsnapshot")
        if kt_snapshot_id is None:
            kt_snapshot_id = _fetch_latest_snapshot_id(conn, "hprof")

        js_root_types = _split_csv(js_root_types_csv) or set(DEFAULT_JS_ROOT_TYPES)
        kt_root_types = _split_csv(kt_root_types_csv) or set(DEFAULT_KT_ROOT_TYPES)

        starts: List[Tuple[Node, int]] = []
        if lang is not None:
            lname = lang.strip().lower()
            if lname not in LANG_BY_NAME:
                raise ValueError("--lang must be js or kotlin")
            lcode = LANG_BY_NAME[lname]
            snap_id = int(js_snapshot_id) if lcode == LANG_JS else int(kt_snapshot_id)
            node = Node(lcode, target_addr)
            if not _node_exists(conn, snap_id, node):
                return {
                    "found": False,
                    "reason": "address_not_found_for_lang",
                    "addr": target_addr,
                    "lang": lname,
                    "snapshot_id": snap_id,
                }
            starts.append((node, snap_id))
        else:
            js_node = Node(LANG_JS, target_addr)
            if _node_exists(conn, int(js_snapshot_id), js_node):
                starts.append((js_node, int(js_snapshot_id)))
            kt_node = Node(LANG_KOTLIN, target_addr)
            if _node_exists(conn, int(kt_snapshot_id), kt_node):
                starts.append((kt_node, int(kt_snapshot_id)))

        if not starts:
            return {
                "found": False,
                "reason": "address_not_found_in_latest_snapshots",
                "addr": target_addr,
                "js_snapshot_id": int(js_snapshot_id),
                "kt_snapshot_id": int(kt_snapshot_id),
            }

        for start, snapshot_id in starts:
            if _is_root(conn, snapshot_id, start, js_root_types, kt_root_types):
                return {
                    "found": True,
                    "addr": target_addr,
                    "lang": LANG_NAME[start.lang],
                    "snapshot_id": snapshot_id,
                    "root": start,
                    "path": [],
                }

            q = deque()
            q.append((start, 0))
            visited: Set[Node] = {start}
            parent: Dict[Node, Tuple[Node, str]] = {}
            found_root: Optional[Node] = None

            while q:
                cur, depth = q.popleft()
                if depth >= max_depth:
                    continue

                for redge in _incoming_edges(
                    conn,
                    snapshot_id,
                    cur,
                    max_fanout=max_fanout,
                    include_weak=include_weak,
                ):
                    holder = redge.holder
                    if holder in visited:
                        continue
                    visited.add(holder)
                    parent[holder] = (cur, redge.detail)

                    if _is_root(conn, snapshot_id, holder, js_root_types, kt_root_types):
                        found_root = holder
                        q.clear()
                        break

                    q.append((holder, depth + 1))

            if found_root is not None:
                chain = _reconstruct_path(found_root, parent)
                return {
                    "found": True,
                    "addr": target_addr,
                    "lang": LANG_NAME[start.lang],
                    "snapshot_id": snapshot_id,
                    "root": found_root,
                    "path": chain,
                }

        return {
            "found": False,
            "reason": "root_path_not_found_within_limits",
            "addr": target_addr,
            "js_snapshot_id": int(js_snapshot_id),
            "kt_snapshot_id": int(kt_snapshot_id),
            "max_depth": max_depth,
            "max_fanout": max_fanout,
        }
    finally:
        conn.close()
