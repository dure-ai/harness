"""Structural validation of a scenario: errors block evaluation, warnings only flag weak spots."""

from collections import Counter
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import timedelta
from typing import Literal, Self

from harness.contract import can_see
from harness.scenario.model import (
    FAMILIES,
    FEEDBACK_FAMILIES,
    Feedback,
    FeedbackTemplate,
    LabeledEvent,
    Probe,
    QueryKey,
    Scenario,
    probe_key,
)
from harness.text import contains_secret
from harness.world import probe_ctx, true_at


@dataclass(frozen=True)
class Issue:
    """One validation finding.

    Attributes:
        level: "error" blocks evaluation; "warning" only flags a weak spot.
        code: Stable identifier such as "block_without_false".
        message: Human-readable detail naming the probe, event or fact.
    """

    level: Literal["error", "warning"]
    code: str
    message: str

    @classmethod
    def error(cls, code: str, message: str) -> Self:
        """An issue that blocks evaluation."""
        return cls("error", code, message)

    @classmethod
    def warning(cls, code: str, message: str) -> Self:
        """An issue that only flags a weak spot."""
        return cls("warning", code, message)


# The families that need a `leak` probe; derived from the model's list so a new family is seen.
LEAK_FAMILIES = tuple(f for f in FAMILIES if f.startswith("leak_"))
# Families that need a promote probe but no block probe.
NO_BLOCK_FAMILIES = ("ext_recall",)


def _templates(feedback: Feedback) -> list[FeedbackTemplate]:
    return [t for _, t in _named_templates(feedback)]


def _named_templates(feedback: Feedback | None) -> list[tuple[str, FeedbackTemplate]]:
    """(slot name, template) for every feedback template, e.g. ("action[f_old]", ...)."""
    if feedback is None:
        return []
    singles = [("abstain", feedback.abstain), ("on_false", feedback.on_false), ("on_true", feedback.on_true)]
    return [
        *((f"action[{key}]", t) for key, t in feedback.action.items()),
        *((slot, t) for slot, t in singles if t is not None),
    ]


def _unknown_users(s: Scenario) -> tuple[set[str], list[str]]:
    """Users that cannot be resolved to exactly one team, plus a message per problem."""
    memberships = Counter(u for members in s.world.teams.values() for u in set(members))
    bad: set[str] = {u for u, n in memberships.items() if n > 1}
    messages = [f"user {u!r} is in more than one team" for u in sorted(bad)]
    for where, user in [
        *((f"event {e.id}", e.user) for e in s.events),
        *((f"probe {p.id}", p.user) for p in s.probes),
    ]:
        if user not in memberships:
            bad.add(user)
            messages.append(f"{where}: user {user!r} is not in any team")
    return bad, messages


def _first_marker_in(markers: Iterable[str], text: str) -> str | None:
    """First non-blank marker found in already-casefolded `text`, ignoring case."""
    return next((m for m in markers if m.strip() and m.casefold() in text), None)


def _secret_in(markers: Iterable[str], text: str) -> str | None:
    """First marker found in `text` by `contains_secret` (spacing, hyphen, width and case blind)."""
    return next((m for m in markers if contains_secret(text, m)), None)


