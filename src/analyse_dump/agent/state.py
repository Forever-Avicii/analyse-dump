from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .types import ToolResult


@dataclass
class AgentStep:
    step: int
    tool_name: str
    args: Dict[str, Any]
    result: ToolResult
    note: str = ""


@dataclass
class AgentState:
    goal: str
    context: Dict[str, Any]
    steps: List[AgentStep] = field(default_factory=list)
    concluded: bool = False
    conclusion_status: str = "in_progress"
    summary: str = ""
    confidence: str = "low"
    evidence: List[str] = field(default_factory=list)
    plan: List[Dict[str, str]] = field(default_factory=list)
    plan_cursor: int = 0
    replan_count: int = 0
    replan_notes: List[str] = field(default_factory=list)
    dedup_skips: int = 0
    case_id: int | None = None
    case_memory_error: str | None = None
    last_error: Optional[str] = None

    def append_step(self, item: AgentStep) -> None:
        self.steps.append(item)
        if not item.result.ok and item.result.error is not None:
            self.last_error = f"{item.result.error.code}: {item.result.error.message}"
