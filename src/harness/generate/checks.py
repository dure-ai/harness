"""Automatic checks on a generated draft: structure, discrimination, and answer-cue leaks."""

import re
import string
from collections.abc import Mapping, Sequence

from harness.config import EvalConfig
from harness.generate.config import GenConfig
from harness.scenario.model import FeedbackTemplate, Probe, Scenario
from harness.scenario.validate import LEAK_FAMILIES, Issue, validate_scenario
from harness.selfcheck import check_discrimination


def secrets_recorded(s: Scenario) -> bool:
    """Whether the scenario says which events hold the secrets (`provenance.secret_events`).

    The generator always writes a list, empty when there are no secrets; an older or
    hand-written file has none (None), and stays None when the harness saves it again.

    Args:
        s: The scenario.

    Returns:
        True when `provenance.secret_events` is a list.
    """
    return s.provenance.secret_events is not None


def secret_events(s: Scenario) -> list[str]:
    """Find the events that hold the scenario's secrets.

    Args:
        s: The scenario to inspect.

    Returns:
        `provenance.secret_events` when the file records it. Otherwise (an older or hand-written
        file) the ids of events whose `secret_markers` list is non-empty, in file order.
    """
    if s.provenance.secret_events is not None:
        return list(s.provenance.secret_events)
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


_FENCE = re.compile(r"(?<!`)(`{3,})(?!`).*?(?<!`)\1(?!`)", re.DOTALL)
_INLINE_SPAN = re.compile(r"(?<!`)(`{1,2})(?!`)([^\n]+?)(?<!`)\1(?!`)")
_MASK = "\0"  # neither a word character nor whitespace, so no cue phrase can span it
_SPAN_MAX_CHARS = 80
_SPAN_MAX_WORDS = 3
_EDGE = string.punctuation + string.whitespace


def _is_code(inner: str, cues: frozenset[str]) -> bool:
    """Whether the text of a one-line backtick span reads as code, not as prose or a bare cue word.

    Code is at most 80 characters, is not (apart from edge punctuation and case) a cue word or
    phrase on its own, and has at most 3 plain alphabetic words (`enabled: false` and
    `git log --oneline` qualify; `old value is outdated and wrong` does not).
    """
    if len(inner) > _SPAN_MAX_CHARS or _fold(inner.strip(_EDGE)) in cues:
        return False
    return sum(1 for token in inner.split() if token.isalpha()) <= _SPAN_MAX_WORDS


def _mask_code(text: str, cues: frozenset[str]) -> str:
    """Replace the code in `text` with a mask: fenced blocks always, one-line spans if `_is_code`."""
    masked = _FENCE.sub(_MASK, text)

    def span(m: re.Match[str]) -> str:
        return _MASK if _is_code(m.group(2), cues) else m.group(0)

    return _INLINE_SPAN.sub(span, masked)


def _mask_marker(text: str, marker: str, patterns: Mapping[str, re.Pattern[str]]) -> str:
    """Mask the whole-word occurrences of `marker` in `text`, except those that cut a cue word.

    An occurrence inside a longer word (`dated` in `outdated`) is not masked, and neither is one
    that overlaps a cue word without covering all of it (`longer` in `no longer`): masking
    those would hide the cue word. An occurrence that contains a whole cue word is masked.
    """
    left = r"(?<!\w)" if re.match(r"\w", marker) else ""
    right = r"(?!\w)" if re.search(r"\w$", marker) else ""
    cue_spans = [m.span() for pat in patterns.values() for m in pat.finditer(text)]

    def keep(m: re.Match[str]) -> str:
        start, end = m.span()
        for cs, ce in cue_spans:
            if cs < end and start < ce and not (start <= cs and ce <= end):
                return m.group(0)
        return _MASK

    return re.sub(f"{left}{re.escape(marker)}{right}", keep, text, flags=re.IGNORECASE)


def _cue_words(
    text: str, patterns: dict[str, re.Pattern[str]], own_markers: Sequence[str] = ()
) -> list[str]:
    """Banned cue words found in `text` outside code and the `own_markers`.

    Code (`_mask_code`: fenced blocks, and short one-line `` `enabled: false` `` spans) and every
    whole-word occurrence of one of the event's own fact markers (`_mask_marker`) are masked
    first, longest marker first, so a cue word inside them is allowed and the same word anywhere
    else in the text is not.
    """
    cues = frozenset(_fold(w) for w in patterns)
    masked = _mask_code(text, cues)
    for marker in sorted((m for m in own_markers if m), key=len, reverse=True):
        masked = _mask_marker(masked, marker, patterns)
    return [word for word, pat in patterns.items() if pat.search(masked)]


def _text_issues(
    where: str,
    text: str,
    own_facts: Sequence[str],
    other_facts: Sequence[str],
    s: Scenario,
    patterns: dict[str, re.Pattern[str]],
) -> list[Issue]:
    """The answer-cue checks for one piece of text that reaches an engine or is written back.

    Used for event bodies, feedback template bodies and probe queries alike.

    Args:
        where: How the findings name the text, e.g. `event e3` or `probe p1 feedback abstain`.
        text: The body.
        own_facts: The facts the text states. It must contain a marker of one of them (skipped
            when empty: noise, a bare failure record, a query), and its cue-word scan masks their
            markers.
        other_facts: Facts whose markers the text must not contain.
        s: The scenario holding the facts.
        patterns: Compiled banned cue words.

    Returns:
        Errors `marker_not_in_body`, `other_fact_marker` and `cue_word`.
    """
    own = [m for fid in own_facts for m in s.facts[fid].markers]
    issues: list[Issue] = []
    if own_facts and not any(_has(text, m) for m in own):
        issues.append(
            Issue(
                "error",
                "marker_not_in_body",
                f"{where}: text has none of fact {', '.join(own_facts)}'s markers",
            )
        )
    for fid in other_facts:
        if hit := next((m for m in s.facts[fid].markers if _has(text, m)), None):
            issues.append(
                Issue(
                    "error",
                    "other_fact_marker",
                    f"{where}: text contains marker {hit!r} of fact {fid}",
                )
            )
    for word in _cue_words(text, patterns, own):
        issues.append(
            Issue("error", "cue_word", f"{where}: text contains cue word {word!r}")
        )
    return issues


