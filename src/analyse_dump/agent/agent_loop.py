from __future__ import annotations

import re
from typing import Any, Dict, Optional, Tuple

from .planner import create_plan
from .replan import maybe_replan
from .state import AgentState, AgentStep
from .stop_policy import should_stop
from .tool_executor import ToolExecutor


def _extract_addr_lang(goal: str, context: Dict[str, Any]) -> Tuple[Optional[str], Optional[str]]:
    ctx_addr = context.get("addr")
    ctx_lang = context.get("lang")
    addr = str(ctx_addr) if ctx_addr is not None else None
    lang = str(ctx_lang).strip().lower() if ctx_lang is not None else None

    if addr is None:
        m = re.search(r"\b0x[0-9a-fA-F]+\b", goal)
        if m:
            addr = m.group(0)
    if addr is None:
        m = re.search(r"\b\d+\b", goal)
        if m:
            addr = m.group(0)

    if lang is None:
        g = goal.lower()
        if "kotlin" in g:
            lang = "kotlin"
        elif "js" in g or "javascript" in g:
            lang = "js"
    return addr, lang


def _common_args(context: Dict[str, Any], addr: str, lang: str) -> Dict[str, Any]:
    return {
        "db": context["db"],
        "addr": addr,
        "lang": lang,
        "js_snapshot_id": context.get("js_snapshot_id"),
        "kt_snapshot_id": context.get("kt_snapshot_id"),
        "roots_mode": context.get("roots_mode", "native"),
        "max_depth": context.get("max_depth", 12),
        "max_fanout": context.get("max_fanout", 512),
        "max_steps": context.get("max_chain_steps", 6),
        "top_k": context.get("top_k", 1),
    }


def run_agent(
    goal: str,
    context: Dict[str, Any],
    executor: ToolExecutor,
    max_steps: int = 6,
) -> AgentState:
    state = AgentState(goal=goal, context=dict(context))
    addr, lang = _extract_addr_lang(goal, context)
    if not addr or not lang:
        state.concluded = True
        state.conclusion_status = "inconclusive"
        state.summary = "Agent cannot infer required addr/lang from goal or context."
        state.confidence = "low"
        return state

    common = _common_args(context, addr, lang)
    plan_steps = create_plan(goal=goal, context=context, lang=lang)
    state.plan = [{"tool_name": s.tool_name, "reason": s.reason} for s in plan_steps]

    while not should_stop(state, max_steps=max_steps):
        if state.plan_cursor >= len(plan_steps):
            state.concluded = True
            state.conclusion_status = "inconclusive"
            state.summary = "Plan exhausted before reaching a confirmed conclusion."
            state.confidence = "low"
            break

        tool_cursor = plan_steps[state.plan_cursor].tool_name
        state.plan_cursor += 1
        if tool_cursor == "analyze_chain":
            args = dict(common)
        elif tool_cursor == "find_root_path":
            args = {
                **common,
                "use_cache": True,
                "cache_profile": context.get("cache_profile", "default"),
            }
        elif tool_cursor == "inspect_js_props":
            args = {
                "db": context["db"],
                "addr": addr,
                "js_snapshot_id": context.get("js_snapshot_id"),
                "max_props": context.get("max_props", 128),
                "max_array_elems": context.get("max_array_elems", 64),
            }
        elif tool_cursor == "search_kt_by_value":
            value = context.get("value")
            args = {
                "db": context["db"],
                "value": str(value if value is not None else "0x0"),
                "kt_snapshot_id": context.get("kt_snapshot_id"),
                "limit": context.get("limit", 200),
            }
        else:
            state.concluded = True
            state.conclusion_status = "inconclusive"
            state.summary = "Agent entered unknown tool path."
            break

        out = executor.execute(tool_cursor, args)
        step_id = len(state.steps) + 1
        state.append_step(
            AgentStep(
                step=step_id,
                tool_name=tool_cursor,
                args=args,
                result=out,
            )
        )
        if not out.ok:
            inserted, reason = maybe_replan(
                lang=lang,
                current_tool=tool_cursor,
                result=out,
                remaining=plan_steps[state.plan_cursor :],
            )
            if inserted:
                plan_steps[state.plan_cursor : state.plan_cursor] = inserted
                state.replan_count += 1
                state.replan_notes.append(str(reason))
                state.plan = [{"tool_name": s.tool_name, "reason": s.reason} for s in plan_steps]
                continue

            state.concluded = True
            state.conclusion_status = "failed"
            state.summary = f"Tool failure at step {step_id}: {state.last_error}"
            state.confidence = "low"
            break

        if tool_cursor == "analyze_chain":
            verdict = str(out.data.get("verdict", "inconclusive"))
            state.evidence.append(f"analyze_chain.verdict={verdict}")
            inserted, reason = maybe_replan(
                lang=lang,
                current_tool=tool_cursor,
                result=out,
                remaining=plan_steps[state.plan_cursor :],
            )
            if inserted:
                plan_steps[state.plan_cursor : state.plan_cursor] = inserted
                state.replan_count += 1
                state.replan_notes.append(str(reason))
                state.plan = [{"tool_name": s.tool_name, "reason": s.reason} for s in plan_steps]
            if verdict == "loop_detected":
                state.concluded = True
                state.conclusion_status = "confirmed"
                state.summary = "Cross-language retain loop detected."
                state.confidence = "high"
                break
            if verdict == "reached_terminal_root":
                state.concluded = True
                state.conclusion_status = "confirmed"
                state.summary = "No retain loop in explored branches; reached terminal root."
                state.confidence = "medium"
                break
            continue

        if tool_cursor == "find_root_path":
            found = bool(out.data.get("found"))
            state.evidence.append(f"find_root_path.found={found}")
            inserted, reason = maybe_replan(
                lang=lang,
                current_tool=tool_cursor,
                result=out,
                remaining=plan_steps[state.plan_cursor :],
            )
            if inserted:
                plan_steps[state.plan_cursor : state.plan_cursor] = inserted
                state.replan_count += 1
                state.replan_notes.append(str(reason))
                state.plan = [{"tool_name": s.tool_name, "reason": s.reason} for s in plan_steps]
            if found:
                state.concluded = True
                state.conclusion_status = "confirmed"
                state.summary = "Found holder path to root."
                state.confidence = "medium"
                break
            continue

        if tool_cursor == "inspect_js_props":
            found = bool(out.data.get("found"))
            state.evidence.append(f"inspect_js_props.found={found}")
            state.concluded = True
            if found:
                state.conclusion_status = "hypothesis"
                state.summary = "Collected JS property evidence; manual cross-language check recommended."
                state.confidence = "medium"
            else:
                state.conclusion_status = "inconclusive"
                state.summary = "JS fallback evidence not found."
                state.confidence = "low"
            break

        if tool_cursor == "search_kt_by_value":
            matches = out.data.get("matches", [])
            count = len(matches) if isinstance(matches, list) else 0
            state.evidence.append(f"search_kt_by_value.matches={count}")
            state.concluded = True
            if count > 0:
                state.conclusion_status = "hypothesis"
                state.summary = "Found Kotlin holder candidates by value search."
                state.confidence = "medium"
            else:
                state.conclusion_status = "inconclusive"
                state.summary = "No Kotlin holder candidates found by value search."
                state.confidence = "low"
            break

    if not state.concluded and len(state.steps) >= max_steps:
        state.concluded = True
        state.conclusion_status = "inconclusive"
        state.summary = "Stopped due to step budget limit."
        state.confidence = "low"
    return state
