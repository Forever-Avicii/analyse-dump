from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Mapping


@dataclass(frozen=True)
class ActionDecision:
    tool_name: str
    args: Dict[str, Any]
    reason: str
    confidence: str = "medium"
    policy: str = "rule"

@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    handler: Callable[[Mapping[str, Any]], Dict[str, Any]]


@dataclass(frozen=True)
class ToolError:
    code: str
    message: str
    retryable: bool = False


@dataclass(frozen=True)
class ToolResult:
    ok: bool
    data: Dict[str, Any] = field(default_factory=dict)
    error: ToolError | None = None
    metrics: Dict[str, Any] = field(default_factory=dict)

