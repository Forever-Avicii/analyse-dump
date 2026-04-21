from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Iterable, List, Optional, Tuple

from analyse_dump import db
from analyse_dump.const import EDGE_ARRAY_ELEMENT, EDGE_FIELD, LANG_KOTLIN, NAME_KIND_ARRAY_INDEX, NAME_KIND_FIELD_NAME

VENDORED_HPROF_PATH = Path(__file__).resolve().parents[3] / "third_party" / "py-hprof"
if VENDORED_HPROF_PATH.exists() and str(VENDORED_HPROF_PATH) not in sys.path:
    sys.path.insert(0, str(VENDORED_HPROF_PATH))

try:
    import hprof  # type: ignore
    from hprof import callstack as hprof_callstack  # type: ignore
    from hprof import _parsing as hprof_parsing  # type: ignore
    from hprof import heap as hprof_heap  # type: ignore
except Exception as exc:  # pragma: no cover
    raise RuntimeError(
        "Cannot import hprof parser. Expected vendored path at "
        f"{VENDORED_HPROF_PATH} or an installed 'hprof' module."
    ) from exc

_HPROF_PATCHED = False
_UNKNOWN_CLASS_SEQ = 0
_INT_RE = re.compile(r"^-?\d+$")


def _safe_obj_id(value) -> Optional[int]:
    try:
        return int(hprof_heap.JavaObject._hprof_id.__get__(value))
    except Exception:
        return None


def _iter_instance_field_edges(obj) -> Iterable[Tuple[str, int]]:
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
                yield (str(field_name), int(target_id))

        bases = getattr(t, "__bases__", ())
        if not bases:
            break
        if len(bases) == 2 and hprof_heap.JavaArray in bases:
            t = bases[1 - bases.index(hprof_heap.JavaArray)]
        else:
            t = bases[0]


def _iter_instance_scalar_fields(obj) -> Iterable[Tuple[str, Optional[int], Optional[str], str]]:
    # Extract non-object instance fields for configurable rules.
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
                ftype = t._hprof_ifieldtypes[idx]
                if ftype is hprof_parsing.jtype.object:
                    continue
                value = values[idx]
                value_text = str(value) if value is not None else None
                value_int = int(value_text) if value_text and _INT_RE.match(value_text) else None
                value_type = getattr(ftype, "name", type(value).__name__)
                yield (str(field_name), value_int, value_text, str(value_type))

        bases = getattr(t, "__bases__", ())
        if not bases:
            break
        if len(bases) == 2 and hprof_heap.JavaArray in bases:
            t = bases[1 - bases.index(hprof_heap.JavaArray)]
        else:
            t = bases[0]


def _iter_array_edges(obj) -> Iterable[Tuple[int, int]]:
    if not isinstance(obj, hprof_heap.JavaArray):
        return
    try:
        for i, elem in enumerate(obj):
            target_id = _safe_obj_id(elem)
            if target_id is None:
                continue
            yield (int(i), int(target_id))
    except Exception:
        return


def _patch_hprof_parser_if_needed() -> None:
    global _HPROF_PATCHED
    if _HPROF_PATCHED:
        return

    def _parse_stack_frame_record_tolerant(hf, reader, progresscb):
        del progresscb  # unused
        frame = hprof_callstack.Frame()
        fid = reader.id()
        frame.method = hf.names[reader.id()]
        frame.signature = hf.names[reader.id()]
        frame.sourcefile = hf.names[reader.id()]
        classload_serial = reader.u4()
        classload = hf.classloads.get(classload_serial)
        if classload is None:
            frame.class_name = f"<unknown_classload_{classload_serial}>"
        else:
            frame.class_name = classload.class_name
        frame.line = reader.i4()
        if fid in hf.stackframes:
            raise hprof_parsing.FormatError("duplicate stack frame id 0x%x" % fid)
        hf.stackframes[fid] = frame

    hprof_parsing.RECORD_PARSERS[0x04] = _parse_stack_frame_record_tolerant

    original_create_class = hprof_heap._create_class

    def _sanitize_class_name(raw_name: object) -> str:
        global _UNKNOWN_CLASS_SEQ
        if not isinstance(raw_name, str):
            _UNKNOWN_CLASS_SEQ += 1
            return f"unknown/NonStringClass{_UNKNOWN_CLASS_SEQ}"

        name = raw_name.strip()
        if not name:
            _UNKNOWN_CLASS_SEQ += 1
            return f"unknown/EmptyClass{_UNKNOWN_CLASS_SEQ}"

        name = name.replace(".", "/")
        name = "/".join(part if part else "__empty__" for part in name.split("/"))
        if "$$" in name:
            base, extra = name.split("$$", 1)
            base = "$".join(part if part else "__anon__" for part in base.split("$"))
            name = base + "$$" + (extra if extra else "__anon__")
        else:
            name = "$".join(part if part else "__anon__" for part in name.split("$"))
        if name.startswith("$"):
            name = "__anon__" + name
        if name.endswith("$"):
            name = name + "__anon__"
        return name

    def _create_class_tolerant(container, name, supercls, staticattrs, iattr_names, iattr_types):
        try:
            return original_create_class(container, name, supercls, staticattrs, iattr_names, iattr_types)
        except AssertionError:
            safe_name = _sanitize_class_name(name)
            return original_create_class(container, safe_name, supercls, staticattrs, iattr_names, iattr_types)

    def _resolve_references_tolerant(hf, progresscb):
        if progresscb:
            progresscb("resolving stacktraces", None, None)
        for load in hf.classloads.values():
            if isinstance(load.stacktrace, int):
                load.stacktrace = hf.stacktraces.get(load.stacktrace)
        from hprof import _heap_parsing as _patched_heap_parsing  # type: ignore
        if hf._pending_heap is not None:
            raise hprof_parsing.FormatError("unfinished segmented heap")
        for heapix, heap in enumerate(hf.heaps, start=1):
            n = len(heap)
            label = "resolving heap %d/%d" % (heapix, len(hf.heaps))

            def innerprogress(pos):
                progresscb(label, pos, n)

            if not progresscb:
                innerprogress = None
            _patched_heap_parsing.resolve_heap_references(heap, innerprogress)

    hprof_heap._create_class = _create_class_tolerant
    hprof_parsing._resolve_references = _resolve_references_tolerant
    _HPROF_PATCHED = True


