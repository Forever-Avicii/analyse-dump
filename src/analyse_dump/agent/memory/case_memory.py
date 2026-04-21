from __future__ import annotations

from pathlib import Path
from typing import Optional

from analyse_dump import db
from analyse_dump.agent.state import AgentState


def persist_case(db_path: Path, state: AgentState) -> Optional[int]:
    conn = db.connect(db_path)
    try:
        db.init_schema(conn)
        cursor = conn.execute(
            """
            INSERT INTO agent_cases(
                goal, summary, conclusion_status, confidence,
                step_count, replan_count, dedup_skips, db_path
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                state.goal,
                state.summary,
                state.conclusion_status,
                state.confidence,
                len(state.steps),
                state.replan_count,
                state.dedup_skips,
                str(db_path),
            ),
        )
        if cursor.lastrowid is None:
            conn.rollback()
            return None
        case_id = int(cursor.lastrowid)
        if state.evidence:
            conn.executemany(
                """
                INSERT INTO agent_case_evidence(case_id, evidence)
                VALUES (?, ?)
                """,
                [(case_id, item) for item in state.evidence],
            )
        conn.commit()
        return case_id
    finally:
        conn.close()
