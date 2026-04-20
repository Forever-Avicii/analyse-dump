from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Set

from analyse_dump import db
from analyse_dump.const import (
    EDGE_ARRAY_ELEMENT,
    EDGE_ELEMENT,
    EDGE_HIDDEN,
    EDGE_PROPERTY,
    EDGE_WEAK,
    LANG_JS,
    LANG_KOTLIN,
    NAME_KIND_STRING_INDEX,
    REF_KIND_STABLE_REF,
)
from analyse_dump.root_distance import make_cache_profile
from analyse_dump.root_path_finder import find_root_path

LANG_BY_NAME = {
    "js": LANG_JS,
    "kotlin": LANG_KOTLIN,
}

LANG_NAME = {
    LANG_JS: "js",
    LANG_KOTLIN: "kotlin",
}

REF_KIND_NAME = {
    REF_KIND_STABLE_REF: "stable_ref",
}


@dataclass(frozen=True)
class SimpleNode:
    lang: int
    addr: int


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


def _segment_anchor(result: Dict[str, object]) -> Optional[SimpleNode]:
    # We use "node before root" as anchor so bridge checks happen near GC-root reach.
    path = result.get("path", [])
    if isinstance(path, list) and path:
        held_before_root = path[-1][1]
        return SimpleNode(int(held_before_root.lang), int(held_before_root.addr))  # type: ignore[attr-defined]
    root = result.get("root")
    if root is None:
        return None
    return SimpleNode(int(root.lang), int(root.addr))  # type: ignore[attr-defined]


def _segment_nodes(result: Dict[str, object]) -> List[SimpleNode]:
    path = result.get("path", [])
    if not isinstance(path, list) or not path:
        root = result.get("root")
        if root is None:
            return []
        return [SimpleNode(int(root.lang), int(root.addr))]  # type: ignore[attr-defined]

    out: List[SimpleNode] = []
    first_held = path[0][1]
    out.append(SimpleNode(int(first_held.lang), int(first_held.addr)))  # type: ignore[attr-defined]
    for holder, _held, _detail in path:
        out.append(SimpleNode(int(holder.lang), int(holder.addr)))  # type: ignore[attr-defined]

    deduped: List[SimpleNode] = []
    seen: Set[SimpleNode] = set()
    for n in out:
        if n in seen:
            continue
        seen.add(n)
        deduped.append(n)
    return deduped


def _parse_num(s: str) -> Optional[int]:
    text = s.strip().lower()
    if not text:
        return None
    try:
        if text.startswith("0x"):
            return int(text, 16)
        if text.isdigit() or (text.startswith("-") and text[1:].isdigit()):
            return int(text, 10)
        return int(text, 16)
    except Exception:
        return None


def _js_array_ref_values(
    conn,
    js_snapshot_id: int,
    container_addr: int,
    max_elems: int = 256,
) -> List[int]:
    out: List[int] = []
    seen: Set[int] = set()

    def _scan_array_node(array_addr: int) -> None:
        rows = conn.execute(
            """
            SELECT e.to_obj_addr, o.name_index, hs.value
            FROM edges e
            LEFT JOIN objects o
              ON o.snapshot_id = e.snapshot_id
             AND o.lang = ?
             AND o.obj_addr = e.to_obj_addr
            LEFT JOIN heap_strings hs
              ON hs.snapshot_id = o.snapshot_id
             AND hs.string_index = o.name_index
            WHERE e.snapshot_id = ?
              AND e.from_obj_addr = ?
              AND e.edge_type IN (?, ?, ?)
            LIMIT ?
            """,
            (LANG_JS, js_snapshot_id, array_addr, EDGE_ELEMENT, EDGE_HIDDEN, EDGE_ARRAY_ELEMENT, max_elems),
        ).fetchall()
        for elem_addr, _name_index, name_value in rows:
            a = int(elem_addr)
            if a in seen:
                continue
            seen.add(a)
            if name_value is None:
                continue
            n = _parse_num(str(name_value))
            if n is not None:
                out.append(n)

    _scan_array_node(container_addr)
    # Some runtimes store JSArray elements in backing "(object elements)".
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
        (js_snapshot_id, container_addr, EDGE_PROPERTY, NAME_KIND_STRING_INDEX),
    ).fetchone()
    if row is not None:
        _scan_array_node(int(row[0]))
    return out


def _js_node_meta(
    conn,
    js_snapshot_id: int,
    addr: int,
) -> Dict[str, Optional[str]]:
    row = conn.execute(
        """
        SELECT o.type_name, hs.value
        FROM objects o
        LEFT JOIN heap_strings hs
          ON hs.snapshot_id = o.snapshot_id
         AND hs.string_index = o.name_index
        WHERE o.snapshot_id = ?
          AND o.lang = ?
          AND o.obj_addr = ?
        LIMIT 1
        """,
        (js_snapshot_id, LANG_JS, addr),
    ).fetchone()
    if row is None:
        return {"type_name": None, "name": None}
    return {
        "type_name": (str(row[0]) if row[0] is not None else None),
        "name": (str(row[1]) if row[1] is not None else None),
    }


