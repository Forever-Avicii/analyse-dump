from __future__ import annotations

from .state import AgentState


def verify_state(state: AgentState) -> AgentState:
    if state.conclusion_status == "failed":
        state.verifier_note = "Verifier kept failed status due to tool errors."
        return state

    chain_verdict = None
    has_root_path = False
    has_js_evidence = False
    has_kt_value_hits = False

    for step in state.steps:
        if not step.result.ok:
            continue
        if step.tool_name == "analyze_chain":
            chain_verdict = str(step.result.data.get("verdict", "inconclusive"))
        elif step.tool_name == "find_root_path":
            has_root_path = bool(step.result.data.get("found"))
        elif step.tool_name == "inspect_js_props":
            has_js_evidence = bool(step.result.data.get("found"))
        elif step.tool_name == "search_kt_by_value":
            matches = step.result.data.get("matches", [])
            has_kt_value_hits = isinstance(matches, list) and len(matches) > 0

    if chain_verdict == "loop_detected":
        state.conclusion_status = "confirmed"
        state.confidence = "high"
        state.summary = "Cross-language retain loop detected and verified."
        state.verifier_note = "Verified by analyze_chain loop_detected verdict."
        return state

    if chain_verdict == "reached_terminal_root":
        state.conclusion_status = "confirmed"
        state.confidence = "medium"
        state.summary = "Terminal root reached and verified without loop."
        state.verifier_note = "Verified by analyze_chain reached_terminal_root verdict."
        return state

    if has_root_path:
        state.conclusion_status = "confirmed"
        state.confidence = "medium"
        state.summary = "Root holder path found and verified."
        state.verifier_note = "Verified by find_root_path positive result."
        return state

    if has_js_evidence or has_kt_value_hits:
        state.conclusion_status = "hypothesis"
        state.confidence = "medium"
        state.verifier_note = "Evidence exists but no terminal chain/root proof."
        return state

    state.conclusion_status = "inconclusive"
    state.confidence = "low"
    if not state.summary:
        state.summary = "No strong evidence found for confirmed diagnosis."
    state.verifier_note = "No strong evidence found for confirmed diagnosis."
    return state
