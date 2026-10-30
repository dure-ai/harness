"""Automatic checks on a generated draft: structure, discrimination, and answer-cue leaks."""

import re
import string
from collections.abc import Mapping, Sequence

from harness.config import EvalConfig
from harness.generate.config import GenConfig
from harness.scenario.model import FeedbackTemplate, Probe, Scenario
from harness.scenario.validate import LEAK_FAMILIES, Issue, validate_scenario
from harness.selfcheck import check_discrimination
from harness.text import contains_secret, jaccard


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


def _has_secret(text: str, marker: str) -> bool:
    """Secret match that ignores case, width, spacing and hyphen variants (`contains_secret`)."""
    return contains_secret(text, marker)


def _fold(text: str) -> str:
    """Collapse whitespace runs and casefold, for comparing quotes with event text."""
    return " ".join(text.split()).casefold()


def _cue_pattern(word: str) -> re.Pattern[str]:
    """Match a cue word or phrase as whole words, ignoring case and whitespace shape."""
    body = r"\s+".join(re.escape(part) for part in word.split())
    return re.compile(rf"(?<!\w){body}(?!\w)", re.IGNORECASE)


# A fenced block: a run of 3+ backticks that opens a line (after optional indentation),
# followed by at most a language tag (`yaml`, `c++`) and a newline, closed by the same run at
# the start of a later line. A one-line ```...``` span, or ``` after prose on the same line, is
# not a block; it is checked like any other text.
_FENCE = re.compile(
    r"^[ \t]*(`{3,})(?!`)[\w+#.-]*[ \t]*\n(?:.*?\n)?[ \t]*\1(?!`)", re.DOTALL | re.MULTILINE
)
_INLINE_SPAN = re.compile(r"(?<!`)(`+)(?!`)([^\n]+?)(?<!`)\1(?!`)")
_MASK = "\0"  # neither a word character nor whitespace, so no cue phrase can span it
_SPAN_MAX_CHARS = 80
_SPAN_MAX_WORDS = 3
_EDGE = string.punctuation + string.whitespace
_CODE_CHARS = frozenset("=:-_./()")
_VALUE_BEFORE = re.compile(r"[=:][\s`]*$")  # `key: <value>`, `x = <value>`
_REST_EMPTY = re.compile(r"[\s`]*$")
_FALSE_AFTER_ASSIGN = re.compile(r"(?<=[=:])false(?!\w)", re.IGNORECASE)  # debug=false, debug:false


def _is_prose_word(token: str) -> bool:
    """A whitespace token that is a plain word once edge punctuation is stripped (`wrong,`, `It's`)."""
    core = token.strip(_EDGE).replace("'", "").replace("\u2019", "")
    return core.isalpha()


def _is_code(inner: str, cues: frozenset[str]) -> bool:
    """Whether the text of a one-line backtick span can be code at all.

    It is at most 80 characters, has at most 3 plain words (`git log --oneline` qualifies;
    `this is wrong, very wrong!` does not), and is not a cue word or phrase on its own once
    edge punctuation, parentheses and hyphens are stripped (`(deprecated)`, `--outdated`,
    `wrong()` are not code; the literal `false` is). A cue word inside such a span is still
    exempt only when it is attached to code, see `_attached`.
    """
    bare = _fold(inner.strip(_EDGE))
    if bare in cues and bare != "false":
        return False
    return len(inner) <= _SPAN_MAX_CHARS and sum(map(_is_prose_word, inner.split())) <= _SPAN_MAX_WORDS


def _attached(inner: str, start: int, end: int) -> bool:
    """Whether the cue occurrence `inner[start:end]` of a code span reads as code, not as prose.

    Attached means touching a code character (`= : - _ . / ( )`) with no space: on the left
    (`--legacy-x`, `mode=legacy`, `api.deprecated`; a `(` only right after a word character, as
    in `f(deprecated)`, never `get (deprecated)`), or on the right when that character joins it
    to more code (`deprecated=true`; a trailing `obsolete:` or `outdated.` is prose
    punctuation). The literal `false` is also exempt as the whole value after `=` or `:`
    (`enabled: false`, ``x = `false` ``) and alone in the span. Any other cue word after a
    space (`status: obsolete`, `x = obsolete`, `stale cache`) is not attached.
    """
    before, after = inner[:start], inner[end:]
    if before and before[-1] in _CODE_CHARS:
        if before[-1] != "(" or (len(before) >= 2 and (before[-2].isalnum() or before[-2] == "_")):
            return True
    if len(after) >= 2 and after[0] in _CODE_CHARS and not after[1].isspace():
        return True
    if inner[start:end].casefold() != "false":
        return False
    return bool(_VALUE_BEFORE.search(before) and _REST_EMPTY.match(after)) or inner.strip(" `").casefold() == "false"