def _is_deprioritized_js_node(
    conn,
    js_snapshot_id: int,
    node: SimpleNode,
    deprioritize_keywords: Sequence[str],
) -> bool:
    meta = _js_node_meta(conn, js_snapshot_id, node.addr)
    tname = (meta["type_name"] or "").lower()
    oname = (meta["name"] or "").lower()
    for kw in deprioritize_keywords:
        k = kw.strip().lower()
        if not k:
            continue
        if k in tname or k in oname:
            return True
    return False


def _bridge_from_js_anchor(
    conn,
    js_snapshot_id: int,
    kt_snapshot_id: int,
    anchor: SimpleNode,
    visited: Set[SimpleNode],
    js_napi_prop: str = "knapi_refs_test",
    kt_napi_field: str = "ref",
) -> Optional[Dict[str, object]]:
    prop_rows = conn.execute(
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
          AND hs.value = ?
        LIMIT 32
        """,
        (js_snapshot_id, anchor.addr, EDGE_PROPERTY, NAME_KIND_STRING_INDEX, js_napi_prop),
    ).fetchall()
    if not prop_rows:
        return None

    ref_values: List[int] = []
    for (container_addr,) in prop_rows:
        ref_values.extend(_js_array_ref_values(conn, js_snapshot_id, int(container_addr)))
    ref_values = sorted(set(ref_values))
    if not ref_values:
        return None

    for ref_value in ref_values:
        holder_rows = conn.execute(
            """
            SELECT DISTINCT f.obj_addr
            FROM object_fields f
            WHERE f.snapshot_id = ?
              AND f.lang = ?
              AND f.field_name = ?
              AND f.field_value_int = ?
            ORDER BY f.obj_addr
            LIMIT 256
            """,
            (kt_snapshot_id, LANG_KOTLIN, kt_napi_field, ref_value),
        ).fetchall()
        if not holder_rows:
            continue
        for (kt_addr,) in holder_rows:
            target = SimpleNode(LANG_KOTLIN, int(kt_addr))
            kind = "loop" if target in visited else "jump"
            return {
                "kind": kind,
                "from": anchor,
                "to": target,
                "ref_kind": "napi_ref",
                "ref_addr": ref_value,
                "evidence": f"js_prop={js_napi_prop}",
            }
    return None


def _bridge_from_kt_root(
    conn,
    js_snapshot_id: int,
    kt_snapshot_id: int,
    root: SimpleNode,
    visited: Set[SimpleNode],
) -> Optional[Dict[str, object]]:
    ref_rows = conn.execute(
        """
        SELECT DISTINCT ref_addr
        FROM xrefs
        WHERE snapshot_id = ?
          AND lang = ?
          AND owner_obj_addr = ?
          AND ref_kind = ?
        ORDER BY ref_addr
        LIMIT 256
        """,
        (kt_snapshot_id, LANG_KOTLIN, root.addr, REF_KIND_STABLE_REF),
    ).fetchall()
    if not ref_rows:
        return None

    for (ref_addr_raw,) in ref_rows:
        ref_addr = int(ref_addr_raw)
        js_rows = conn.execute(
            """
            SELECT owner_obj_addr
            FROM xrefs
            WHERE snapshot_id = ?
              AND lang = ?
              AND ref_addr = ?
              AND ref_kind = ?
            ORDER BY owner_obj_addr
            LIMIT 256
            """,
            (js_snapshot_id, LANG_JS, ref_addr, REF_KIND_STABLE_REF),
        ).fetchall()
        for (js_addr_raw,) in js_rows:
            target = SimpleNode(LANG_JS, int(js_addr_raw))
            kind = "loop" if target in visited else "jump"
            return {
                "kind": kind,
                "from": root,
                "to": target,
                "ref_kind": "stable_ref",
                "ref_addr": ref_addr,
                "evidence": "kt_root_stable_ref",
            }
    return None


def analyze_chain(
    db_path: Path,
    addr: str,
    lang: str,
    js_snapshot_id: Optional[int] = None,
    kt_snapshot_id: Optional[int] = None,
    max_steps: int = 8,
    max_depth: int = 16,
    max_fanout: int = 512,
    include_weak: bool = False,
    js_root_types_csv: Optional[str] = None,
    kt_root_types_csv: Optional[str] = None,
    js_napi_prop: str = "knapi_refs_test",
    kt_napi_field: str = "ref",
    js_deprioritize_keywords_csv: str = "global,synthetic,handle,native",
    js_cache_profile: Optional[str] = None,
    kt_cache_profile: Optional[str] = None,
) -> Dict[str, object]:
    lang_norm = lang.strip().lower()
    if lang_norm not in LANG_BY_NAME:
        raise ValueError("--lang must be js or kotlin")

    current = SimpleNode(LANG_BY_NAME[lang_norm], _parse_addr(addr))

    conn = db.connect(db_path)
    try:
        if js_snapshot_id is None:
            js_snapshot_id = _fetch_latest_snapshot_id(conn, "heapsnapshot")
        if kt_snapshot_id is None:
            kt_snapshot_id = _fetch_latest_snapshot_id(conn, "hprof")

        visited: Set[SimpleNode] = {current}
        segments: List[Dict[str, object]] = []
        bridges: List[Dict[str, object]] = []
        for step in range(1, max_steps + 1):
            # By default, JS root detection is too heuristic. For chain orchestration,
            # prefer walking to pseudo roots (0/1) unless caller overrides root types.
            js_root_types_for_step = js_root_types_csv
            if current.lang == LANG_JS and js_root_types_for_step is None:
                js_root_types_for_step = "__never_match__"
            if current.lang == LANG_JS:
                cache_profile = make_cache_profile(
                    lang="js",
                    include_weak=include_weak,
                    root_types_csv=js_root_types_for_step,
                    profile_override=js_cache_profile,
                )
            else:
                cache_profile = make_cache_profile(
                    lang="kotlin",
                    include_weak=include_weak,
                    root_types_csv=kt_root_types_csv,
                    profile_override=kt_cache_profile,
                )

            seg = find_root_path(
                db_path=db_path,
                addr=f"0x{current.addr:x}",
                lang=LANG_NAME[current.lang],
                js_snapshot_id=int(js_snapshot_id),
                kt_snapshot_id=int(kt_snapshot_id),
                max_depth=max_depth,
                max_fanout=max_fanout,
                include_weak=include_weak,
                js_root_types_csv=js_root_types_for_step,
                kt_root_types_csv=kt_root_types_csv,
                use_cache=True,
                cache_profile=cache_profile,
            )
            segments.append(
                {
                    "step": step,
                    "start_lang": LANG_NAME[current.lang],
                    "start_addr": current.addr,
                    "root_result": seg,
                }
            )

            if not seg.get("found"):
                return {
                    "verdict": "inconclusive",
                    "reason": str(seg.get("reason", "root_path_not_found")),
                    "segments": segments,
                    "bridges": bridges,
                }

            root = seg.get("root")
            if root is None:
                return {
                    "verdict": "inconclusive",
                    "reason": "segment_missing_root",
                    "segments": segments,
                    "bridges": bridges,
                }
            root_node = SimpleNode(int(root.lang), int(root.addr))  # type: ignore[attr-defined]
            anchor_node = _segment_anchor(seg)
            if anchor_node is None:
                return {
                    "verdict": "inconclusive",
                    "reason": "segment_missing_anchor",
                    "segments": segments,
                    "bridges": bridges,
                }

            if current.lang == LANG_JS:
                bridge = _bridge_from_js_anchor(
                    conn,
                    js_snapshot_id=int(js_snapshot_id),
                    kt_snapshot_id=int(kt_snapshot_id),
                    anchor=anchor_node,
                    visited=visited,
                    js_napi_prop=js_napi_prop,
                    kt_napi_field=kt_napi_field,
                )
            else:
                bridge = _bridge_from_kt_root(
                    conn,
                    js_snapshot_id=int(js_snapshot_id),
                    kt_snapshot_id=int(kt_snapshot_id),
                    root=root_node,
                    visited=visited,
                )
            if bridge is None:
                return {
                    "verdict": "reached_terminal_root",
                    "segments": segments,
                    "bridges": bridges,
                }

            from_node = bridge["from"]
            to_node = bridge["to"]
            ref_addr = int(bridge["ref_addr"])
            bridges.append(
                {
                    "step": step,
                    "kind": bridge["kind"],
                    "from_lang": LANG_NAME[from_node.lang],
                    "from_addr": from_node.addr,
                    "to_lang": LANG_NAME[to_node.lang],
                    "to_addr": to_node.addr,
                    "ref_kind": str(bridge["ref_kind"]),
                    "ref_addr": ref_addr,
                    "evidence": str(bridge.get("evidence", "")),
                    "anchor_lang": LANG_NAME[anchor_node.lang],
                    "anchor_addr": anchor_node.addr,
                }
            )

            if bridge["kind"] == "loop":
                return {
                    "verdict": "loop_detected",
                    "segments": segments,
                    "bridges": bridges,
                }

            current = to_node
            visited.add(current)

        return {
            "verdict": "inconclusive",
            "reason": "max_steps_reached",
            "segments": segments,
            "bridges": bridges,
        }
    finally:
        conn.close()
