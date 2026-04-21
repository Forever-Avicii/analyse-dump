from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Set

from analyse_dump import db
from analyse_dump.const import (
    EDGE_ARRAY_ELEMENT,
    EDGE_ELEMENT,
    EDGE_HIDDEN,
    EDGE_PROPERTY,
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


@dataclass(frozen=True)
class SimpleNode:
    lang: int
    addr: int


@dataclass
class _SearchState:
    current: SimpleNode
    visited: Set[SimpleNode]
    segments: List[Dict[str, object]]
    bridges: List[Dict[str, object]]


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
            SELECT e.to_obj_addr, hs.value
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
        for elem_addr, name_value in rows:
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


def _bridge_candidates_from_js_anchor(
    conn,
    js_snapshot_id: int,
    kt_snapshot_id: int,
    anchor: SimpleNode,
    visited: Set[SimpleNode],
    js_napi_prop: str,
    kt_napi_field: str,
    max_candidates: int,
) -> List[Dict[str, object]]:
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
        return []

    ref_values: List[int] = []
    for (container_addr,) in prop_rows:
        ref_values.extend(_js_array_ref_values(conn, js_snapshot_id, int(container_addr)))
    ref_values = sorted(set(ref_values))
    if not ref_values:
        return []

    out: List[Dict[str, object]] = []
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
        for (kt_addr,) in holder_rows:
            target = SimpleNode(LANG_KOTLIN, int(kt_addr))
            kind = "loop" if target in visited else "jump"
            out.append(
                {
                    "kind": kind,
                    "from": anchor,
                    "to": target,
                    "ref_kind": "napi_ref",
                    "ref_addr": int(ref_value),
                    "evidence": f"js_prop={js_napi_prop}",
                }
            )
            if len(out) >= max_candidates:
                return out
    return out


def _bridge_candidates_from_kt_root(
    conn,
    js_snapshot_id: int,
    kt_snapshot_id: int,
    root: SimpleNode,
    visited: Set[SimpleNode],
    max_candidates: int,
) -> List[Dict[str, object]]:
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
        return []

    out: List[Dict[str, object]] = []
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
            out.append(
                {
                    "kind": kind,
                    "from": root,
                    "to": target,
                    "ref_kind": "stable_ref",
                    "ref_addr": ref_addr,
                    "evidence": "kt_root_stable_ref",
                }
            )
            if len(out) >= max_candidates:
                return out
    return out


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
    include_shortcut: bool = False,
    roots_mode: str = "native",
    js_root_types_csv: Optional[str] = None,
    kt_root_types_csv: Optional[str] = None,
    js_napi_prop: str = "knapi_refs_test",
    kt_napi_field: str = "ref",
    js_deprioritize_keywords_csv: str = "global,synthetic,handle,native",
    js_cache_profile: Optional[str] = None,
    kt_cache_profile: Optional[str] = None,
    max_branch_candidates: int = 4,
) -> Dict[str, object]:
    del js_deprioritize_keywords_csv  # reserved for future ranking policies

    lang_norm = lang.strip().lower()
    if lang_norm not in LANG_BY_NAME:
        raise ValueError("--lang must be js or kotlin")

    start = SimpleNode(LANG_BY_NAME[lang_norm], _parse_addr(addr))

    conn = db.connect(db_path)
    try:
        if js_snapshot_id is None:
            js_snapshot_id = _fetch_latest_snapshot_id(conn, "heapsnapshot")
        if kt_snapshot_id is None:
            kt_snapshot_id = _fetch_latest_snapshot_id(conn, "hprof")

        frontier: List[_SearchState] = [
            _SearchState(current=start, visited={start}, segments=[], bridges=[])
        ]
        terminals: List[Dict[str, object]] = []

        for step in range(1, max_steps + 1):
            next_frontier: List[_SearchState] = []

            for state in frontier:
                current = state.current
                visited = set(state.visited)
                segments = list(state.segments)
                bridges = list(state.bridges)

                js_root_types_for_step = js_root_types_csv
                if current.lang == LANG_JS and js_root_types_for_step is None:
                    js_root_types_for_step = "__never_match__"

                if current.lang == LANG_JS:
                    cache_profile = make_cache_profile(
                        lang="js",
                        include_weak=include_weak,
                        include_shortcut=include_shortcut,
                        root_types_csv=js_root_types_for_step,
                        roots_mode=roots_mode,
                        profile_override=js_cache_profile,
                    )
                else:
                    cache_profile = make_cache_profile(
                        lang="kotlin",
                        include_weak=include_weak,
                        include_shortcut=include_shortcut,
                        root_types_csv=kt_root_types_csv,
                        roots_mode=roots_mode,
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
                    include_shortcut=include_shortcut,
                    js_root_types_csv=js_root_types_for_step,
                    kt_root_types_csv=kt_root_types_csv,
                    use_cache=True,
                    cache_profile=cache_profile,
                    roots_mode=roots_mode,
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
                    terminals.append(
                        {
                            "verdict": "inconclusive",
                            "reason": str(seg.get("reason", "root_path_not_found")),
                            "segments": segments,
                            "bridges": bridges,
                        }
                    )
                    continue

                root = seg.get("root")
                if root is None:
                    terminals.append(
                        {
                            "verdict": "inconclusive",
                            "reason": "segment_missing_root",
                            "segments": segments,
                            "bridges": bridges,
                        }
                    )
                    continue
                root_node = SimpleNode(int(root.lang), int(root.addr))  # type: ignore[attr-defined]
                anchor_node = _segment_anchor(seg)
                if anchor_node is None:
                    terminals.append(
                        {
                            "verdict": "inconclusive",
                            "reason": "segment_missing_anchor",
                            "segments": segments,
                            "bridges": bridges,
                        }
                    )
                    continue

                if current.lang == LANG_JS:
                    candidates = _bridge_candidates_from_js_anchor(
                        conn=conn,
                        js_snapshot_id=int(js_snapshot_id),
                        kt_snapshot_id=int(kt_snapshot_id),
                        anchor=anchor_node,
                        visited=visited,
                        js_napi_prop=js_napi_prop,
                        kt_napi_field=kt_napi_field,
                        max_candidates=max(1, max_branch_candidates),
                    )
                else:
                    candidates = _bridge_candidates_from_kt_root(
                        conn=conn,
                        js_snapshot_id=int(js_snapshot_id),
                        kt_snapshot_id=int(kt_snapshot_id),
                        root=root_node,
                        visited=visited,
                        max_candidates=max(1, max_branch_candidates),
                    )

                if not candidates:
                    terminals.append(
                        {
                            "verdict": "reached_terminal_root",
                            "segments": segments,
                            "bridges": bridges,
                        }
                    )
                    continue

                loop_candidates = [c for c in candidates if str(c.get("kind")) == "loop"]
                ordered = loop_candidates + [c for c in candidates if str(c.get("kind")) != "loop"]

                for cand in ordered[: max(1, max_branch_candidates)]:
                    from_node = cand["from"]
                    to_node = cand["to"]
                    next_bridges = list(bridges)
                    next_bridges.append(
                        {
                            "step": step,
                            "kind": cand["kind"],
                            "from_lang": LANG_NAME[from_node.lang],
                            "from_addr": from_node.addr,
                            "to_lang": LANG_NAME[to_node.lang],
                            "to_addr": to_node.addr,
                            "ref_kind": str(cand["ref_kind"]),
                            "ref_addr": int(cand["ref_addr"]),
                            "evidence": str(cand.get("evidence", "")),
                            "anchor_lang": LANG_NAME[anchor_node.lang],
                            "anchor_addr": anchor_node.addr,
                            "candidate_count": len(candidates),
                        }
                    )

                    if cand["kind"] == "loop":
                        return {
                            "verdict": "loop_detected",
                            "segments": segments,
                            "bridges": next_bridges,
                        }

                    next_visited = set(visited)
                    next_visited.add(to_node)
                    next_frontier.append(
                        _SearchState(
                            current=to_node,
                            visited=next_visited,
                            segments=segments,
                            bridges=next_bridges,
                        )
                    )

            if not next_frontier:
                break
            frontier = next_frontier[: max(1, max_branch_candidates)]

        if terminals:
            return terminals[0]
        if frontier:
            return {
                "verdict": "inconclusive",
                "reason": "max_steps_reached",
                "segments": frontier[0].segments,
                "bridges": frontier[0].bridges,
            }
        return {
            "verdict": "inconclusive",
            "reason": "max_steps_reached",
            "segments": [],
            "bridges": [],
        }
    finally:
        conn.close()
