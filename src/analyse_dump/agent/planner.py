from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List


@dataclass(frozen=True)
class PlanStep:
    tool_name: str
    reason: str


def create_plan(goal: str, context: Dict[str, object], lang: str) -> List[PlanStep]:
    goal_norm = goal.lower()

    # Prioritize direct root-path request by intent.
    if "root path" in goal_norm or "path to root" in goal_norm:
        first = PlanStep("find_root_path", "Goal explicitly asks for root path.")
    else:
        first = PlanStep("analyze_chain", "Start with cross-language chain diagnosis.")

    plan: List[PlanStep] = [first]
    if first.tool_name != "analyze_chain":
        plan.append(PlanStep("analyze_chain", "Chain diagnosis is still needed for verdict context."))
    if first.tool_name != "find_root_path":
        plan.append(PlanStep("find_root_path", "Fallback to direct holder-path evidence."))

    if lang == "js":
        plan.append(PlanStep("inspect_js_props", "Gather JS property evidence for bridge candidates."))
    else:
        # Only useful when caller already has explicit value; otherwise tool is likely low-signal.
        if context.get("value") is not None:
            plan.append(PlanStep("search_kt_by_value", "Search Kotlin holders by provided ref value."))
    return plan