def import_hprof(db_path: Path, hprof_path: Path, batch_size: int = 5000) -> int:
    _patch_hprof_parser_if_needed()
    conn = db.connect(db_path)
    db.init_schema(conn)

    snapshot_id = db.create_snapshot(
        conn,
        snapshot_type="hprof",
        source_path=str(hprof_path),
        meta={"parser": "py-hprof", "note": "py-hprof loads the full heap into memory"},
    )

    object_rows: List[Tuple[int, int, int, Optional[str], Optional[int], Optional[int], Optional[int]]] = []
    edge_rows: List[Tuple[int, int, int, int, int, Optional[int], Optional[str]]] = []
    field_rows: List[Tuple[int, int, int, str, Optional[int], Optional[str], Optional[str]]] = []

    raw = hprof_path.read_bytes()
    hf = hprof.parse(raw)
    try:
        for heap_index, heap in enumerate(hf.heaps):
            for _, obj in heap.items():
                obj_id = _safe_obj_id(obj)
                if obj_id is None:
                    continue

                type_name = str(type(obj))
                object_rows.append((snapshot_id, LANG_KOTLIN, int(obj_id), type_name, None, heap_index, None))

                for field_name, to_addr in _iter_instance_field_edges(obj):
                    edge_rows.append((snapshot_id, int(obj_id), to_addr, EDGE_FIELD, NAME_KIND_FIELD_NAME, None, field_name))
                for field_name, value_int, value_text, value_type in _iter_instance_scalar_fields(obj):
                    field_rows.append(
                        (
                            snapshot_id,
                            LANG_KOTLIN,
                            int(obj_id),
                            field_name,
                            value_int,
                            value_text,
                            value_type,
                        )
                    )
                for index_num, to_addr in _iter_array_edges(obj):
                    edge_rows.append(
                        (
                            snapshot_id,
                            int(obj_id),
                            to_addr,
                            EDGE_ARRAY_ELEMENT,
                            NAME_KIND_ARRAY_INDEX,
                            index_num,
                            None,
                        )
                    )

                if len(object_rows) >= batch_size:
                    db.insert_objects(conn, object_rows)
                    object_rows.clear()
                if len(edge_rows) >= batch_size:
                    db.insert_edges(conn, edge_rows)
                    edge_rows.clear()
                if len(field_rows) >= batch_size:
                    db.insert_object_fields(conn, field_rows)
                    field_rows.clear()

        if object_rows:
            db.insert_objects(conn, object_rows)
        if edge_rows:
            db.insert_edges(conn, edge_rows)
        if field_rows:
            db.insert_object_fields(conn, field_rows)

        root_rows = conn.execute(
            """
            SELECT DISTINCT obj_addr, type_name
            FROM objects
            WHERE snapshot_id = ?
              AND lang = ?
              AND type_name LIKE ?
            """,
            (snapshot_id, LANG_KOTLIN, "%kotlin.native.internal.StableRef%"),
        ).fetchall()
        if root_rows:
            db.insert_roots(
                conn,
                [
                    (
                        snapshot_id,
                        LANG_KOTLIN,
                        int(obj_addr),
                        str(type_name),
                        "hprof_type",
                        "medium",
                        None,
                    )
                    for obj_addr, type_name in root_rows
                ],
            )

        conn.commit()
    finally:
        try:
            hf.close()
        except Exception:
            pass
        conn.close()

    return snapshot_id
