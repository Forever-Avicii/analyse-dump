from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Iterable, List, Optional, Tuple

from analyse_dump import db

VENDORED_HPROF_PATH = Path(__file__).resolve().parents[3] / "third_party" / "py-hprof"
if VENDORED_HPROF_PATH.exists() and str(VENDORED_HPROF_PATH) not in sys.path:
    sys.path.insert(0, str(VENDORED_HPROF_PATH))

try:
    import hprof  # type: ignore
    from hprof import _parsing as hprof_parsing  # type: ignore
    from hprof import heap as hprof_heap  # type: ignore
except Exception as exc:  # pragma: no cover
    raise RuntimeError(
        "Cannot import hprof parser. Expected vendored path at "
        f"{VENDORED_HPROF_PATH} or an installed 'hprof' module."
    ) from exc


def _to_addr(value: int) -> str:
    return f"0x{int(value):x}"


def _safe_obj_id(value) -> Optional[int]:
    try:
        return int(hprof_heap.JavaObject._hprof_id.__get__(value))
    except Exception:
        return None


def _iter_instance_field_edges(obj) -> Iterable[Tuple[str, str]]:
    # Walk class hierarchy and extract object-typed instance fields only.
    t = type(obj)
    visited = set()
    while t is not hprof_heap.JavaObject:
        if t in visited:
            break
        visited.add(t)

        if hasattr(t, "_hprof_ifieldix") and hasattr(t, "_hprof_ifieldtypes"):
            field_items = sorted(t._hprof_ifieldix.items(), key=lambda x: x[1])
            try:
                values = t._hprof_ifieldvals.__get__(obj)
            except Exception:
                values = ()

            for field_name, idx in field_items:
                if idx >= len(values):
                    continue
                if idx >= len(t._hprof_ifieldtypes):
                    continue
                if t._hprof_ifieldtypes[idx] is not hprof_parsing.jtype.object:
                    continue
                target = values[idx]
                target_id = _safe_obj_id(target)
                if target_id is None:
                    continue
                yield (str(field_name), _to_addr(target_id))

        bases = getattr(t, "__bases__", ())
        if not bases:
            break
        if len(bases) == 2 and hprof_heap.JavaArray in bases:
            t = bases[1 - bases.index(hprof_heap.JavaArray)]
        else:
            t = bases[0]


def _iter_array_edges(obj) -> Iterable[Tuple[str, str]]:
    if not isinstance(obj, hprof_heap.JavaArray):
        return
    try:
        for i, elem in enumerate(obj):
            target_id = _safe_obj_id(elem)
            if target_id is None:
                continue
            yield (str(i), _to_addr(target_id))
    except Exception:
        return


def import_hprof(db_path: Path, hprof_path: Path, batch_size: int = 5000) -> int:
    conn = db.connect(db_path)
    db.init_schema(conn)

    snapshot_id = db.create_snapshot(
        conn,
        snapshot_type="hprof",
        source_path=str(hprof_path),
        meta={"parser": "py-hprof", "note": "py-hprof loads the full heap into memory"},
    )

    object_rows: List[Tuple[int, str, str, str, Optional[int], Optional[int], Optional[str]]] = []
    edge_rows: List[Tuple[int, str, str, Optional[str], Optional[str]]] = []

    hf = hprof.open(str(hprof_path))
    try:
        for heap_index, heap in enumerate(hf.heaps):
            for _, obj in heap.items():
                obj_id = _safe_obj_id(obj)
                if obj_id is None:
                    continue

                obj_addr = _to_addr(obj_id)
                type_name = str(type(obj))
                extra = json.dumps({"heap_index": heap_index}, ensure_ascii=True)
                object_rows.append((snapshot_id, "kotlin", obj_addr, type_name, None, None, extra))

                for field_name, to_addr in _iter_instance_field_edges(obj):
                    edge_rows.append((snapshot_id, obj_addr, to_addr, "field", field_name))
                for index_name, to_addr in _iter_array_edges(obj):
                    edge_rows.append((snapshot_id, obj_addr, to_addr, "array_element", index_name))

                if len(object_rows) >= batch_size:
                    db.insert_objects(conn, object_rows)
                    object_rows.clear()
                if len(edge_rows) >= batch_size:
                    db.insert_edges(conn, edge_rows)
                    edge_rows.clear()

        if object_rows:
            db.insert_objects(conn, object_rows)
        if edge_rows:
            db.insert_edges(conn, edge_rows)

        conn.commit()
    finally:
        try:
            hf.close()
        except Exception:
            pass
        conn.close()

    return snapshot_id