def _secret_exclusive(p: Probe, team: str, labeled: list[LabeledEvent]) -> Iterator[Issue]:
    """Secret markers of events hidden from the asker that the asker's side also shows.

    The asker's side is the query, the events visible to the asker by the probe time, and the
    probe's own feedback templates (the asker writes those records, so the asker sees them;
    they are checked against every event hidden from the asker, whenever it is recorded).
    """
    for slot, tpl in _named_templates(p.feedback):
        body = tpl.text.casefold()
        for le in labeled:
            if can_see(le.event, p.user, team):
                continue
            if (hit := _secret_in(le.secret_markers, body)) is not None:
                yield Issue.error(
                    "secret_marker_not_exclusive",
                    f"probe {p.id}: feedback template {slot} contains secret marker {hit!r} "
                    f"of event {le.event.id}, which the asker cannot see",
                )
    recorded = [le for le in labeled if le.event.t <= p.t]
    visible = [le for le in recorded if can_see(le.event, p.user, team)]
    query = p.query.casefold()
    for le in recorded:
        if can_see(le.event, p.user, team):
            continue
        if (hit := _secret_in(le.secret_markers, query)) is not None:
            yield Issue.error(
                "secret_marker_not_exclusive",
                f"probe {p.id}: query contains secret marker {hit!r} of event {le.event.id}, "
                "which the asker cannot see",
            )
        for other in visible:
            if (hit := _secret_in(le.secret_markers, other.event.text.casefold())) is not None:
                yield Issue.error(
                    "secret_marker_not_exclusive",
                    f"probe {p.id}: visible event {other.event.id} contains secret marker "
                    f"{hit!r} of event {le.event.id}, which the asker cannot see",
                )


def _duplicate_ids(s: Scenario) -> Iterator[Issue]:
    for kind, ids in (("event", [e.id for e in s.events]), ("probe", [p.id for p in s.probes])):
        for dup, n in Counter(ids).items():
            if n > 1:
                yield Issue.error("duplicate_id", f"{kind} id {dup!r} appears {n} times")


def _duplicate_probe_contexts(s: Scenario) -> Iterator[Issue]:
    same_context: dict[QueryKey, list[str]] = {}
    for p in s.probes:
        same_context.setdefault(probe_key(p), []).append(p.id)
    for ids in same_context.values():
        if len(ids) > 1:
            yield Issue.error(
                "duplicate_probe_context",
                f"probes {', '.join(ids)} ask the same question in the same context "
                "(time, user, session, repo, query, meta); an engine cannot tell them apart",
            )


def _unknown_facts(s: Scenario) -> Iterator[Issue]:
    for e in s.events:
        if e.fact is not None and e.fact not in s.facts:
            yield Issue.error("unknown_fact", f"event {e.id}: unknown fact {e.fact!r}")
    for p in s.probes:
        for fid in p.about:
            if fid not in s.facts:
                yield Issue.error("unknown_fact", f"probe {p.id}: about references unknown fact {fid!r}")
        if p.feedback is None:
            continue
        for key in p.feedback.action:
            if key not in s.facts:
                yield Issue.error("unknown_fact", f"probe {p.id}: feedback action key {key!r} is not a fact")
        for tpl in _templates(p.feedback):
            if tpl.fact is not None and tpl.fact not in s.facts:
                yield Issue.error(
                    "unknown_fact", f"probe {p.id}: feedback template references unknown fact {tpl.fact!r}"
                )


def _empty_markers_and_negative_delays(s: Scenario) -> Iterator[Issue]:
    for fid, f in s.facts.items():
        if any(not m.strip() for m in f.markers):
            yield Issue.error("empty_marker", f"fact {fid}: empty marker")
    for e in s.events:
        if any(not m.strip() for m in e.secret_markers):
            yield Issue.error("empty_marker", f"event {e.id}: empty secret marker")
    for p in s.probes:
        if p.feedback is not None and any(
            t.delay is not None and t.delay < timedelta(0) for t in _templates(p.feedback)
        ):
            yield Issue.error("negative_delay", f"probe {p.id}: feedback template with a negative delay")


def _bad_refs(s: Scenario) -> Iterator[Issue]:
    order = {e.id: (e.t, i) for i, e in enumerate(s.events)}
    for i, e in enumerate(s.events):
        for ref in e.refs:
            if ref not in order:
                yield Issue.error("unknown_ref", f"event {e.id}: refs unknown event {ref!r}")
            elif order[ref] >= (e.t, i):  # must be replayed before this event
                yield Issue.error("ref_not_earlier", f"event {e.id}: refs {ref!r}, which is not earlier")


