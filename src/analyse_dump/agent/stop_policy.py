from __future__ import annotations

import time

from .state import AgentState


def should_stop(state: AgentState, max_steps: int, start_ts: float, max_seconds: float | None) -> bool:
    if state.concluded:
        return True
    if len(state.steps) >= max_steps:
        state.concluded = True
        state.conclusion_status = "inconclusive"
        state.summary = "Stopped due to step budget limit."
        state.confidence = "low"
        return True
    if max_seconds is not None and max_seconds >= 0:
        elapsed = time.perf_counter() - start_ts
        if elapsed >= max_seconds:
            state.concluded = True
            state.conclusion_status = "inconclusive"
            state.summary = "Stopped due to time budget limit."
            state.confidence = "low"
            return True
    return False
