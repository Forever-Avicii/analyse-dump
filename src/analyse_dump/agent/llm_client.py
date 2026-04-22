from __future__ import annotations

import json
import os
from typing import Any, Dict, Optional

class LLMClient:
    def __init__(self, api_key: Optional[str] = None, base_url: Optional[str] = None) -> None:
        self.api_key = api_key or os.getenv("OPENAI_API_KEY")
        self.base_url = base_url

    def generate_json(self, model: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        # 先做 stub，后续接真实 API
        # 现在返回一个保守策略，保证联调先跑通
        _ = (model, payload, self.api_key, self.base_url)
        return {
            "tool_name": "analyze_chain",
            "args": {},
            "reason": "stub llm chooses analyze_chain first",
            "confidence": "medium",
        }