def _confirmations_of_other_facts(s: Scenario) -> Iterator[Issue]:
    """Warn about a successful judgement whose own fact is not the fact of an event it judges."""
    by_id = {e.id: e for e in s.events}
    for e in s.events:
        if e.outcome != "success" or e.fact is None or not e.refs:
            continue
        if any(r not in by_id for r in e.refs):
            continue  # `unknown_ref` already reports it
        if e.fact not in {by_id[r].fact for r in e.refs}:
            yield Issue.warning(
                "confirm_asserts_other_fact",
                f"event {e.id}: a successful judgement of {e.refs} asserts fact "
                f"{e.fact!r}, not the fact of the events it judges "
                "(contract §3-2: refs are what is judged)",
            )


def _inverted_intervals(s: Scenario) -> Iterator[Issue]:
    for fid, f in s.facts.items():
        if f.valid_from is not None and f.valid_until is not None and f.valid_from >= f.valid_until:
            yield Issue.error("interval_inverted", f"fact {fid}: valid_from is not before valid_until")


def _family_and_mode(s: Scenario) -> Iterator[Issue]:
    """The mode matches the family, read_only probes carry no feedback, and the probe kinds pair up."""
    if (s.family in FEEDBACK_FAMILIES) != (s.mode == "feedback"):
        yield Issue.error("mode_mismatch", f"family {s.family!r} does not match mode {s.mode!r}")
    if s.mode == "read_only":
        for p in s.probes:
            if p.feedback is not None:
                yield Issue.error("feedback_in_read_only", f"probe {p.id}: feedback in a read_only scenario")
    if s.family != "normal_repeat":
        kinds = {p.kind for p in s.probes}
        needed = "leak" if s.family in LEAK_FAMILIES else "block"
        if needed not in kinds and s.family not in NO_BLOCK_FAMILIES:
            yield Issue.error("pairing_missing", f"no {needed} probe (family {s.family!r})")
        if "promote" not in kinds:
            yield Issue.error("pairing_missing", "no promote probe")


def _unknown_teams(s: Scenario) -> Iterator[Issue]:
    for e in s.events:
        if e.team is not None and e.team not in s.world.teams:
            yield Issue.error("unknown_team", f"event {e.id}: team {e.team!r} is not in world.teams")


def _probe_labels(s: Scenario, bad_users: set[str], labeled: list[LabeledEvent]) -> Iterator[Issue]:
    """Each probe's labels make sense for its kind; probes with an unresolvable user or fact are skipped."""
    for p in s.probes:
        if p.user in bad_users or any(fid not in s.facts for fid in p.about):
            continue
        ctx = probe_ctx(p)
        team = s.team_of(p.user)
        yield from _secret_exclusive(p, team, labeled)
        about = {fid: s.facts[fid] for fid in p.about}
        recorded = [le for le in labeled if le.fact in about and le.event.t <= p.t]
        if p.kind == "block" and not any(not true_at(f, p.t, ctx) for f in about.values()):
            yield Issue.error(
                "block_without_false", f"probe {p.id}: no about fact is false at the probe time and context"
            )
        elif p.kind == "promote":
            seen_true = {
                le.fact
                for le in recorded
                if le.fact is not None and can_see(le.event, p.user, team) and true_at(about[le.fact], p.t, ctx)
            }
            if not seen_true:
                yield Issue.error(
                    "promote_without_seen_true",
                    f"probe {p.id}: no true about fact has been recorded visibly to the asker by the probe time",
                )
        elif p.kind == "leak" and all(can_see(le.event, p.user, team) for le in recorded):
            yield Issue.error("leak_without_hidden", f"probe {p.id}: no recorded about fact is hidden from the asker")


