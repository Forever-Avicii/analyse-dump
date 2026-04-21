from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Mapping

from analyse_dump.bridge_queries import inspect_js_props, search_kt_by_value
from analyse_dump.chain_analyzer import analyze_chain
from analyse_dump.root_path_finder import find_root_path

from .types import ToolSpec


def _required_str(args: Mapping[str, Any], key: str) -> str:
    value = args.get(key)
    if value is None:
        raise ValueError(f"missing required argument: {key}")
    text = str(value).strip()
    if not text:
        raise ValueError(f"empty required argument: {key}")
    return text


def _db_path(args: Mapping[str, Any]) -> Path:
    return Path(_required_str(args, "db"))


def tool_find_root_path(args: Mapping[str, Any]) -> Dict[str, Any]:
    return find_root_path(
        db_path=_db_path(args),
        addr=_required_str(args, "addr"),
        lang=(str(args["lang"]) if args.get("lang") is not None else None),
        js_snapshot_id=(int(args["js_snapshot_id"]) if args.get("js_snapshot_id") is not None else None),
        kt_snapshot_id=(int(args["kt_snapshot_id"]) if args.get("kt_snapshot_id") is not None else None),
        max_depth=int(args.get("max_depth", 16)),
        max_fanout=int(args.get("max_fanout", 512)),
        include_weak=bool(args.get("include_weak", False)),
        include_shortcut=bool(args.get("include_shortcut", False)),
        js_root_types_csv=(str(args["js_root_types"]) if args.get("js_root_types") is not None else None),
        kt_root_types_csv=(str(args["kt_root_types"]) if args.get("kt_root_types") is not None else None),
        use_cache=bool(args.get("use_cache", True)),
        cache_profile=str(args.get("cache_profile", "default")),
        roots_mode=str(args.get("roots_mode", "native")),
    )


def tool_analyze_chain(args: Mapping[str, Any]) -> Dict[str, Any]:
    return analyze_chain(
        db_path=_db_path(args),
        addr=_required_str(args, "addr"),
        lang=_required_str(args, "lang"),
        js_snapshot_id=(int(args["js_snapshot_id"]) if args.get("js_snapshot_id") is not None else None),
        kt_snapshot_id=(int(args["kt_snapshot_id"]) if args.get("kt_snapshot_id") is not None else None),
        max_steps=int(args.get("max_steps", 8)),
        max_depth=int(args.get("max_depth", 16)),
        max_fanout=int(args.get("max_fanout", 512)),
        include_weak=bool(args.get("include_weak", False)),
        include_shortcut=bool(args.get("include_shortcut", False)),
        roots_mode=str(args.get("roots_mode", "native")),
        js_root_types_csv=(str(args["js_root_types"]) if args.get("js_root_types") is not None else None),
        kt_root_types_csv=(str(args["kt_root_types"]) if args.get("kt_root_types") is not None else None),
        js_napi_prop=str(args.get("js_napi_prop", "knapi_refs_test")),
        kt_napi_field=str(args.get("kt_napi_field", "ref")),
        js_deprioritize_keywords_csv=str(args.get("js_deprioritize_keywords", "global,synthetic,handle,native")),
        js_cache_profile=(str(args["js_cache_profile"]) if args.get("js_cache_profile") is not None else None),
        kt_cache_profile=(str(args["kt_cache_profile"]) if args.get("kt_cache_profile") is not None else None),
        max_branch_candidates=int(args.get("max_branch_candidates", 4)),
        top_k=int(args.get("top_k", 1)),
    )


def tool_inspect_js_props(args: Mapping[str, Any]) -> Dict[str, Any]:
    return inspect_js_props(
        db_path=_db_path(args),
        addr=_required_str(args, "addr"),
        js_snapshot_id=(int(args["js_snapshot_id"]) if args.get("js_snapshot_id") is not None else None),
        max_props=int(args.get("max_props", 256)),
        max_array_elems=int(args.get("max_array_elems", 128)),
    )


def tool_search_kt_by_value(args: Mapping[str, Any]) -> Dict[str, Any]:
    return search_kt_by_value(
        db_path=_db_path(args),
        value=_required_str(args, "value"),
        kt_snapshot_id=(int(args["kt_snapshot_id"]) if args.get("kt_snapshot_id") is not None else None),
        limit=int(args.get("limit", 500)),
    )


def default_tool_specs() -> Dict[str, ToolSpec]:
    tools = [
        ToolSpec(
            name="find_root_path",
            description="Find shortest holder path from object to language root.",
            handler=tool_find_root_path,
        ),
        ToolSpec(
            name="analyze_chain",
            description="Analyze cross-language retention chain and bridge hops.",
            handler=tool_analyze_chain,
        ),
        ToolSpec(
            name="inspect_js_props",
            description="Inspect JS properties and array values for object.",
            handler=tool_inspect_js_props,
        ),
        ToolSpec(
            name="search_kt_by_value",
            description="Search Kotlin objects by field value.",
            handler=tool_search_kt_by_value,
        ),
    ]
    return {item.name: item for item in tools}

