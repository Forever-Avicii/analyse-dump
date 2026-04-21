from __future__ import annotations

from .state import AgentState


def should_stop(state: AgentState, max_steps: int) -> bool:
    if state.concluded:
        return True
    return len(state.steps) >= max_steps

