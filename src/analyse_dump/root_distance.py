from __future__ import annotations

from collections import deque
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

from analyse_dump import db
from analyse_dump.const import LANG_JS, LANG_KOTLIN

LANG_BY_NAME = {
    "js": LANG_JS,
    "kotlin": LANG_KOTLIN,
}

DEFAULT_JS_ROOT_TYPES = {"synthetic", "native", "handle"}
DEFAULT_KT_ROOT_TYPES = {"kotlin.native.internal.StableRef"}
DEFAULT_JS_PSEUDO_ROOT_ADDRS = {0, 1}


def make_cache_profile(
    lang: str,
    include_weak: bool,
    root_types_csv: Optional[str],
    profile_override: Optional[str] = None,
) -> str:
    if profile_override is not None and profile_override.strip():
        return profile_override.strip()
    lang_norm = lang.strip().lower()
    roots = (root_types_csv or "default").strip()
    return f"v1|lang={lang_norm}|weak={1 if include_weak else 0}|roots={roots}"


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


def _choose_snapshot_id(conn, lang_code: int, snapshot_id: Optional[int]) -> int:
    if snapshot_id is not None:
        return int(snapshot_id)
    if lang_code == LANG_JS:
        return _fetch_latest_snapshot_id(conn, "heapsnapshot")
    return _fetch_latest_snapshot_id(conn, "hprof")


def _load_roots(
    conn,
    snapshot_id: int,
    lang_code: int,
    js_root_types_csv: Optional[str],
    kt_root_types_csv: Optional[str],
) -> List[int]:
    # If caller explicitly passed root-types override, honor it and bypass roots table.
    has_explicit_override = (
        (lang_code == LANG_JS and js_root_types_csv is not None)
        or (lang_code == LANG_KOTLIN and kt_root_types_csv is not None)
    )
    if not has_explicit_override:
        rows = conn.execute(
            """
            SELECT DISTINCT obj_addr
            FROM roots
            WHERE snapshot_id = ?
              AND lang = ?
            """,
            (snapshot_id, lang_code),
        ).fetchall()
        if rows:
            return sorted({int(r[0]) for r in rows})

    if lang_code == LANG_JS:
        root_types = _split_csv(js_root_types_csv) or set(DEFAULT_JS_ROOT_TYPES)
        rows = conn.execute(
            """
            SELECT DISTINCT obj_addr
            FROM objects
            WHERE snapshot_id = ?
              AND lang = ?
              AND (
                obj_addr IN (0, 1)
                OR type_name IN ({})
              )
            """.format(",".join("?" for _ in root_types)),
            (snapshot_id, lang_code, *sorted(root_types)),
        ).fetchall()
    else:
        root_types = _split_csv(kt_root_types_csv) or set(DEFAULT_KT_ROOT_TYPES)
        rows = conn.execute(
            """
            SELECT DISTINCT obj_addr
            FROM objects
            WHERE snapshot_id = ?
              AND lang = ?
              AND type_name IN ({})
            """.format(",".join("?" for _ in root_types)),
            (snapshot_id, lang_code, *sorted(root_types)),
        ).fetchall()

    roots = sorted({int(r[0]) for r in rows})
    if lang_code == LANG_JS:
        # Ensure pseudo roots exist in seed set even if objects table misses one row.
        roots = sorted(set(roots) | set(DEFAULT_JS_PSEUDO_ROOT_ADDRS))
    return roots


def _iter_outgoing_neighbors(
    conn,
    snapshot_id: int,
    from_addr: int,
    include_weak: bool,
    max_fanout: int,
) -> Iterable[int]:
    if include_weak:
        rows = conn.execute(
            """
            SELECT to_obj_addr
            FROM edges
            WHERE snapshot_id = ?
              AND from_obj_addr = ?
            LIMIT ?
            """,
            (snapshot_id, from_addr, max_fanout),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT to_obj_addr
            FROM edges
            WHERE snapshot_id = ?
              AND from_obj_addr = ?
              AND edge_type != 7
            LIMIT ?
            """,
            (snapshot_id, from_addr, max_fanout),
        ).fetchall()
    for (to_addr,) in rows:
        yield int(to_addr)


def build_root_distance(
    db_path: Path,
    lang: str,
    profile: str = "default",
    snapshot_id: Optional[int] = None,
    include_weak: bool = False,
    max_fanout: int = 20000,
    js_root_types_csv: Optional[str] = None,
    kt_root_types_csv: Optional[str] = None,
    batch_size: int = 20000,
) -> Dict[str, object]:
    lang_norm = lang.strip().lower()
    if lang_norm not in LANG_BY_NAME:
        raise ValueError("--lang must be js or kotlin")
    lang_code = LANG_BY_NAME[lang_norm]

    conn = db.connect(db_path)
    try:
        # Ensure latest schema objects exist (safe no-op if already present).
        db.init_schema(conn)
        sid = _choose_snapshot_id(conn, lang_code=lang_code, snapshot_id=snapshot_id)
        roots = _load_roots(
            conn,
            snapshot_id=sid,
            lang_code=lang_code,
            js_root_types_csv=js_root_types_csv,
            kt_root_types_csv=kt_root_types_csv,
        )
        if not roots:
            return {"snapshot_id": sid, "lang": lang_code, "roots": 0, "nodes": 0}

        dist: Dict[int, int] = {}
        parent: Dict[int, Optional[int]] = {}
        q = deque()
        for r in roots:
            if r in dist:
                continue
            dist[r] = 0
            parent[r] = None
            q.append(r)

        while q:
            cur = q.popleft()
            cur_dist = dist[cur]
            for nei in _iter_outgoing_neighbors(
                conn,
                snapshot_id=sid,
                from_addr=cur,
                include_weak=include_weak,
                max_fanout=max_fanout,
            ):
                if nei in dist:
                    continue
                dist[nei] = cur_dist + 1
                parent[nei] = cur
                q.append(nei)

        conn.execute(
            "DELETE FROM root_distance_cache WHERE snapshot_id = ? AND lang = ? AND profile = ?",
            (sid, lang_code, profile),
        )

        rows: List[Tuple[int, int, str, int, int, Optional[int]]] = []
        for obj_addr, d in dist.items():
            rows.append((sid, lang_code, profile, int(obj_addr), int(d), parent.get(obj_addr)))
            if len(rows) >= batch_size:
                conn.executemany(
                    """
                    INSERT INTO root_distance_cache(snapshot_id, lang, profile, obj_addr, dist, parent_addr)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    rows,
                )
                rows.clear()
        if rows:
            conn.executemany(
                """
                INSERT INTO root_distance_cache(snapshot_id, lang, profile, obj_addr, dist, parent_addr)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                rows,
            )

        conn.commit()
        return {
            "snapshot_id": sid,
            "lang": lang_code,
            "profile": profile,
            "roots": len(roots),
            "nodes": len(dist),
        }
    finally:
        conn.close()
