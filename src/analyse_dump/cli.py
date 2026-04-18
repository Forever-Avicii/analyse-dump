from __future__ import annotations

import argparse
from pathlib import Path

from analyse_dump import db
from analyse_dump.importers.heapsnapshot_importer import import_heapsnapshot
from analyse_dump.importers.hprof_importer import import_hprof
from analyse_dump.linker import link_with_config
from analyse_dump.root_path_finder import find_root_path


def _format_addr(lang: str, addr: int) -> str:
    if lang == "js":
        return str(int(addr))
    return f"0x{int(addr):x}"


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="analyse-dump",
        description="Offline importers for HPROF and V8/ArkTS heapsnapshot -> SQLite",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_init = sub.add_parser("init-db", help="initialize database schema")
    p_init.add_argument("--db", required=True, type=Path, help="SQLite db path")

    p_hprof = sub.add_parser("import-hprof", help="import .hprof file")
    p_hprof.add_argument("--db", required=True, type=Path, help="SQLite db path")
    p_hprof.add_argument("--input", required=True, type=Path, help="HPROF file path")
    p_hprof.add_argument("--batch-size", type=int, default=5000, help="batch size for inserts")

    p_heap = sub.add_parser("import-heapsnapshot", help="import V8/ArkTS heapsnapshot")
    p_heap.add_argument("--db", required=True, type=Path, help="SQLite db path")
    p_heap.add_argument("--input", required=True, type=Path, help="heapsnapshot file path")
    p_heap.add_argument("--batch-size", type=int, default=10000, help="batch size for inserts")
    p_heap.add_argument(
        "--skip-strings",
        action="store_true",
        help="do not persist the strings table",
    )

    p_link = sub.add_parser("link-xrefs", help="build xrefs and cross_links by config rules")
    p_link.add_argument("--db", required=True, type=Path, help="SQLite db path")
    p_link.add_argument("--config", required=True, type=Path, help="JSON config path")
    p_link.add_argument("--js-snapshot-id", type=int, default=None, help="explicit JS snapshot id")
    p_link.add_argument("--kt-snapshot-id", type=int, default=None, help="explicit Kotlin snapshot id")

    p_path = sub.add_parser("find-root-path", help="find shortest holder path from object to language root")
    p_path.add_argument("--db", required=True, type=Path, help="SQLite db path")
    p_path.add_argument("--addr", required=True, type=str, help="object address (hex like 0xabc or decimal)")
    p_path.add_argument("--lang", type=str, default=None, help="optional: js or kotlin")
    p_path.add_argument("--js-snapshot-id", type=int, default=None, help="explicit JS snapshot id")
    p_path.add_argument("--kt-snapshot-id", type=int, default=None, help="explicit Kotlin snapshot id")
    p_path.add_argument("--max-depth", type=int, default=16, help="max reverse path depth while searching root")
    p_path.add_argument("--max-fanout", type=int, default=512, help="max incoming holders expanded per node")
    p_path.add_argument("--include-weak", action="store_true", help="include weak edges in reverse traversal")
    p_path.add_argument("--js-root-types", type=str, default=None, help="comma-separated JS root type names")
    p_path.add_argument("--kt-root-types", type=str, default=None, help="comma-separated Kotlin root type names")

    return parser


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()

    if args.command == "init-db":
        args.db.parent.mkdir(parents=True, exist_ok=True)
        conn = db.connect(args.db)
        db.init_schema(conn)
        conn.commit()
        conn.close()
        print(f"Initialized schema at: {args.db}")
        return

    if args.command == "import-hprof":
        args.db.parent.mkdir(parents=True, exist_ok=True)
        snapshot_id = import_hprof(args.db, args.input, batch_size=args.batch_size)
        print(f"Imported HPROF snapshot_id={snapshot_id} -> {args.db}")
        return

    if args.command == "import-heapsnapshot":
        args.db.parent.mkdir(parents=True, exist_ok=True)
        snapshot_id = import_heapsnapshot(
            args.db,
            args.input,
            batch_size=args.batch_size,
            store_strings=not args.skip_strings,
        )
        print(f"Imported heapsnapshot snapshot_id={snapshot_id} -> {args.db}")
        return

    if args.command == "link-xrefs":
        results = link_with_config(
            db_path=args.db,
            config_path=args.config,
            js_snapshot_id=args.js_snapshot_id,
            kt_snapshot_id=args.kt_snapshot_id,
        )
        for item in results:
            print(
                f"rule={item['rule']} ref_kind={item['ref_kind']} "
                f"js_xrefs={item['js_xrefs']} kt_xrefs={item['kt_xrefs']} cross_links={item['cross_links']}"
            )
        return

    if args.command == "find-root-path":
        result = find_root_path(
            db_path=args.db,
            addr=args.addr,
            lang=args.lang,
            js_snapshot_id=args.js_snapshot_id,
            kt_snapshot_id=args.kt_snapshot_id,
            max_depth=args.max_depth,
            max_fanout=args.max_fanout,
            include_weak=args.include_weak,
            js_root_types_csv=args.js_root_types,
            kt_root_types_csv=args.kt_root_types,
        )
        if not result["found"]:
            lang_name = str(result.get("lang", args.lang or "kotlin")).lower()
            addr_text = _format_addr("js" if lang_name == "js" else "kotlin", int(result["addr"]))
            print(
                f"found=false addr={addr_text} "
                f"reason={result['reason']}"
            )
            return

        found_lang = str(result["lang"]).lower()
        found_addr = _format_addr("js" if found_lang == "js" else "kotlin", int(result["addr"]))
        print(
            f"found=true addr={found_addr} lang={result['lang']} "
            f"snapshot_id={result['snapshot_id']}"
        )
        root = result["root"]
        root_lang = "js" if root.lang == 0 else "kotlin"
        print(f"root={root_lang}:{_format_addr(root_lang, int(root.addr))}")

        path = result["path"]
        if not path:
            print("path: start node is already a root")
            return

        for idx, item in enumerate(path, start=1):
            holder, held, detail = item
            held_lang = "js" if held.lang == 0 else "kotlin"
            holder_lang = "js" if holder.lang == 0 else "kotlin"
            print(
                f"{idx}. {held_lang}:{_format_addr(held_lang, int(held.addr))} <- "
                f"{holder_lang}:{_format_addr(holder_lang, int(holder.addr))} via {detail}"
            )
        return

    parser.error("Unknown command")


if __name__ == "__main__":
    main()
