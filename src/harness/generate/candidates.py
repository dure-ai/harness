"""Fact candidates: ask the model for reusable repository knowledge in a trace and check its quotes."""

import re
from importlib import resources
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from harness.generate.config import GenConfig
from harness.generate.llm import LLMClient
from harness.generate.traces import Trace, compress, message_text
from harness.text import jaccard

_DEDUPE_THRESHOLD = 0.8
_QUOTE_CHARS = 40
_MIN_QUOTE_CHARS = 8
_MIN_QUOTE_WORDS = 2
_SPACES = re.compile(r"\s+")

Kind = Literal[
    "build", "test", "config", "convention", "workaround", "ownership", "other"
]


class RawCandidate(BaseModel):
    """A fact candidate as the model writes it.

    Attributes:
        statement: One self-contained sentence stating the fact.
        kind: Category of the fact.
        quote: Exact excerpt of the supporting message.
        message_index: Original number of the message holding the quote.
        condition: Setting in which the fact holds, if it is not general.
        fix: For workaround-like facts, whether the cause was fixed or only worked around.
    """

    model_config = ConfigDict(frozen=True)

    statement: str
    kind: Kind
    quote: str
    message_index: int
    condition: dict[str, str] = {}
    fix: Literal["root", "workaround"] | None = None


class FactCandidate(RawCandidate):
    """A fact candidate of one trace.

    Attributes:
        index: Position after the cap, set in code by `extract_candidates`; never asked of the model.
    """

    index: int = 0


class CandidateList(BaseModel):
    """Reply schema of the `candidates` request.

    Attributes:
        candidates: Candidates in the order the model ranked them.
    """

    candidates: list[RawCandidate]


class Rejection(BaseModel):
    """One line of the rejection log.

    Attributes:
        stage: Pipeline stage that rejected the item.
        code: Short machine-readable reason.
        message: One-line explanation.
        trace_id: Trajectory the item came from, if any.
        family: Scenario family being generated, if known.
        draft: Path of the rejected draft kept for diagnosis, if one was kept.
    """

    stage: str
    code: str
    message: str
    trace_id: str | None = None
    family: str | None = None
    draft: str | None = None


def load_prompt(name: str) -> str:
    """Read a prompt file shipped with the package.

    Args:
        name: File name under `harness/generate/prompts`.

    Returns:
        The prompt text.
    """
    return (resources.files("harness.generate") / "prompts" / name).read_text(
        encoding="utf-8"
    )


def extract_candidates(
    client: LLMClient, trace: Trace, cfg: GenConfig
) -> list[FactCandidate]:
    """Ask the model for fact candidates in one trace.

    Args:
        client: LLM client.
        trace: Trajectory to read; it is compressed before being sent.
        cfg: Generation config; `max_candidates_per_trace` caps the result.

    Returns:
        At most `max_candidates_per_trace` candidates in the model's order, numbered from 0.

    Raises:
        LLMError: If the model call fails.
    """
    system = load_prompt("candidates.md").replace(
        "{max_candidates}", str(cfg.max_candidates_per_trace)
    )
    user = f"Repository: {trace.repo}\n\n{compress(trace)}"
    reply = client.complete_json("candidates", system, user, CandidateList)
    kept = reply.candidates[: cfg.max_candidates_per_trace]
    return [FactCandidate(**c.model_dump(), index=i) for i, c in enumerate(kept)]


def _fold(text: str) -> str:
    return _SPACES.sub(" ", text).strip()


def check_quotes(
    trace: Trace, cands: list[FactCandidate], min_chars: int = _MIN_QUOTE_CHARS
) -> tuple[list[FactCandidate], list[Rejection]]:
    """Keep the candidates whose quote really appears in the message they cite and is long enough.

    Whitespace runs are folded to one space on both sides before comparing. A quote found in
    the message must also have at least `min_chars` non-space characters and two words, so a
    fragment like "e" (found in almost any message) is not taken as evidence.

    Args:
        trace: Trajectory the candidates came from.
        cands: Candidates to check.
        min_chars: Least number of non-space characters (`GenConfig.min_quote_chars`).

    Returns:
        The passing candidates in order, and a rejection for each of the others.
    """
    known = {m.index for m in trace.messages}
    ok: list[FactCandidate] = []
    rejected: list[Rejection] = []
    for c in cands:
        if c.message_index not in known:
            rejected.append(
                Rejection(
                    stage="candidates",
                    code="message_out_of_range",
                    message=f"candidate {c.index} cites message #{c.message_index}, which does not exist",
                    trace_id=trace.trajectory_id,
                )
            )
            continue
        quote = _fold(c.quote)
        if not quote or quote not in _fold(message_text(trace, c.message_index)):
            rejected.append(
                Rejection(
                    stage="candidates",
                    code="quote_not_found",
                    message=f"candidate {c.index} quote not found in message #{c.message_index}",
                    trace_id=trace.trajectory_id,
                )
            )
            continue
        if len(quote.replace(" ", "")) < min_chars or len(quote.split()) < _MIN_QUOTE_WORDS:
            rejected.append(
                Rejection(
                    stage="candidates",
                    code="quote_too_short",
                    message=(
                        f"candidate {c.index} quote {quote!r} is shorter than {min_chars} "
                        f"non-space characters or {_MIN_QUOTE_WORDS} words"
                    ),
                    trace_id=trace.trajectory_id,
                )
            )
            continue
        ok.append(c)
    return ok, rejected


def dedupe(cands: list[FactCandidate]) -> list[FactCandidate]:
    """Drop candidates whose statement nearly repeats an earlier one.

    Args:
        cands: Candidates in priority order.

    Returns:
        The candidates whose statement has token Jaccard below 0.8 against every kept one.
    """
    kept: list[FactCandidate] = []
    for c in cands:
        if all(jaccard(c.statement, k.statement) < _DEDUPE_THRESHOLD for k in kept):
            kept.append(c)
    return kept


_ASSISTANT = re.compile(
    r"\[#(\d+) assistant\]([^\n]*(?:\n(?!\n(?:\[#\d+ \w+\]|\[… ))[^\n]*)*)"
)

_FAKE_FACTS: list[dict[str, Any]] = [
    {
        "statement": "The test suite of this repository is run with the pinned project test command.",
        "kind": "test",
    },
    {
        "statement": "Flaky timeouts in the integration step are avoided with a retry rather than fixed at the cause.",
        "kind": "workaround",
        "fix": "workaround",
    },
    {
        "statement": "The api service must be configured before its client code can be exercised.",
        "kind": "config",
        "condition": {"service": "api"},
    },
]


def fake_candidates(system: str, user: str) -> dict[str, Any]:
    """Canned `candidates` reply for the fake provider, built from the compressed trace in `user`.

    Each candidate quotes the first 40 characters of an assistant message as rendered, so it
    passes `check_quotes`. With fewer than three assistant messages, messages are reused.

    Args:
        system: System prompt (unused).
        user: User prompt holding the compressed trace.

    Returns:
        `{"candidates": [...]}` with three candidates, or an empty list without assistant text.
    """
    msgs = []
    for m in _ASSISTANT.finditer(user):
        body = m.group(2).strip()
        if body:
            msgs.append((int(m.group(1)), body[:_QUOTE_CHARS]))
    if not msgs:
        return {"candidates": []}
    out = []
    for i, fact in enumerate(_FAKE_FACTS):
        index, quote = msgs[i % len(msgs)]
        out.append({"quote": quote, "message_index": index, "condition": {}, **fact})
    return {"candidates": out}
