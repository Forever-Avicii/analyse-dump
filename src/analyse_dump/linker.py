from __future__ import annotations
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Set, Tuple

from analyse_dump import db
from analyse_dump.const import (
    EDGE_ARRAY_ELEMENT,
    EDGE_ELEMENT,
    EDGE_HIDDEN,
    EDGE_INTERNAL,
    EDGE_PROPERTY,
    EDGE_SHORTCUT,
    EDGE_TYPE_BY_NAME,
    LANG_JS,
    LANG_KOTLIN,
    NAME_KIND_STRING_INDEX,
    REF_KIND_BY_NAME,
    REF_KIND_UNKNOWN,
)


@dataclass
class RuleContext:
    db_path: Path
    js_snapshot_id: int
    kt_snapshot_id: int


def _load_config(config_path: Path) -> dict:
    return json.loads(config_path.read_text(encoding="utf-8"))


def _fetch_latest_snapshot_id(conn, snapshot_type: str) -> int:
    row = conn.execute(
        "SELECT id FROM snapshots WHERE type = ? ORDER BY id DESC LIMIT 1",
        (snapshot_type,),
    ).fetchone()
    if row is None:
        raise ValueError(f"No snapshot found for type={snapshot_type}")
    return int(row[0])


def _parse_int(value: object, fmt: str) -> Optional[int]:
    if value is None:
        return None
    s = str(value).strip().lower()
    if not s:
        return None

    try:
        if fmt == "hex":
            return int(s, 16)
        if fmt == "dec":
            return int(s, 10)
        if s.startswith("0x"):
            return int(s, 16)
        if s.isdigit() or (s.startswith("-") and s[1:].isdigit()):
            return int(s, 10)
        return int(s, 16)
    except Exception:
        return None


def _resolve_string_index(conn, snapshot_id: int, value: str) -> List[int]:
    rows = conn.execute(
        "SELECT string_index FROM heap_strings WHERE snapshot_id = ? AND value = ?",
        (snapshot_id, value),
    ).fetchall()
    return [int(r[0]) for r in rows]


def _resolve_name_index_for_node(conn, snapshot_id: int, obj_addr: int) -> Optional[int]:
    row = conn.execute(
        "SELECT name_index FROM objects WHERE snapshot_id = ? AND obj_addr = ?",
        (snapshot_id, obj_addr),
    ).fetchone()
    if row is None or row[0] is None:
        return None
    return int(row[0])


def _resolve_string_value(conn, snapshot_id: int, string_index: int) -> Optional[str]:
    row = conn.execute(
        "SELECT value FROM heap_strings WHERE snapshot_id = ? AND string_index = ?",
        (snapshot_id, string_index),
    ).fetchone()
    if row is None:
        return None
    return str(row[0])


def _to_edge_codes(edge_types: Optional[Sequence[str]]) -> Optional[List[int]]:
    if edge_types is None:
        return None
    out = []
    for name in edge_types:
        out.append(EDGE_TYPE_BY_NAME.get(name, 0))
    return out


def _extract_js_property_values(
    conn,
    js_snapshot_id: int,
    property_name: str,
    value_format: str,
    edge_types: Optional[Sequence[str]] = None,
    max_depth: int = 2,
) -> Dict[int, Set[int]]:
    property_indexes = _resolve_string_index(conn, js_snapshot_id, property_name)
    if not property_indexes:
        return {}

    markers_placeholder = ",".join("?" for _ in property_indexes)
    edge_type_codes = _to_edge_codes(edge_types)

    if edge_type_codes:
        edge_placeholder = ",".join("?" for _ in edge_type_codes)
        rows = conn.execute(
            f"""
            SELECT from_obj_addr, to_obj_addr
            FROM edges
            WHERE snapshot_id = ?
              AND name_kind = ?
              AND name_num IN ({markers_placeholder})
              AND edge_type IN ({edge_placeholder})
            """,
            (js_snapshot_id, NAME_KIND_STRING_INDEX, *property_indexes, *edge_type_codes),
        ).fetchall()
    else:
        rows = conn.execute(
            f"""
            SELECT from_obj_addr, to_obj_addr
            FROM edges
            WHERE snapshot_id = ?
              AND name_kind = ?
              AND name_num IN ({markers_placeholder})
            """,
            (js_snapshot_id, NAME_KIND_STRING_INDEX, *property_indexes),
        ).fetchall()

    if not rows:
        return {}

    owner_to_targets: Dict[int, Set[int]] = {}
    for owner, target in rows:
        owner_to_targets.setdefault(int(owner), set()).add(int(target))

    owner_to_refs: Dict[int, Set[int]] = {owner: set() for owner in owner_to_targets}
    cache: Dict[int, Set[int]] = {}

    traversable_types = (
        EDGE_ELEMENT,
        EDGE_HIDDEN,
        EDGE_ARRAY_ELEMENT,
        EDGE_PROPERTY,
        EDGE_INTERNAL,
        EDGE_SHORTCUT,
    )

    def _collect_ref_values(start_addr: int) -> Set[int]:
        if start_addr in cache:
            return cache[start_addr]

        ref_values: Set[int] = set()
        visited: Set[int] = set()
        queue: List[Tuple[int, int]] = [(start_addr, 0)]

        while queue:
            node_addr, depth = queue.pop(0)
            if node_addr in visited:
                continue
            visited.add(node_addr)

            name_index = _resolve_name_index_for_node(conn, js_snapshot_id, node_addr)
            if name_index is not None:
                value_text = _resolve_string_value(conn, js_snapshot_id, name_index)
                iv = _parse_int(value_text, value_format)
                if iv is not None:
                    ref_values.add(iv)

            if depth >= max_depth:
                continue

            child_rows = conn.execute(
                """
                SELECT to_obj_addr
                FROM edges
                WHERE snapshot_id = ?
                  AND from_obj_addr = ?
                  AND edge_type IN (?, ?, ?, ?, ?, ?)
                LIMIT 4096
                """,
                (
                    js_snapshot_id,
                    node_addr,
                    traversable_types[0],
                    traversable_types[1],
                    traversable_types[2],
                    traversable_types[3],
                    traversable_types[4],
                    traversable_types[5],
                ),
            ).fetchall()
            for (child_addr,) in child_rows:
                queue.append((int(child_addr), depth + 1))

        cache[start_addr] = ref_values
        return ref_values

    for owner, targets in owner_to_targets.items():
        for target_addr in targets:
            owner_to_refs.setdefault(owner, set()).update(_collect_ref_values(target_addr))

    return owner_to_refs


