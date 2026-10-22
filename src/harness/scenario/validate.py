"""Structural validation of a scenario: errors block evaluation, warnings only flag weak spots."""

from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import timedelta
from typing import Literal

from harness.contract import can_see
from harness.scenario.model import (
    FEEDBACK_FAMILIES,
    Feedback,
    FeedbackTemplate,
    LabeledEvent,
    Probe,
    QueryKey,
    Scenario,
    probe_key,
)
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


LEAK_FAMILIES = ("leak_personal", "leak_cross_team")


def _templates(feedback: Feedback) -> list[FeedbackTemplate]:
    singles = [feedback.abstain, feedback.on_false, feedback.on_true]
    return [*feedback.action.values(), *(t for t in singles if t is not None)]


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
    for m in markers:
        if m.strip() and m.casefold() in text:
            return m
    return None


def _check_secret_exclusive(
    p: Probe,
    team: str,
    labeled: list[LabeledEvent],
    error: Callable[[str, str], None],
) -> None:
    """Report secret markers of events hidden from the asker that the asker's side also shows."""
    recorded = [le for le in labeled if le.event.t <= p.t]
    visible = [le for le in recorded if can_see(le.event, p.user, team)]
    query = p.query.casefold()
    for le in recorded:
        if can_see(le.event, p.user, team):
            continue
        hit = _first_marker_in(le.secret_markers, query)
        if hit is not None:
            error(
                "secret_marker_not_exclusive",
                f"probe {p.id}: query contains secret marker {hit!r} of event {le.event.id}, "
                "which the asker cannot see",
            )
        for other in visible:
            hit = _first_marker_in(le.secret_markers, other.event.text.casefold())
            if hit is not None:
                error(
                    "secret_marker_not_exclusive",
                    f"probe {p.id}: visible event {other.event.id} contains secret marker "
                    f"{hit!r} of event {le.event.id}, which the asker cannot see",
                )


