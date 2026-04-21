from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional, Tuple

from analyse_dump import db
from analyse_dump.chain_analyzer import analyze_chain
from analyse_dump.const import LANG_JS, LANG_KOTLIN


def _fetch_latest_snapshot_id(conn, snapshot_type: str) -> int:
    row = conn.execute(
        "SELECT id FROM snapshots WHERE type = ? ORDER BY id DESC LIMIT 1",
        (snapshot_type,),
    ).fetchone()
    if row is None:
        raise ValueError(f"No snapshot found for type={snapshot_type}")
    return int(row[0])


def _node_label(
    conn,
    snapshot_id: int,
    lang: int,
    addr: int,
) -> Dict[str, Optional[str]]:
    if lang == LANG_JS:
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
            (snapshot_id, lang, addr),
        ).fetchone()
        if row is None:
            return {"type_name": None, "name": None}
        return {
            "type_name": str(row[0]) if row[0] is not None else None,
            "name": str(row[1]) if row[1] is not None else None,
        }

    row = conn.execute(
        """
        SELECT type_name
        FROM objects
        WHERE snapshot_id = ?
          AND lang = ?
          AND obj_addr = ?
        LIMIT 1
        """,
        (snapshot_id, lang, addr),
    ).fetchone()
    return {"type_name": (str(row[0]) if row and row[0] is not None else None), "name": None}


def run_agent_analysis(
    db_path: Path,
    addr: str,
    lang: str,
    js_snapshot_id: Optional[int] = None,
    kt_snapshot_id: Optional[int] = None,
    max_steps: int = 8,
    max_depth: int = 16,
    max_fanout: int = 512,
    include_weak: bool = False,
    roots_mode: str = "mixed",
    js_root_types_csv: Optional[str] = None,
    kt_root_types_csv: Optional[str] = None,
    js_napi_prop: str = "knapi_refs_test",
    kt_napi_field: str = "ref",
    js_cache_profile: Optional[str] = None,
    kt_cache_profile: Optional[str] = None,
    max_branch_candidates: int = 4,
) -> Dict[str, object]:
    chain = analyze_chain(
        db_path=db_path,
        addr=addr,
        lang=lang,
        js_snapshot_id=js_snapshot_id,
        kt_snapshot_id=kt_snapshot_id,
        max_steps=max_steps,
        max_depth=max_depth,
        max_fanout=max_fanout,
        include_weak=include_weak,
        roots_mode=roots_mode,
        js_root_types_csv=js_root_types_csv,
        kt_root_types_csv=kt_root_types_csv,
        js_napi_prop=js_napi_prop,
        kt_napi_field=kt_napi_field,
        js_cache_profile=js_cache_profile,
        kt_cache_profile=kt_cache_profile,
        max_branch_candidates=max_branch_candidates,
    )

    conn = db.connect(db_path)
    try:
        if js_snapshot_id is None:
            js_snapshot_id = _fetch_latest_snapshot_id(conn, "heapsnapshot")
        if kt_snapshot_id is None:
            kt_snapshot_id = _fetch_latest_snapshot_id(conn, "hprof")

        suspects: List[Dict[str, object]] = []
        for b in chain.get("bridges", []):
            from_lang = str(b.get("from_lang", ""))
            from_addr = int(b.get("from_addr", 0))
            if from_lang == "js":
                label = _node_label(conn, int(js_snapshot_id), LANG_JS, from_addr)
                suspects.append(
                    {
                        "lang": "js",
                        "addr": from_addr,
                        "type_name": label.get("type_name"),
                        "name": label.get("name"),
                        "reason": f"bridge-source {b.get('ref_kind')} ({b.get('kind')})",
                    }
                )
            elif from_lang == "kotlin":
                label = _node_label(conn, int(kt_snapshot_id), LANG_KOTLIN, from_addr)
                suspects.append(
                    {
                        "lang": "kotlin",
                        "addr": from_addr,
                        "type_name": label.get("type_name"),
                        "name": None,
                        "reason": f"bridge-source {b.get('ref_kind')} ({b.get('kind')})",
                    }
                )

        dedup: Dict[Tuple[str, int], Dict[str, object]] = {}
        for s in suspects:
            dedup[(str(s["lang"]), int(s["addr"]))] = s
        suspects = list(dedup.values())

        verdict = str(chain.get("verdict", "inconclusive"))
        if verdict == "loop_detected":
            confidence = "high"
            summary = "Cross-language retain cycle detected."
        elif verdict == "reached_terminal_root":
            confidence = "medium"
            summary = "No cycle in explored branches; chain ends at terminal roots."
        else:
            confidence = "low"
            summary = "Analysis inconclusive within current limits."

        next_actions = [
            "Validate release lifecycle around JS bridge anchors (knapi_refs_test owners).",
            "Validate Kotlin owners that keep napi_ref/stable_ref alive across segment boundaries.",
            "Re-run with larger --max-steps or --max-branch-candidates if multiple holders are expected.",
        ]

        return {
            "summary": summary,
            "confidence": confidence,
            "chain": chain,
            "suspects": suspects,
            "next_actions": next_actions,
        }
    finally:
        conn.close()
