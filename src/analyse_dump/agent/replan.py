from __future__ import annotations

from typing import List, Tuple

from .planner import PlanStep
from .types import ToolResult


def maybe_replan(
    *,
    lang: str,
    current_tool: str,
    result: ToolResult,
    remaining: List[PlanStep],
) -> Tuple[List[PlanStep], str | None]:
    # 1) Tool failure fallback.
    if not result.ok:
        if current_tool != "find_root_path":
            return [PlanStep("find_root_path", "Fallback after tool failure.")], "tool_failure_fallback"
        return [], None

    # 2) Inconclusive chain => force root path next.
    if current_tool == "analyze_chain":
        verdict = str(result.data.get("verdict", "inconclusive"))
        if verdict == "inconclusive" and not _has_tool(remaining, "find_root_path"):
            return [PlanStep("find_root_path", "Replan after inconclusive chain verdict.")], "chain_inconclusive"
        return [], None

    # 3) Root path miss on JS => inspect props immediately.
    if current_tool == "find_root_path":
        found = bool(result.data.get("found"))
        if not found and lang == "js" and not _has_tool(remaining, "inspect_js_props"):
            return [PlanStep("inspect_js_props", "Replan after root path miss on JS.")], "root_path_miss"
        return [], None

    return [], None


def _has_tool(steps: List[PlanStep], name: str) -> bool:
    for item in steps:
        if item.tool_name == name:
            return True
    return False

