from __future__ import annotations

import argparse
import json
from pathlib import Path

from analyse_dump import db
from analyse_dump.agent_analyzer import run_agent_analysis
from analyse_dump.chain_analyzer import analyze_chain
from analyse_dump.bridge_queries import inspect_js_props, search_kt_by_value
from analyse_dump.importers.heapsnapshot_importer import import_heapsnapshot
from analyse_dump.importers.hprof_importer import import_hprof
from analyse_dump.linker import link_with_config
from analyse_dump.root_distance import build_root_distance, make_cache_profile
from analyse_dump.root_path_finder import find_root_path
from analyse_dump.roots_builder import build_roots


def _format_addr(lang: str, addr: int) -> str:
    if lang == "js":
        return str(int(addr))
    return f"0x{int(addr):x}"


def _render_chain_narrative(result: dict) -> str:
    lines = []
    verdict = str(result.get("verdict", "inconclusive"))
    lines.append(f"Analysis verdict: {verdict}.")
    if "reason" in result:
        lines.append(f"Reason: {result['reason']}.")

    segments = result.get("segments", [])
    bridges = result.get("bridges", [])
    lines.append(f"Segments analyzed: {len(segments)}. Cross-language bridges: {len(bridges)}.")

    for seg in segments:
        root_result = seg.get("root_result", {})
        start_lang = str(seg.get("start_lang", "unknown"))
        start_addr = int(seg.get("start_addr", 0))
        if root_result.get("found"):
            root = root_result.get("root")
            if root is not None:
                root_lang = "js" if int(root.lang) == 0 else "kotlin"  # type: ignore[attr-defined]
                root_addr = int(root.addr)  # type: ignore[attr-defined]
                lines.append(
                    f"Step {seg.get('step')}: {start_lang}:{_format_addr(start_lang, start_addr)} "
                    f"reaches root {root_lang}:{_format_addr(root_lang, root_addr)}."
                )
        else:
            lines.append(
                f"Step {seg.get('step')}: {start_lang}:{_format_addr(start_lang, start_addr)} "
                f"failed to reach root ({root_result.get('reason', 'unknown')})."
            )

    for b in bridges:
        from_lang = str(b.get("from_lang", "unknown"))
        to_lang = str(b.get("to_lang", "unknown"))
        lines.append(
            f"Bridge {b.get('step')}: {from_lang}:{_format_addr(from_lang, int(b.get('from_addr', 0)))} "
            f"-> {to_lang}:{_format_addr(to_lang, int(b.get('to_addr', 0)))} via "
            f"{b.get('ref_kind')}@0x{int(b.get('ref_addr', 0)):x} ({b.get('kind')})."
        )

    return "\n".join(lines)


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

    p_roots = sub.add_parser("build-roots", help="materialize roots table from current snapshot semantics")
    p_roots.add_argument("--db", required=True, type=Path, help="SQLite db path")
    p_roots.add_argument("--js-snapshot-id", type=int, default=None, help="explicit JS snapshot id")
    p_roots.add_argument("--kt-snapshot-id", type=int, default=None, help="explicit Kotlin snapshot id")
    p_roots.add_argument("--roots-mode", type=str, default="mixed", help="native, heuristic, or mixed")

    p_dist = sub.add_parser("build-root-distance", help="precompute distance-to-root cache by language")
    p_dist.add_argument("--db", required=True, type=Path, help="SQLite db path")
    p_dist.add_argument("--lang", required=True, type=str, help="js or kotlin")
    p_dist.add_argument("--snapshot-id", type=int, default=None, help="explicit snapshot id")
    p_dist.add_argument("--include-weak", action="store_true", help="include weak edges while building cache")
    p_dist.add_argument("--max-fanout", type=int, default=20000, help="max outgoing edges expanded per node")
    p_dist.add_argument("--batch-size", type=int, default=20000, help="insert batch size")
    p_dist.add_argument("--js-root-types", type=str, default=None, help="comma-separated JS root type names")
    p_dist.add_argument("--kt-root-types", type=str, default=None, help="comma-separated Kotlin root type names")
    p_dist.add_argument("--profile", type=str, default=None, help="optional cache profile override")
    p_dist.add_argument("--roots-mode", type=str, default="mixed", help="native, heuristic, or mixed")

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
    p_path.add_argument("--no-cache", action="store_true", help="disable root_distance cache and force BFS")
    p_path.add_argument("--cache-profile", type=str, default=None, help="optional cache profile override")
    p_path.add_argument("--roots-mode", type=str, default="mixed", help="native, heuristic, or mixed")

    p_jsprops = sub.add_parser("inspect-js-props", help="inspect JS object properties and array values")
    p_jsprops.add_argument("--db", required=True, type=Path, help="SQLite db path")
    p_jsprops.add_argument("--addr", required=True, type=str, help="JS object address/id")
    p_jsprops.add_argument("--js-snapshot-id", type=int, default=None, help="explicit JS snapshot id")
    p_jsprops.add_argument("--max-props", type=int, default=256, help="max properties to output")
    p_jsprops.add_argument("--max-array-elems", type=int, default=128, help="max array elements per property")

    p_ktsearch = sub.add_parser("search-kt-by-value", help="search Kotlin holders by field value")
    p_ktsearch.add_argument("--db", required=True, type=Path, help="SQLite db path")
    p_ktsearch.add_argument("--value", required=True, type=str, help="value to search (hex/dec)")
    p_ktsearch.add_argument("--kt-snapshot-id", type=int, default=None, help="explicit Kotlin snapshot id")
    p_ktsearch.add_argument("--limit", type=int, default=500, help="max matches")

    p_chain = sub.add_parser("analyze-chain", help="orchestrate cross-language retention chain analysis")
    p_chain.add_argument("--db", required=True, type=Path, help="SQLite db path")
    p_chain.add_argument("--addr", required=True, type=str, help="start object address/id (hex/dec)")
    p_chain.add_argument("--lang", required=True, type=str, help="start language: js or kotlin")
    p_chain.add_argument("--js-snapshot-id", type=int, default=None, help="explicit JS snapshot id")
    p_chain.add_argument("--kt-snapshot-id", type=int, default=None, help="explicit Kotlin snapshot id")
    p_chain.add_argument("--max-steps", type=int, default=8, help="max cross-language hops")
    p_chain.add_argument("--max-depth", type=int, default=16, help="max reverse depth per language segment")
    p_chain.add_argument("--max-fanout", type=int, default=512, help="max incoming edges expanded per node")
    p_chain.add_argument("--include-weak", action="store_true", help="include weak edges in root-path search")
    p_chain.add_argument("--js-root-types", type=str, default=None, help="comma-separated JS root type names")
    p_chain.add_argument("--kt-root-types", type=str, default=None, help="comma-separated Kotlin root type names")
    p_chain.add_argument(
        "--js-napi-prop",
        type=str,
        default="knapi_refs_test",
        help="JS property name carrying napi-ref address array",
    )
    p_chain.add_argument(
        "--kt-napi-field",
        type=str,
        default="ref",
        help="Kotlin field name expected to carry napi_ref value",
    )
    p_chain.add_argument(
        "--js-deprioritize-keywords",
        type=str,
        default="global,synthetic,handle,native",
        help="comma-separated keywords to deprioritize JS nodes while selecting bridge anchors",
    )
    p_chain.add_argument("--js-cache-profile", type=str, default=None, help="optional JS cache profile override")
    p_chain.add_argument("--kt-cache-profile", type=str, default=None, help="optional Kotlin cache profile override")
    p_chain.add_argument("--max-branch-candidates", type=int, default=4, help="max bridge candidates explored per step")
    p_chain.add_argument("--json", action="store_true", help="output full analyze-chain result as JSON")
    p_chain.add_argument("--narrative", action="store_true", help="print narrative summary after structured output")
    p_chain.add_argument("--roots-mode", type=str, default="mixed", help="native, heuristic, or mixed")

    p_agent = sub.add_parser("analyze-chain-agent", help="agent-style diagnosis on top of analyze-chain output")
    p_agent.add_argument("--db", required=True, type=Path, help="SQLite db path")
    p_agent.add_argument("--addr", required=True, type=str, help="start object address/id (hex/dec)")
    p_agent.add_argument("--lang", required=True, type=str, help="start language: js or kotlin")
    p_agent.add_argument("--js-snapshot-id", type=int, default=None, help="explicit JS snapshot id")
    p_agent.add_argument("--kt-snapshot-id", type=int, default=None, help="explicit Kotlin snapshot id")
    p_agent.add_argument("--max-steps", type=int, default=8, help="max cross-language hops")
    p_agent.add_argument("--max-depth", type=int, default=16, help="max reverse depth per language segment")
    p_agent.add_argument("--max-fanout", type=int, default=512, help="max incoming edges expanded per node")
    p_agent.add_argument("--include-weak", action="store_true", help="include weak edges in root-path search")
    p_agent.add_argument("--js-root-types", type=str, default=None, help="comma-separated JS root type names")
    p_agent.add_argument("--kt-root-types", type=str, default=None, help="comma-separated Kotlin root type names")
    p_agent.add_argument("--js-napi-prop", type=str, default="knapi_refs_test", help="JS napi ref property name")
    p_agent.add_argument("--kt-napi-field", type=str, default="ref", help="Kotlin napi ref field name")
    p_agent.add_argument("--js-cache-profile", type=str, default=None, help="optional JS cache profile override")
    p_agent.add_argument("--kt-cache-profile", type=str, default=None, help="optional Kotlin cache profile override")
    p_agent.add_argument("--max-branch-candidates", type=int, default=4, help="max bridge candidates explored per step")
    p_agent.add_argument("--json", action="store_true", help="output agent analysis as JSON")
    p_agent.add_argument("--roots-mode", type=str, default="mixed", help="native, heuristic, or mixed")

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

    if args.command == "build-roots":
        result = build_roots(
            db_path=args.db,
            js_snapshot_id=args.js_snapshot_id,
            kt_snapshot_id=args.kt_snapshot_id,
            roots_mode=args.roots_mode,
        )
        print(
            f"built=true roots_mode={result['roots_mode']} "
            f"js_snapshot_id={result['js_snapshot_id']} js_roots={result['js_roots']} "
            f"kt_snapshot_id={result['kt_snapshot_id']} kt_roots={result['kt_roots']}"
        )
        return

    if args.command == "build-root-distance":
        result = build_root_distance(
            db_path=args.db,
            lang=args.lang,
            profile=make_cache_profile(
                lang=args.lang,
                include_weak=args.include_weak,
                root_types_csv=(args.js_root_types if args.lang.strip().lower() == "js" else args.kt_root_types),
                roots_mode=args.roots_mode,
                profile_override=args.profile,
            ),
            roots_mode=args.roots_mode,
            snapshot_id=args.snapshot_id,
            include_weak=args.include_weak,
            max_fanout=args.max_fanout,
            js_root_types_csv=args.js_root_types,
            kt_root_types_csv=args.kt_root_types,
            batch_size=args.batch_size,
        )
        lang_name = "js" if int(result["lang"]) == 0 else "kotlin"
        print(
            f"built=true lang={lang_name} snapshot_id={result['snapshot_id']} profile={result['profile']} "
            f"roots={result['roots']} nodes={result['nodes']}"
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
            use_cache=not args.no_cache,
            cache_profile=make_cache_profile(
                lang=(args.lang or "js"),
                include_weak=args.include_weak,
                root_types_csv=(args.js_root_types if (args.lang or "").strip().lower() == "js" else args.kt_root_types),
                roots_mode=args.roots_mode,
                profile_override=args.cache_profile,
            ),
            roots_mode=args.roots_mode,
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

    if args.command == "inspect-js-props":
        result = inspect_js_props(
            db_path=args.db,
            addr=args.addr,
            js_snapshot_id=args.js_snapshot_id,
            max_props=args.max_props,
            max_array_elems=args.max_array_elems,
        )
        if not result["found"]:
            print(f"found=false addr={args.addr} reason={result['reason']}")
            return

        print(
            f"found=true snapshot_id={result['snapshot_id']} addr={result['addr']} "
            f"type={result['type_name']} name={result['name']}"
        )
        props = result["properties"]
        print(f"properties={len(props)}")
        for i, p in enumerate(props, start=1):
            print(
                f"{i}. {p['property']} -> addr={p['target_addr']} type={p['target_type']} "
                f"name={p['target_name']}"
            )
            arr = p.get("array_values", [])
            if arr:
                print(f"   array_values={len(arr)}")
                for j, elem in enumerate(arr, start=1):
                    print(
                        f"   {j}) elem_addr={elem['elem_addr']} type={elem['elem_type']} "
                        f"name={elem['elem_name']} parsed={elem['parsed_hex']}"
                    )
        return

    if args.command == "search-kt-by-value":
        result = search_kt_by_value(
            db_path=args.db,
            value=args.value,
            kt_snapshot_id=args.kt_snapshot_id,
            limit=args.limit,
        )
        matches = result["matches"]
        print(
            f"snapshot_id={result['snapshot_id']} query={result['query_value_hex']} "
            f"matches={len(matches)}"
        )
        for i, row in enumerate(matches, start=1):
            print(
                f"{i}. obj={row['obj_addr_hex']} type={row['type_name']} "
                f"field={row['field_name']} value_int={row['field_value_int']} "
                f"value_text={row['field_value_text']}"
            )
        return

    if args.command == "analyze-chain":
        result = analyze_chain(
            db_path=args.db,
            addr=args.addr,
            lang=args.lang,
            js_snapshot_id=args.js_snapshot_id,
            kt_snapshot_id=args.kt_snapshot_id,
            max_steps=args.max_steps,
            max_depth=args.max_depth,
            max_fanout=args.max_fanout,
            include_weak=args.include_weak,
            roots_mode=args.roots_mode,
            js_root_types_csv=args.js_root_types,
            kt_root_types_csv=args.kt_root_types,
            js_napi_prop=args.js_napi_prop,
            kt_napi_field=args.kt_napi_field,
            js_deprioritize_keywords_csv=args.js_deprioritize_keywords,
            js_cache_profile=args.js_cache_profile,
            kt_cache_profile=args.kt_cache_profile,
            max_branch_candidates=args.max_branch_candidates,
        )
        if args.json:
            print(json.dumps(result, default=str, ensure_ascii=False, indent=2))
            if args.narrative:
                print("---")
                print(_render_chain_narrative(result))
            return

        print(f"verdict={result['verdict']}")
        if "reason" in result:
            print(f"reason={result['reason']}")

        segments = result.get("segments", [])
        print(f"segments={len(segments)}")
        for seg in segments:
            root_result = seg["root_result"]
            start_lang = str(seg["start_lang"])
            print(
                f"segment#{seg['step']} start={start_lang}:{_format_addr(start_lang, int(seg['start_addr']))} "
                f"found={root_result.get('found')} reason={root_result.get('reason', '-')}"
            )
            if root_result.get("found"):
                root = root_result["root"]
                root_lang = "js" if root.lang == 0 else "kotlin"
                print(f"  root={root_lang}:{_format_addr(root_lang, int(root.addr))}")

        bridges = result.get("bridges", [])
        print(f"bridges={len(bridges)}")
        for b in bridges:
            from_lang = str(b["from_lang"])
            to_lang = str(b["to_lang"])
            anchor_lang = str(b.get("anchor_lang", from_lang))
            print(
                f"bridge#{b['step']} {from_lang}:{_format_addr(from_lang, int(b['from_addr']))} -> "
                f"{to_lang}:{_format_addr(to_lang, int(b['to_addr']))} "
                f"{b['ref_kind']}@0x{int(b['ref_addr']):x} "
                f"kind={b['kind']} "
                f"anchor={anchor_lang}:{_format_addr(anchor_lang, int(b.get('anchor_addr', b['from_addr'])))} "
                f"evidence={b.get('evidence', '-')}"
            )
        return

    if args.command == "analyze-chain-agent":
        result = run_agent_analysis(
            db_path=args.db,
            addr=args.addr,
            lang=args.lang,
            js_snapshot_id=args.js_snapshot_id,
            kt_snapshot_id=args.kt_snapshot_id,
            max_steps=args.max_steps,
            max_depth=args.max_depth,
            max_fanout=args.max_fanout,
            include_weak=args.include_weak,
            roots_mode=args.roots_mode,
            js_root_types_csv=args.js_root_types,
            kt_root_types_csv=args.kt_root_types,
            js_napi_prop=args.js_napi_prop,
            kt_napi_field=args.kt_napi_field,
            js_cache_profile=args.js_cache_profile,
            kt_cache_profile=args.kt_cache_profile,
            max_branch_candidates=args.max_branch_candidates,
        )
        if args.json:
            print(json.dumps(result, default=str, ensure_ascii=False, indent=2))
            return

        print(f"summary={result['summary']}")
        print(f"confidence={result['confidence']}")
        suspects = result.get("suspects", [])
        print(f"suspects={len(suspects)}")
        for i, s in enumerate(suspects, start=1):
            s_lang = str(s.get("lang", "unknown"))
            s_addr = int(s.get("addr", 0))
            print(
                f"{i}. {s_lang}:{_format_addr(s_lang, s_addr)} "
                f"type={s.get('type_name')} name={s.get('name')} reason={s.get('reason')}"
            )
        print("next_actions:")
        for i, action in enumerate(result.get("next_actions", []), start=1):
            print(f"{i}. {action}")
        return

    parser.error("Unknown command")


if __name__ == "__main__":
    main()
