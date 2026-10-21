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


def _cue_words(text: str, patterns: dict[str, re.Pattern[str]], allowed: Sequence[str] = ()) -> list[str]:
    """Banned cue words found in `text`, except those inside one of the `allowed` markers."""
    return [
        word
        for word, pat in patterns.items()
        if pat.search(text) and not any(pat.search(marker) for marker in allowed)
    ]


def _body_issues(s: Scenario, gen: GenConfig) -> list[Issue]:
    issues: list[Issue] = []
    patterns = {w: _cue_pattern(w) for w in gen.banned_cue_words if w.strip()}
    for e in s.events:
        own = s.facts[e.fact].markers if e.fact is not None else []
        if e.fact is not None and not any(_has(e.text, m) for m in own):
            issues.append(Issue("error", "marker_not_in_body", f"event {e.id}: text has none of fact {e.fact}'s markers"))
        for fid, f in s.facts.items():
            if fid != e.fact and (hit := next((m for m in f.markers if _has(e.text, m)), None)):
                issues.append(Issue("error", "other_fact_marker", f"event {e.id}: text contains marker {hit!r} of fact {fid}"))
        for word in _cue_words(e.text, patterns, own):
            issues.append(Issue("error", "cue_word", f"event {e.id}: text contains cue word {word!r}"))
    for p in s.probes:
        for word in _cue_words(p.query, patterns):
            issues.append(Issue("error", "cue_word", f"probe {p.id}: query contains cue word {word!r}"))
    return issues


def _secret_issues(s: Scenario, secret_event_ids: Sequence[str]) -> list[Issue]:
    issues: list[Issue] = []
    secret_ids = set(secret_event_ids)
    secrets = [m for e in s.events if e.id in secret_ids for m in e.secret_markers]
    for e in s.events:
        if e.id in secret_ids:
            if not any(_has(e.text, m) for m in e.secret_markers):
                issues.append(Issue("error", "secret_not_in_secret_event", f"event {e.id}: text has none of its secret markers"))
        elif hit := next((m for m in secrets if _has(e.text, m)), None):
            issues.append(Issue("error", "secret_in_public_event", f"event {e.id}: public text contains secret marker {hit!r}"))
    return issues


def _evidence_issues(s: Scenario) -> list[Issue]:
    events = {e.id: e for e in s.events}
    return [
        Issue("error", "quoted_evidence_mismatch", f"event {ev.event_id}: text does not contain its quoted evidence {ev.quote!r}")
        for ev in s.provenance.evidence
        if ev.origin == "quoted"
        and ev.quote
        and ev.event_id in events
        and _fold(ev.quote) not in _fold(events[ev.event_id].text)
    ]


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
    secret placement, quoted evidence).

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
