from __future__ import annotations

import time
from typing import Any, Dict, Mapping

from .types import ToolError, ToolResult, ToolSpec


class ToolExecutor:
    def __init__(self, tools: Mapping[str, ToolSpec]) -> None:
        self._tools = dict(tools)

    def execute(self, name: str, args: Mapping[str, Any]) -> ToolResult:
        started = time.perf_counter()
        spec = self._tools.get(name)
        if spec is None:
            return ToolResult(
                ok=False,
                error=ToolError(
                    code="tool_not_found",
                    message=f"unknown tool: {name}",
                    retryable=False,
                ),
                metrics={"tool_name": name, "duration_ms": self._duration_ms(started)},
            )

        try:
            data = spec.handler(args)
            return ToolResult(
                ok=True,
                data=data,
                metrics={"tool_name": name, "duration_ms": self._duration_ms(started)},
            )
        except ValueError as exc:
            return ToolResult(
                ok=False,
                error=ToolError(code="invalid_args", message=str(exc), retryable=False),
                metrics={"tool_name": name, "duration_ms": self._duration_ms(started)},
            )
        except Exception as exc:  # pragma: no cover - defensive fallback
            return ToolResult(
                ok=False,
                error=ToolError(code="tool_failure", message=str(exc), retryable=True),
                metrics={"tool_name": name, "duration_ms": self._duration_ms(started)},
            )

    @staticmethod
    def _duration_ms(started: float) -> int:
        return int((time.perf_counter() - started) * 1000)

