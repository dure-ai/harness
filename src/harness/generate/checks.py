"""Automatic checks on a generated draft: structure, discrimination, and answer-cue leaks."""

import re
from collections.abc import Sequence

from harness.config import EvalConfig
from harness.generate.config import GenConfig
from harness.scenario.model import Scenario
from harness.scenario.validate import Issue, validate_scenario
from harness.selfcheck import check_discrimination


def secret_events(s: Scenario) -> list[str]:
    """Find the events that carry secret markers.

    Args:
        s: The scenario to inspect.

    Returns:
        Ids of events whose `secret_markers` list is non-empty, in file order.
    """
    return [e.id for e in s.events if e.secret_markers]


def _has(text: str, needle: str) -> bool:
    """Casefold substring match; an empty needle never matches."""
    return bool(needle) and needle.casefold() in text.casefold()


def _fold(text: str) -> str:
    """Collapse whitespace runs and casefold, for comparing quotes with event text."""
    return " ".join(text.split()).casefold()


def _cue_pattern(word: str) -> re.Pattern[str]:
    """Match a cue word or phrase as whole words, ignoring case and whitespace shape."""
    body = r"\s+".join(re.escape(part) for part in word.split())
    return re.compile(rf"(?<!\w){body}(?!\w)", re.IGNORECASE)


_CODE_SPAN = re.compile(r"(`+)(?!`)(.+?)(?<!`)\1(?!`)", re.DOTALL)
_MASK = "\0"  # neither a word character nor whitespace, so no cue phrase can span it


def _cue_words(
    text: str, patterns: dict[str, re.Pattern[str]], own_markers: Sequence[str] = ()
) -> list[str]:
    """Banned cue words found in `text` outside backtick code spans and the `own_markers`.

    Code spans (`` `enabled: false` ``) and every occurrence of one of the event's own fact
    markers are masked first, longest marker first, so a cue word inside them is allowed and
    the same word anywhere else in the text is not.
    """
    masked = _CODE_SPAN.sub(_MASK, text)
    for marker in sorted((m for m in own_markers if m), key=len, reverse=True):
        masked = re.sub(re.escape(marker), _MASK, masked, flags=re.IGNORECASE)
    return [word for word, pat in patterns.items() if pat.search(masked)]


def _body_issues(s: Scenario, gen: GenConfig) -> list[Issue]:
    issues: list[Issue] = []
    patterns = {w: _cue_pattern(w) for w in gen.banned_cue_words if w.strip()}
    for e in s.events:
        own = s.facts[e.fact].markers if e.fact is not None else []
        if e.fact is not None and not any(_has(e.text, m) for m in own):
            issues.append(
                Issue(
                    "error",
                    "marker_not_in_body",
                    f"event {e.id}: text has none of fact {e.fact}'s markers",
                )
            )
        for fid, f in s.facts.items():
            if fid != e.fact and (
                hit := next((m for m in f.markers if _has(e.text, m)), None)
            ):
                issues.append(
                    Issue(
                        "error",
                        "other_fact_marker",
                        f"event {e.id}: text contains marker {hit!r} of fact {fid}",
                    )
                )
        for word in _cue_words(e.text, patterns, own):
            issues.append(
                Issue(
                    "error",
                    "cue_word",
                    f"event {e.id}: text contains cue word {word!r}",
                )
            )
    for p in s.probes:
        for word in _cue_words(p.query, patterns):
            issues.append(
                Issue(
                    "error",
                    "cue_word",
                    f"probe {p.id}: query contains cue word {word!r}",
                )
            )
    return issues


def _secret_issues(s: Scenario, secret_event_ids: Sequence[str]) -> list[Issue]:
    issues: list[Issue] = []
    secret_ids = set(secret_event_ids)
    secrets = [m for e in s.events if e.id in secret_ids for m in e.secret_markers]
    for e in s.events:
        if e.id in secret_ids:
            if not any(_has(e.text, m) for m in e.secret_markers):
                issues.append(
                    Issue(
                        "error",
                        "secret_not_in_secret_event",
                        f"event {e.id}: text has none of its secret markers",
                    )
                )
        elif hit := next((m for m in secrets if _has(e.text, m)), None):
            issues.append(
                Issue(
                    "error",
                    "secret_in_public_event",
                    f"event {e.id}: public text contains secret marker {hit!r}",
                )
            )
    return issues


def quote_problem(s: Scenario, fact: str | None) -> str | None:
    """Why an event stating `fact` cannot be quoted from the source trace, or None if it can.

    The trace supports only true facts the template did not invent: a fact with `valid=False`,
    a fact listed in `provenance.fabricated_facts`, and noise (no fact) cannot be quoted.

    Args:
        s: The scenario (or skeleton) holding the facts and provenance.
        fact: The event's fact id.

    Returns:
        A short reason, or None.
    """
    if fact is None:
        return "it states no fact"
    if fact in s.provenance.fabricated_facts:
        return f"its fact {fact} is fabricated"
    if fact in s.facts and not s.facts[fact].valid:
        return f"its fact {fact} is never true"
    return None


def _evidence_issues(s: Scenario) -> list[Issue]:
    events = {e.id: e for e in s.events}
    issues: list[Issue] = []
    for ev in s.provenance.evidence:
        if ev.origin != "quoted" or ev.event_id not in events:
            continue
        event = events[ev.event_id]
        if (why := quote_problem(s, event.fact)) is not None:
            issues.append(
                Issue(
                    "error",
                    "quoted_unsupported",
                    f"event {ev.event_id}: origin is quoted but {why}",
                )
            )
        elif ev.quote and _fold(ev.quote) not in _fold(event.text):
            issues.append(
                Issue(
                    "error",
                    "quoted_evidence_mismatch",
                    f"event {ev.event_id}: text does not contain its quoted evidence {ev.quote!r}",
                )
            )
    return issues


def blocking(issues: Sequence[Issue]) -> list[Issue]:
    """The findings that keep a draft from being saved or approved.

    Args:
        issues: Findings of `check_draft`.

    Returns:
        Every error, and the `too_easy` warning (a draft that does not discriminate is useless).
    """
    return [i for i in issues if i.level == "error" or i.code == "too_easy"]


def check_draft(
    s: Scenario,
    cfg: EvalConfig,
    gen: GenConfig,
    secret_event_ids: Sequence[str] = (),
) -> list[Issue]:
    """Run every automatic check a draft must pass before it is saved or approved.

    Order: structural validation; if that finds an error, stop and return its issues.
    Otherwise discrimination, then the body checks (markers, other-fact markers, cue words,
    secret placement, quoted evidence: only a true, non-fabricated fact can be quoted, and the
    quote must be in the body).

    Args:
        s: The draft scenario.
        cfg: Eval config used by the discrimination run.
        gen: Generation config holding the banned cue words.
        secret_event_ids: Events that hold the secrets; see `secret_events`.

    Returns:
        All findings; errors mean the draft must not be saved or approved.
    """
    issues = validate_scenario(s)
    if any(i.level == "error" for i in issues):
        return issues
    return [
        *issues,
        *check_discrimination(s, cfg),
        *_body_issues(s, gen),
        *_secret_issues(s, secret_event_ids),
        *_evidence_issues(s),
    ]
