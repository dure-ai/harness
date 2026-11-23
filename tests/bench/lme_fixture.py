"""Synthetic LongMemEval-shaped records for tests; no real dataset content."""

import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

Session = tuple[str, Sequence[tuple[str, str]]]  # (date text, [(role, content)])


def lme_question(
    qid: str,
    qtype: str,
    sessions: Sequence[Session],
    answer_idx: Sequence[int],
    question_date: str,
    has_answer: Mapping[int, Sequence[int]] | None = None,
) -> dict[str, Any]:
    """Build one record.

    `answer_idx` are the indices into `sessions` that hold the evidence; `has_answer` maps a
    session index to the turn indices flagged as evidence (default: turn 0 of each answer session).
    """
    marks = {i: [0] for i in answer_idx} if has_answer is None else {i: list(v) for i, v in has_answer.items()}
    haystack = []
    for si, (_, turns) in enumerate(sessions):
        rows: list[dict[str, Any]] = []
        for ti, (role, text) in enumerate(turns):
            row: dict[str, Any] = {"role": role, "content": text}
            if si in answer_idx:
                row["has_answer"] = ti in marks.get(si, [])
            rows.append(row)
        haystack.append(rows)
    return {
        "question_id": qid,
        "question_type": qtype,
        "question": f"question of {qid}?",
        "answer": "synthetic answer",
        "question_date": question_date,
        "haystack_session_ids": [f"sess-{qid}-{i}" for i in range(len(sessions))],
        "haystack_dates": [d for d, _ in sessions],
        "haystack_sessions": haystack,
        "answer_session_ids": [f"sess-{qid}-{i}" for i in answer_idx],
    }


def write_lme(path: Path, questions: Sequence[dict[str, Any]]) -> Path:
    """Write `questions` as a LongMemEval JSON list."""
    path.write_text(json.dumps(list(questions)), encoding="utf-8")
    return path