def _mask_code(text: str, patterns: Mapping[str, re.Pattern[str]]) -> str:
    """Mask the code in `text`: fenced blocks whole, and the attached cue words of one-line spans.

    A one-line span (any number of backticks, `` `x` `` to ```` ```x``` ````) that is not code
    by `_is_code` is left as prose; in one that is, only cue words `_attached` to code are
    masked. Outside code, the literal `false` right after `=` or `:` (`debug=false`) is masked.
    """
    masked = _FENCE.sub(_MASK, text)
    cues = frozenset(_fold(w) for w in patterns)

    def span(m: re.Match[str]) -> str:
        inner = m.group(2)
        if not _is_code(inner, cues):
            return m.group(0)
        hits = sorted(
            (h.start(), h.end())
            for pat in patterns.values()
            for h in pat.finditer(inner)
            if _attached(inner, h.start(), h.end())
        )
        out, pos = [], 0
        for start, end in hits:
            if start >= pos:
                out += [inner[pos:start], _MASK]
                pos = end
        out.append(inner[pos:])
        return m.group(1) + "".join(out) + m.group(1)

    masked = _INLINE_SPAN.sub(span, masked)
    return _FALSE_AFTER_ASSIGN.sub(_MASK, masked)


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

    Code (`_mask_code`: multi-line fenced blocks, and cue words attached to code in short
    one-line spans such as `` `enabled: false` ``) and every whole-word occurrence of one of the
    `own_markers` (`_mask_marker`) are masked first, longest marker first, so a cue word inside
    them is allowed and the same word anywhere else in the text is not.
    """
    masked = _mask_code(text, patterns)
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
    mask_facts: Sequence[str] = (),
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
        mask_facts: More facts whose markers the cue-word scan masks, without requiring one
            in the text (a probe query may reuse the markers of the facts it asks about).

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
    masked = [*own, *(m for fid in mask_facts for m in s.facts[fid].markers)]
    for word in _cue_words(text, patterns, masked):
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


def _marker_cue_words(marker: str, patterns: dict[str, re.Pattern[str]]) -> list[str]:
    """Banned cue words a marker holds, judged as the code literal it may be.

    The marker is scanned as if it were written in a one-line code span, so it gets the same
    exemptions as code in a body: a cue word attached to code characters (`--legacy-peer-deps`)
    and the literal `false` as a value (`enabled: false`, `debug=false`). Prose does not pass
    (`legacy flag`, `the stale option`), and neither does a marker that is only a cue word,
    `false` included: it would hide that word everywhere in the text it is a marker of.
    """
    found = _cue_words(marker if "`" in marker else f"`{marker}`", patterns)
    bare = _fold(marker.strip(_EDGE))
    return found + [w for w in patterns if _fold(w) == bare and w not in found]


def _marker_issues(s: Scenario, patterns: dict[str, re.Pattern[str]]) -> list[Issue]:
    """Error `cue_word_in_marker` for a marker that holds a banned cue word.

    The cue-word scan masks a fact's markers in the text that states it (and the `about`
    facts' markers in a query), so a cue word that is itself a marker, or sits inside one, would
    pass the scan and still reach the engine. The marker is therefore scanned too, with the same
    word-boundary rule and code-literal exemptions (`_marker_cue_words`). Only facts that matter
    are scanned: the ones an event or feedback template states and the ones a probe asks about.

    Args:
        s: The scenario.
        patterns: Compiled banned cue words.

    Returns:
        One error per offending marker, naming the fact and the cue words found.
    """
    used = {e.fact for e in s.events}
    used |= {tpl.fact for p in s.probes for _, tpl in _feedback_bodies(p)}
    used |= {fid for p in s.probes for fid in p.about}
    issues: list[Issue] = []
    for fid, f in s.facts.items():
        if fid not in used:
            continue
        for marker in f.markers:
            if words := _marker_cue_words(marker, patterns):
                issues.append(
                    Issue(
                        "error",
                        "cue_word_in_marker",
                        f"fact {fid}: marker {marker!r} contains cue word "
                        + ", ".join(repr(w) for w in words),
                    )
                )
    return issues


def marker_issues(s: Scenario, gen: GenConfig) -> list[Issue]:
    """Errors `cue_word_in_marker` for a scenario, with the banned cue words of `gen`.

    The body writer runs this on its filled reply so a marker that holds a cue word is sent
    back for one retry instead of rejecting the whole draft.
    """
    patterns = {w: _cue_pattern(w) for w in gen.banned_cue_words if w.strip()}
    return _marker_issues(s, patterns)


def _body_issues(s: Scenario, gen: GenConfig) -> list[Issue]:
    patterns = {w: _cue_pattern(w) for w in gen.banned_cue_words if w.strip()}
    issues: list[Issue] = _marker_issues(s, patterns)

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
        # A query names the facts it asks about, so only its cue words are checked, and the
        # markers of those facts are masked like an event's own markers.
        about = [fid for fid in p.about if fid in s.facts]
        issues += _text_issues(f"probe {p.id}", p.query, [], [], s, patterns, about)
        for where, tpl in _feedback_bodies(p):
            own, other = states(tpl.fact)
            issues += _text_issues(where, tpl.text, own, other, s, patterns)
    return issues


def _secret_issues(s: Scenario) -> list[Issue]:
    """Where the secret strings are, and are not.

    The secret events come from `secret_events`. When `provenance.secret_events` is recorded it
    must name exactly the events that have `secret_markers` (`secret_events_mismatch`), so a
    secret cannot sit in an unlisted event the other checks skip. A leak family needs at least
    one, recorded or not. A listed event must exist, carry secret markers and contain one of them. No other event body, no fact text or marker and no
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
    if recorded:
        listed = set(ids)
        marked = {e.id for e in s.events if e.secret_markers}
        if listed != marked:
            parts = []
            if unlisted := sorted(marked - listed):
                parts.append(f"events with secret_markers not listed: {', '.join(unlisted)}")
            if unmarked := sorted(listed - marked):
                parts.append(f"listed events without secret_markers: {', '.join(unmarked)}")
            issues.append(
                Issue(
                    "error",
                    "secret_events_mismatch",
                    "provenance.secret_events differs from the events that have secret_markers ("
                    + "; ".join(parts)
                    + ")",
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
        elif not any(_has_secret(e.text, m) for m in e.secret_markers):
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
            hit := next((m for m in secrets if _has_secret(e.text, m)), None)
        ):
            issues.append(
                Issue(
                    "error",
                    "secret_in_public_event",
                    f"event {e.id}: public text contains secret marker {hit!r}",
                )
            )
    for fid, f in s.facts.items():
        if hit := next((m for m in secrets if _has_secret(f.text, m) or any(_has_secret(k, m) for k in f.markers)), None):
            issues.append(
                Issue(
                    "error",
                    "secret_in_fact_text",
                    f"fact {fid}: text or markers contain secret marker {hit!r}",
                )
            )
    for p in s.probes:
        for where, tpl in _feedback_bodies(p):
            if hit := next((m for m in secrets if _has_secret(tpl.text, m)), None):
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


def repeat_issues(s: Scenario, cfg: EvalConfig) -> list[Issue]:
    """Error `repeats_too_different` when a single-source poison's false records are no near repeats.

    The `repeat_n` baseline serves a record only when `n` visible records (itself included) have
    token Jaccard >= `tau` with it. A `single_source_poison` scenario exists to show that baseline
    fooled by one user's repeated false claim, so that user's records of the false fact must
    include at least `n` records that each match at least `n - 1` of the others. Only the family
    `single_source_poison` is checked.

    Args:
        s: The scenario.
        cfg: Eval config holding `repeat_n` (n and tau).

    Returns:
        One error naming the pairwise maximum similarities, or nothing.
    """
    if s.family != "single_source_poison":
        return []
    n, tau = cfg.repeat_n.n, cfg.repeat_n.tau
    best: list[tuple[str, list[float]]] = []
    for fid, f in s.facts.items():
        if f.valid:
            continue
        for user in dict.fromkeys(e.user for e in s.events if e.fact == fid):
            group = [e for e in s.events if e.fact == fid and e.user == user]
            sims = [[jaccard(a.text, b.text) for b in group if b is not a] for a in group]
            if sum(sum(x >= tau for x in row) >= n - 1 for row in sims) >= n:
                return []
            best.append((f"{fid} by {user}", [max(row, default=0.0) for row in sims]))
    detail = "; ".join(
        f"{who}: " + ", ".join(f"{m:.2f}" for m in maxes) for who, maxes in best
    )
    return [
        Issue(
            "error",
            "repeats_too_different",
            f"repeat_n (n={n}, tau={tau}) would not serve the false fact: need {n} records of it "
            f"from one user each similar to {n - 1} others; max similarity per record: "
            f"{detail or 'no records of a false fact'}",
        )
    ]


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
        *repeat_issues(s, cfg),
    ]