def _feedback_bodies(p: Probe) -> list[tuple[str, FeedbackTemplate]]:
    """The feedback templates of a probe with the names the findings use."""
    fb = p.feedback
    if fb is None:
        return []
    named = [(f"action:{fid}", tpl) for fid, tpl in fb.action.items()]
    for name in ("abstain", "on_false", "on_true"):
        if (tpl := getattr(fb, name)) is not None:
            named.append((name, tpl))
    return [(f"probe {p.id} feedback {name}", tpl) for name, tpl in named]


def _body_issues(s: Scenario, gen: GenConfig) -> list[Issue]:
    issues: list[Issue] = []
    patterns = {w: _cue_pattern(w) for w in gen.banned_cue_words if w.strip()}

    def states(fact: str | None) -> tuple[list[str], list[str]]:
        """(own facts, other facts) of a text that states `fact`; None means noise."""
        return (
            [] if fact is None else [fact],
            [fid for fid in s.facts if fid != fact],
        )

    for e in s.events:
        own, other = states(e.fact)
        issues += _text_issues(f"event {e.id}", e.text, own, other, s, patterns)
    for p in s.probes:
        # A query names the facts it asks about, so only its cue words are checked.
        issues += _text_issues(f"probe {p.id}", p.query, [], [], s, patterns)
        for where, tpl in _feedback_bodies(p):
            own, other = states(tpl.fact)
            issues += _text_issues(where, tpl.text, own, other, s, patterns)
    return issues


def _secret_issues(s: Scenario) -> list[Issue]:
    """Where the secret strings are, and are not.

    The secret events come from `secret_events`. A leak family needs at least one, recorded or
    not. A listed event must exist, carry secret markers and contain one of them. No other event body, no fact text or marker and no
    feedback template body may contain any of them. (A probe query or a visible event holding a
    secret is the core validator's `secret_marker_not_exclusive`, which runs first.)
    """
    issues: list[Issue] = []
    recorded = secrets_recorded(s)
    ids = secret_events(s)
    if not recorded and ids:
        issues.append(
            Issue(
                "warning",
                "secret_events_not_recorded",
                "provenance.secret_events is missing; the secret events are taken from the "
                "events that have secret_markers",
            )
        )
    if s.family in LEAK_FAMILIES and not ids:
        where = "provenance.secret_events is empty" if recorded else "no event has secret_markers"
        issues.append(
            Issue(
                "error",
                "no_secret_events",
                f"family {s.family!r} needs a secret event but {where}",
            )
        )
    by_id = {e.id: e for e in s.events}
    secret_ids = set(ids)
    for eid in ids:
        e = by_id.get(eid)
        if e is None:
            issues.append(
                Issue("error", "secret_missing", f"event {eid}: listed as a secret event but not found")
            )
        elif not e.secret_markers:
            issues.append(
                Issue("error", "secret_missing", f"event {eid}: listed as a secret event but has no secret_markers")
            )
        elif not any(_has(e.text, m) for m in e.secret_markers):
            issues.append(
                Issue(
                    "error",
                    "secret_not_in_secret_event",
                    f"event {e.id}: text has none of its secret markers",
                )
            )
    secrets = [m for eid in ids if (e := by_id.get(eid)) for m in e.secret_markers]
    for e in s.events:
        if e.id not in secret_ids and (
            hit := next((m for m in secrets if _has(e.text, m)), None)
        ):
            issues.append(
                Issue(
                    "error",
                    "secret_in_public_event",
                    f"event {e.id}: public text contains secret marker {hit!r}",
                )
            )
    for fid, f in s.facts.items():
        if hit := next((m for m in secrets if _has(f.text, m) or any(_has(k, m) for k in f.markers)), None):
            issues.append(
                Issue(
                    "error",
                    "secret_in_fact_text",
                    f"fact {fid}: text or markers contain secret marker {hit!r}",
                )
            )
    for p in s.probes:
        for where, tpl in _feedback_bodies(p):
            if hit := next((m for m in secrets if _has(tpl.text, m)), None):
                issues.append(
                    Issue(
                        "error",
                        "secret_in_feedback",
                        f"{where}: text contains secret marker {hit!r}",
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
) -> list[Issue]:
    """Run every automatic check a draft must pass before it is saved or approved.

    Order: structural validation; if that finds an error, stop and return its issues.
    Otherwise discrimination, then the body checks (markers, other-fact markers and cue words in
    event bodies, probe queries and feedback template bodies; secret placement; quoted evidence:
    only a true, non-fabricated fact can be quoted, and the quote must be in the body).
    The secret events are read from `provenance.secret_events` (see `secret_events`).

    The checks are necessary, not sufficient. They look at structure, markers, banned cue
    words and secret placement; they cannot tell whether an event body contradicts its own
    label (e.g. a "true" fact whose record says not to use it). Only human review catches that.

    Args:
        s: The draft scenario.
        cfg: Eval config used by the discrimination run.
        gen: Generation config holding the banned cue words.

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
        *_secret_issues(s),
        *_evidence_issues(s),
    ]