def _extract_kt_field_refs(
    conn,
    kt_snapshot_id: int,
    field_name: str,
    value_format: str,
    type_filter: Optional[Sequence[str]] = None,
) -> Dict[int, Set[int]]:
    if type_filter:
        placeholders = ",".join("?" for _ in type_filter)
        rows = conn.execute(
            f"""
            SELECT f.obj_addr, f.field_value_int, f.field_value_text
            FROM object_fields f
            JOIN objects o
              ON o.snapshot_id = f.snapshot_id
             AND o.obj_addr = f.obj_addr
            WHERE f.snapshot_id = ?
              AND f.field_name = ?
              AND o.type_name IN ({placeholders})
            """,
            (kt_snapshot_id, field_name, *type_filter),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT obj_addr, field_value_int, field_value_text
            FROM object_fields
            WHERE snapshot_id = ?
              AND field_name = ?
            """,
            (kt_snapshot_id, field_name),
        ).fetchall()

    out: Dict[int, Set[int]] = {}
    for obj_addr, field_value_int, field_value_text in rows:
        iv = int(field_value_int) if field_value_int is not None else _parse_int(field_value_text, value_format)
        if iv is None:
            continue
        out.setdefault(int(obj_addr), set()).add(iv)
    return out


def _extract_kt_stable_refs(
    conn,
    kt_snapshot_id: int,
    stable_ref_type: str,
) -> Dict[int, Set[int]]:
    rows = conn.execute(
        """
        SELECT obj_addr
        FROM objects
        WHERE snapshot_id = ?
          AND lang = ?
          AND type_name = ?
        """,
        (kt_snapshot_id, LANG_KOTLIN, stable_ref_type),
    ).fetchall()

    out: Dict[int, Set[int]] = {}
    for (obj_addr,) in rows:
        addr = int(obj_addr)
        out.setdefault(addr, set()).add(addr)
    return out


def _clear_rule_rows(conn, js_snapshot_id: int, kt_snapshot_id: int, ref_kind: int) -> None:
    conn.execute(
        "DELETE FROM cross_links WHERE snapshot_id IN (?, ?) AND ref_kind = ?",
        (js_snapshot_id, kt_snapshot_id, ref_kind),
    )
    conn.execute(
        "DELETE FROM xrefs WHERE snapshot_id IN (?, ?) AND ref_kind = ?",
        (js_snapshot_id, kt_snapshot_id, ref_kind),
    )


def _insert_xrefs(
    conn,
    snapshot_id: int,
    lang: int,
    owner_to_refs: Dict[int, Set[int]],
    ref_kind: int,
) -> int:
    rows = []
    for owner, refs in owner_to_refs.items():
        for ref in refs:
            rows.append((snapshot_id, lang, ref, owner, ref_kind))
    if not rows:
        return 0
    conn.executemany(
        """
        INSERT INTO xrefs(snapshot_id, lang, ref_addr, owner_obj_addr, ref_kind)
        VALUES (?, ?, ?, ?, ?)
        """,
        rows,
    )
    return len(rows)


def _insert_cross_links(
    conn,
    js_snapshot_id: int,
    kt_snapshot_id: int,
    js_owner_to_refs: Dict[int, Set[int]],
    kt_owner_to_refs: Dict[int, Set[int]],
    ref_kind: int,
) -> int:
    ref_to_js: Dict[int, Set[int]] = {}
    ref_to_kt: Dict[int, Set[int]] = {}

    for js_obj, refs in js_owner_to_refs.items():
        for r in refs:
            ref_to_js.setdefault(r, set()).add(js_obj)
    for kt_obj, refs in kt_owner_to_refs.items():
        for r in refs:
            ref_to_kt.setdefault(r, set()).add(kt_obj)

    rows = []
    common_refs = set(ref_to_js).intersection(ref_to_kt)
    for ref in common_refs:
        for js_obj in ref_to_js[ref]:
            for kt_obj in ref_to_kt[ref]:
                rows.append((js_snapshot_id, js_obj, kt_obj, ref, ref_kind))

    if not rows:
        return 0

    conn.executemany(
        """
        INSERT INTO cross_links(snapshot_id, js_obj_addr, kt_obj_addr, ref_addr, ref_kind)
        VALUES (?, ?, ?, ?, ?)
        """,
        rows,
    )
    return len(rows)


def _run_rule(conn, ctx: RuleContext, rule: dict) -> dict:
    rule_name = rule.get("name", "unnamed-rule")
    rule_type = rule.get("type")
    ref_kind_name = rule.get("ref_kind", rule_name)
    ref_kind = REF_KIND_BY_NAME.get(ref_kind_name, REF_KIND_UNKNOWN)

    _clear_rule_rows(conn, ctx.js_snapshot_id, ctx.kt_snapshot_id, ref_kind)

    if rule_type == "arkts_holds_kotlin_stable_ref":
        js_owner_to_refs = _extract_js_property_values(
            conn,
            ctx.js_snapshot_id,
            property_name=rule["js_property"],
            value_format=rule.get("js_value_format", "hex"),
            edge_types=rule.get("js_edge_types"),
            max_depth=int(rule.get("js_value_search_depth", 2)),
        )
        kt_owner_to_refs = _extract_kt_stable_refs(
            conn,
            ctx.kt_snapshot_id,
            stable_ref_type=rule.get("kt_stable_ref_type", "kotlin.native.internal.StableRef"),
        )

    elif rule_type == "kotlin_holds_arkts_napi_ref":
        js_owner_to_refs = _extract_js_property_values(
            conn,
            ctx.js_snapshot_id,
            property_name=rule["js_property"],
            value_format=rule.get("js_value_format", "hex"),
            edge_types=rule.get("js_edge_types"),
            max_depth=int(rule.get("js_value_search_depth", 2)),
        )
        kt_owner_to_refs = _extract_kt_field_refs(
            conn,
            ctx.kt_snapshot_id,
            field_name=rule.get("kt_field", "ref"),
            value_format=rule.get("kt_value_format", "dec"),
            type_filter=rule.get("kt_type_filter"),
        )

    else:
        raise ValueError(f"Unsupported rule type: {rule_type}")

    js_x = _insert_xrefs(conn, ctx.js_snapshot_id, LANG_JS, js_owner_to_refs, ref_kind)
    kt_x = _insert_xrefs(conn, ctx.kt_snapshot_id, LANG_KOTLIN, kt_owner_to_refs, ref_kind)
    links = _insert_cross_links(
        conn,
        ctx.js_snapshot_id,
        ctx.kt_snapshot_id,
        js_owner_to_refs,
        kt_owner_to_refs,
        ref_kind=ref_kind,
    )

    return {
        "rule": rule_name,
        "ref_kind": ref_kind_name,
        "js_xrefs": js_x,
        "kt_xrefs": kt_x,
        "cross_links": links,
    }


def link_with_config(
    db_path: Path,
    config_path: Path,
    js_snapshot_id: Optional[int] = None,
    kt_snapshot_id: Optional[int] = None,
) -> List[dict]:
    config = _load_config(config_path)
    rules = config.get("rules", [])
    if not rules:
        raise ValueError("Config must include non-empty 'rules'")

    conn = db.connect(db_path)
    try:
        db.init_schema(conn)

        if js_snapshot_id is None:
            js_snapshot_id = _fetch_latest_snapshot_id(conn, "heapsnapshot")
        if kt_snapshot_id is None:
            kt_snapshot_id = _fetch_latest_snapshot_id(conn, "hprof")

        ctx = RuleContext(
            db_path=db_path,
            js_snapshot_id=int(js_snapshot_id),
            kt_snapshot_id=int(kt_snapshot_id),
        )

        results = []
        for rule in rules:
            results.append(_run_rule(conn, ctx, rule))

        conn.commit()
        return results
    finally:
        conn.close()
