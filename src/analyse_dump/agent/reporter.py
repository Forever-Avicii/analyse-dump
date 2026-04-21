from __future__ import annotations

import json
from typing import Any, Dict, List

from .state import AgentState


def state_to_dict(state: AgentState) -> Dict[str, Any]:
    steps: List[Dict[str, Any]] = []
    for item in state.steps:
        steps.append(
            {
                "step": item.step,
                "tool_name": item.tool_name,
                "args": item.args,
                "note": item.note,
                "result": {
                    "ok": item.result.ok,
                    "data": item.result.data,
                    "error": (
                        {
                            "code": item.result.error.code,
                            "message": item.result.error.message,
                            "retryable": item.result.error.retryable,
                        }
                        if item.result.error is not None
                        else None
                    ),
                    "metrics": item.result.metrics,
                },
            }
        )

    return {
        "goal": state.goal,
        "plan": state.plan,
        "plan_cursor": state.plan_cursor,
        "replan_count": state.replan_count,
        "replan_notes": state.replan_notes,
        "dedup_skips": state.dedup_skips,
        "concluded": state.concluded,
        "conclusion_status": state.conclusion_status,
        "summary": state.summary,
        "confidence": state.confidence,
        "evidence": state.evidence,
        "last_error": state.last_error,
        "step_count": len(state.steps),
        "steps": steps,
    }


def state_to_json_text(state: AgentState) -> str:
    return json.dumps(state_to_dict(state), ensure_ascii=False, indent=2, default=str)


def state_to_text(state: AgentState) -> str:
    lines = [
        f"summary={state.summary}",
        f"status={state.conclusion_status}",
        f"confidence={state.confidence}",
        f"steps={len(state.steps)}",
    ]
    if state.evidence:
        lines.append("evidence:")
        for i, e in enumerate(state.evidence, start=1):
            lines.append(f"{i}. {e}")
    if state.last_error:
        lines.append(f"last_error={state.last_error}")
    return "\n".join(lines)
