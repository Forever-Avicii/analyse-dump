from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Dict, Mapping, Optional

from .state import AgentState
from .types import ActionDecision, ToolSpec

@dataclass
class PolicyContext:
    db: str
    addr: str
    lang: str
    js_snapshot_id: Optional[int] = None
    kt_snapshot_id: Optional[int] = None
    roots_mode: str = "native"
    max_depth: int = 12
    max_fanout: int = 512
    max_chain_steps: int = 6


class BasePolicy:
    def next_action(
        self,
        state: AgentState,
        ctx: PolicyContext,
        tools: Mapping[str, ToolSpec],
    ) -> ActionDecision:
        raise NotImplementedError


class RulePolicy(BasePolicy):
    def next_action(self, state: AgentState, ctx: PolicyContext, tools: Mapping[str, ToolSpec]) -> ActionDecision:
        # 最小策略：首轮先 analyze_chain，之后 fallback find_root_path
        if len(state.steps) == 0:
            return ActionDecision(
                tool_name="analyze_chain",
                args={
                    "db": ctx.db,
                    "addr": ctx.addr,
                    "lang": ctx.lang,
                    "js_snapshot_id": ctx.js_snapshot_id,
                    "kt_snapshot_id": ctx.kt_snapshot_id,
                    "roots_mode": ctx.roots_mode,
                    "max_steps": ctx.max_chain_steps,
                    "max_depth": ctx.max_depth,
                    "max_fanout": ctx.max_fanout,
                },
                reason="Default first action: cross-language chain diagnosis.",
                confidence="medium",
                policy="rule",
            )

        return ActionDecision(
            tool_name="find_root_path",
            args={
                "db": ctx.db,
                "addr": ctx.addr,
                "lang": ctx.lang,
                "js_snapshot_id": ctx.js_snapshot_id,
                "kt_snapshot_id": ctx.kt_snapshot_id,
                "roots_mode": ctx.roots_mode,
                "max_depth": ctx.max_depth,
                "max_fanout": ctx.max_fanout,
                "use_cache": True,
            },
            reason="Fallback holder-path evidence.",
            confidence="medium",
            policy="rule",
        )


class LLMPolicy(BasePolicy):
    def __init__(self, client: "LLMClient", model: str) -> None:
        self.client = client
        self.model = model

    def next_action(self, state: AgentState, ctx: PolicyContext, tools: Mapping[str, ToolSpec]) -> ActionDecision:
        prompt = {
            "goal": state.goal,
            "step_count": len(state.steps),
            "last_error": state.last_error,
            "available_tools": {k: v.description for k, v in tools.items()},
            "context": {
                "db": ctx.db,
                "addr": ctx.addr,
                "lang": ctx.lang,
                "roots_mode": ctx.roots_mode,
                "max_depth": ctx.max_depth,
                "max_fanout": ctx.max_fanout,
                "max_chain_steps": ctx.max_chain_steps,
            },
            "output_schema": {
                "tool_name": "string",
                "args": "object",
                "reason": "string",
                "confidence": "low|medium|high"
            },
        }
        raw = self.client.generate_json(model=self.model, payload=prompt)
        tool_name = str(raw["tool_name"])
        if tool_name not in tools:
            raise ValueError(f"invalid tool_name from llm: {tool_name}")
        args = dict(raw.get("args", {}))
        args.setdefault("db", ctx.db)
        args.setdefault("addr", ctx.addr)
        args.setdefault("lang", ctx.lang)

        return ActionDecision(
            tool_name=tool_name,
            args=args,
            reason=str(raw.get("reason", "llm_decision")),
            confidence=str(raw.get("confidence", "medium")),
            policy="llm",
        )