def validate_scenario(s: Scenario) -> list[Issue]:
    """Check a scenario's structure and labels before it is used.

    Each problem yields exactly one code: checks that need a resolvable user or a known fact
    skip the probes and events that already failed those checks.

    Args:
        s: The scenario to check.

    Returns:
        Issues in a stable order; empty when the scenario is clean.
    """
    issues: list[Issue] = []

    def error(code: str, message: str) -> None:
        issues.append(Issue("error", code, message))

    for kind, ids in (
        ("event", [e.id for e in s.events]),
        ("probe", [p.id for p in s.probes]),
    ):
        for dup, n in Counter(ids).items():
            if n > 1:
                error("duplicate_id", f"{kind} id {dup!r} appears {n} times")

    same_context: dict[QueryKey, list[str]] = {}
    for p in s.probes:
        same_context.setdefault(probe_key(p), []).append(p.id)
    for ids in same_context.values():
        if len(ids) > 1:
            error(
                "duplicate_probe_context",
                f"probes {', '.join(ids)} ask the same question in the same context "
                "(time, user, session, repo, query, meta); an engine cannot tell them apart",
            )

    for e in s.events:
        if e.fact is not None and e.fact not in s.facts:
            error("unknown_fact", f"event {e.id}: unknown fact {e.fact!r}")
    for p in s.probes:
        for fid in p.about:
            if fid not in s.facts:
                error(
                    "unknown_fact",
                    f"probe {p.id}: about references unknown fact {fid!r}",
                )
        if p.feedback is not None:
            for key in p.feedback.action:
                if key not in s.facts:
                    error(
                        "unknown_fact",
                        f"probe {p.id}: feedback action key {key!r} is not a fact",
                    )
            for tpl in _templates(p.feedback):
                if tpl.fact is not None and tpl.fact not in s.facts:
                    error(
                        "unknown_fact",
                        f"probe {p.id}: feedback template references unknown fact {tpl.fact!r}",
                    )

    for fid, f in s.facts.items():
        if any(not m.strip() for m in f.markers):
            error("empty_marker", f"fact {fid}: empty marker")
    for e in s.events:
        if any(not m.strip() for m in e.secret_markers):
            error("empty_marker", f"event {e.id}: empty secret marker")
    for p in s.probes:
        if p.feedback is not None and any(
            t.delay is not None and t.delay < timedelta(0)
            for t in _templates(p.feedback)
        ):
            error(
                "negative_delay",
                f"probe {p.id}: feedback template with a negative delay",
            )

    order = {e.id: (e.t, i) for i, e in enumerate(s.events)}
    for i, e in enumerate(s.events):
        for ref in e.refs:
            if ref not in order:
                error("unknown_ref", f"event {e.id}: refs unknown event {ref!r}")
            elif order[ref] >= (e.t, i):  # must be replayed before this event
                error("ref_not_earlier", f"event {e.id}: refs {ref!r}, which is not earlier")

    bad_users, messages = _unknown_users(s)
    for message in messages:
        error("unknown_user", message)

    for fid, f in s.facts.items():
        if (
            f.valid_from is not None
            and f.valid_until is not None
            and f.valid_from >= f.valid_until
        ):
            error(
                "interval_inverted", f"fact {fid}: valid_from is not before valid_until"
            )

    feedback_family = s.family in FEEDBACK_FAMILIES
    if feedback_family != (s.mode == "feedback"):
        error("mode_mismatch", f"family {s.family!r} does not match mode {s.mode!r}")

    if s.mode == "read_only":
        for p in s.probes:
            if p.feedback is not None:
                error(
                    "feedback_in_read_only",
                    f"probe {p.id}: feedback in a read_only scenario",
                )

    if s.family != "normal_repeat":
        kinds = {p.kind for p in s.probes}
        needed = "leak" if s.family in LEAK_FAMILIES else "block"
        if needed not in kinds:
            error("pairing_missing", f"no {needed} probe (family {s.family!r})")
        if "promote" not in kinds:
            error("pairing_missing", "no promote probe")

    for e in s.events:
        if e.team is not None and e.team not in s.world.teams:
            error("unknown_team", f"event {e.id}: team {e.team!r} is not in world.teams")

    # Checks below need the probe's user and the event users resolvable to a team.
    known_events = s.model_copy(
        update={"events": [e for e in s.events if e.user not in bad_users]}
    )
    labeled = known_events.labeled_events()

    for p in s.probes:
        if p.user in bad_users or any(fid not in s.facts for fid in p.about):
            continue
        ctx = probe_ctx(p)
        team = s.team_of(p.user)
        _check_secret_exclusive(p, team, labeled, error)
        about = {fid: s.facts[fid] for fid in p.about}
        recorded = [le for le in labeled if le.fact in about and le.event.t <= p.t]

        if p.kind == "block" and not any(
            not true_at(f, p.t, ctx) for f in about.values()
        ):
            error(
                "block_without_false",
                f"probe {p.id}: no about fact is false at the probe time and context",
            )
        elif p.kind == "promote":
            seen_true = {
                le.fact
                for le in recorded
                if le.fact is not None
                and can_see(le.event, p.user, team)
                and true_at(about[le.fact], p.t, ctx)
            }
            if not seen_true:
                error(
                    "promote_without_seen_true",
                    f"probe {p.id}: no true about fact has been recorded visibly to the asker by the probe time",
                )
        elif p.kind == "leak" and all(
            can_see(le.event, p.user, team) for le in recorded
        ):
            error(
                "leak_without_hidden",
                f"probe {p.id}: no recorded about fact is hidden from the asker",
            )

    bled: set[tuple[str, str]] = set()
    for p in s.probes:
        about_ids = [fid for fid in p.about if fid in s.facts]
        for le in labeled:
            body = le.event.text.casefold()
            for fid in about_ids:
                if fid == le.fact or (le.event.id, fid) in bled:
                    continue
                hit = _first_marker_in(s.facts[fid].markers, body)
                if hit is not None:
                    bled.add((le.event.id, fid))
                    error(
                        "marker_bleed",
                        f"event {le.event.id}: body contains marker {hit!r} of fact {fid!r}, "
                        f"which is not the event's fact (probe {p.id} asks about both)",
                    )

    used = dict.fromkeys(fid for p in s.probes for fid in p.about if fid in s.facts)
    for fid in used:
        if not s.facts[fid].markers:
            issues.append(
                Issue("warning", "markers_missing", f"fact {fid}: no markers")
            )

    return issues
