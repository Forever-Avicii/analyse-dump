from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Dict, Set


@dataclass
class SessionMemory:
    seen_calls: Set[str] = field(default_factory=set)
    dedup_skips: int = 0

    def has_seen(self, tool_name: str, args: Dict[str, Any]) -> bool:
        return self._fingerprint(tool_name, args) in self.seen_calls

    def record(self, tool_name: str, args: Dict[str, Any]) -> None:
        self.seen_calls.add(self._fingerprint(tool_name, args))

    @staticmethod
    def _fingerprint(tool_name: str, args: Dict[str, Any]) -> str:
        payload = {"tool_name": tool_name, "args": args}
        return json.dumps(payload, sort_keys=True, ensure_ascii=True, default=str)