def _marker_bleed(s: Scenario, labeled: list[LabeledEvent]) -> Iterator[Issue]:
    """Event bodies and feedback templates that carry a marker of another fact a probe asks about.

    Feedback templates become events too, so their texts are checked like event bodies. Each
    (text, fact) pair is reported once.
    """
    templates = [
        (f"probe {q.id} feedback template {slot}", tpl) for q in s.probes for slot, tpl in _named_templates(q.feedback)
    ]
    bled: set[tuple[str, str]] = set()
    for p in s.probes:
        about_ids = [fid for fid in p.about if fid in s.facts]
        for where, tpl in templates:
            body = tpl.text.casefold()
            for fid in about_ids:
                if fid == tpl.fact or (where, fid) in bled:
                    continue
                if (hit := _first_marker_in(s.facts[fid].markers, body)) is not None:
                    bled.add((where, fid))
                    yield Issue.error(
                        "marker_bleed",
                        f"{where}: text contains marker {hit!r} of fact {fid!r}, which is not "
                        f"the template's fact (probe {p.id} asks about it)",
                    )
        for le in labeled:
            body = le.event.text.casefold()
            for fid in about_ids:
                if fid == le.fact or (le.event.id, fid) in bled:
                    continue
                if (hit := _first_marker_in(s.facts[fid].markers, body)) is not None:
                    bled.add((le.event.id, fid))
                    yield Issue.error(
                        "marker_bleed",
                        f"event {le.event.id}: body contains marker {hit!r} of fact {fid!r}, "
                        f"which is not the event's fact (probe {p.id} asks about both)",
                    )


def _warnings(s: Scenario) -> Iterator[Issue]:
    """Facts a probe asks about without markers, and feedback action templates that do not match `about`.

    Action records come only from `feedback.action[<followed about fact>]` (design 6.2). These
    are warnings, not errors: a template may be left out on purpose. Unknown facts are already
    `unknown_fact` errors, and feedback in a read_only scenario is `feedback_in_read_only`;
    both are skipped here.
    """
    for fid in dict.fromkeys(fid for p in s.probes for fid in p.about if fid in s.facts):
        if not s.facts[fid].markers:
            yield Issue.warning("markers_missing", f"fact {fid}: no markers")
    if s.mode != "feedback":
        return
    for p in s.probes:
        if p.feedback is None:
            continue
        action = p.feedback.action
        for fid in dict.fromkeys(p.about):
            if fid in s.facts and fid not in action:
                yield Issue.warning(
                    "feedback_action_missing_for_about",
                    f"probe {p.id}: about fact {fid!r} has no feedback action template",
                )
        for key in action:
            if key in s.facts and key not in p.about:
                yield Issue.warning(
                    "feedback_action_not_in_about", f"probe {p.id}: feedback action key {key!r} is not in about"
                )


def validate_scenario(s: Scenario) -> list[Issue]:
    """Check a scenario's structure and labels before it is used.

    Checks that need a resolvable user or a known fact skip the probes and events that already
    failed those checks, so a problem is not reported twice under a dependent code. Independent
    checks can still both fire on one defect (a secret marker repeated in a visible event body
    may raise both `secret_marker_not_exclusive` and `marker_bleed`).

    Args:
        s: The scenario to check.

    Returns:
        Issues in a stable order; empty when the scenario is clean.
    """
    bad_users, user_messages = _unknown_users(s)
    # The label checks need the probe's user and the event users resolvable to a team.
    labeled = s.model_copy(update={"events": [e for e in s.events if e.user not in bad_users]}).labeled_events()
    return [
        *_duplicate_ids(s),
        *_duplicate_probe_contexts(s),
        *_unknown_facts(s),
        *_empty_markers_and_negative_delays(s),
        *_bad_refs(s),
        *_confirmations_of_other_facts(s),
        *(Issue.error("unknown_user", message) for message in user_messages),
        *_inverted_intervals(s),
        *_family_and_mode(s),
        *_unknown_teams(s),
        *_probe_labels(s, bad_users, labeled),
        *_marker_bleed(s, labeled),
        *_warnings(s),
    ]
