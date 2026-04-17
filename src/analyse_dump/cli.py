from __future__ import annotations

import argparse
from pathlib import Path

from analyse_dump import db
from analyse_dump.importers.heapsnapshot_importer import import_heapsnapshot
from analyse_dump.importers.hprof_importer import import_hprof


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

    parser.error("Unknown command")


if __name__ == "__main__":
    main()
