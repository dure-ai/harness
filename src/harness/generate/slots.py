"""Body slots: what the body writer must fill in a skeleton, its reply schema, and how replies are applied.

A family template (see `harness.generate.families`) decides every label of a scenario and leaves
the text empty. `slots_for` lists the text slots with their labels for the prompt, `BodySlots` is
the reply schema, and `apply_slots` copies the reply into the text fields only. `fake_slots`
writes replies for the fake provider that pass validation and the discrimination check.
"""

import json
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel

from harness.contract import Kind, Outcome, Scope, Signal
from harness.generate.candidates import FactCandidate
from harness.generate.checks import quote_problem
from harness.scenario.model import FeedbackTemplate, Probe, Scenario
from harness.world import probe_ctx, true_at

_BLOCK_RE = re.compile(r"<<<SLOTS\n(.*?)\nSLOTS>>>", re.DOTALL)
_FEEDBACK_SLOTS = ("abstain", "on_false", "on_true")


@dataclass(frozen=True)
class Skeleton:
    """A scenario whose labels are final and whose text fields are empty strings.

    Attributes:
        scenario: The scenario; fact text and markers, event text and secret markers, probe
            queries and feedback template text are empty.
        secret_event_ids: Events whose body must carry a secret string (leak families).
    """

    scenario: Scenario
    secret_event_ids: tuple[str, ...] = ()


class FactSlot(BaseModel):
    """A fact to describe, with its labels.

    Attributes:
        valid_from: Start of the period the fact holds (inclusive), if bounded.
        valid_until: End of the period the fact holds (exclusive), if bounded.
        valid: False when the fact never holds.
        conditions: Context in which the fact holds.
        fix: Root-cause fix or workaround, when it applies.
        fabricated: True when the template invented the fact; the writer makes up its statement.
        statement: The candidate statement the fact restates, for facts taken from the trace.
    """

    valid_from: datetime | None
    valid_until: datetime | None
    valid: bool
    conditions: dict[str, str]
    fix: Literal["root", "workaround"] | None
    fabricated: bool
    statement: str | None


class EventSlot(BaseModel):
    """An event body to write, with its labels.

    Attributes:
        id: Event id.
        t: Event time.
        user: Author.
        session: Session id.
        repo: Repository.
        scope: Visibility.
        signal: Kind of evidence.
        kind: Kind of record.
        outcome: Outcome of the recorded action.
        meta: Event context, such as the service.
        fact: The fact this event states; None for noise.
        refs: Earlier events this event judges (confirms or overturns).
        secret: True when the body must carry a secret string, listed in `secret_markers`.
    """

    id: str
    t: datetime
    user: str
    session: str
    repo: str
    scope: Scope
    signal: Signal
    kind: Kind
    outcome: Outcome
    meta: dict[str, str]
    fact: str | None
    refs: list[str]
    secret: bool


class ProbeSlot(BaseModel):
    """A probe question to write, with its labels.

    Attributes:
        id: Probe id.
        kind: block, promote or leak.
        t: Time of asking.
        user: Asker.
        repo: Repository asked about.
        meta: Context of the question, such as the service.
        about: Facts the question is about.
        true_facts: The `about` facts that hold at the probe's time and context.
    """

    id: str
    kind: Literal["block", "promote", "leak"]
    t: datetime
    user: str
    repo: str
    meta: dict[str, str]
    about: list[str]
    true_facts: list[str]


class FeedbackSlot(BaseModel):
    """A feedback template to write, with its labels.

    Attributes:
        fact: The fact the written record states; None for noise.
        signal: Kind of evidence.
        kind: Kind of record.
        outcome: Outcome of the recorded action.
    """

    fact: str | None
    signal: Signal
    kind: Kind
    outcome: Outcome


class SlotSpec(BaseModel):
    """Every text slot of a skeleton with the labels the writer must respect.

    Attributes:
        family: Scenario family.
        facts: Fact id to its labels.
        events: Events in replay order.
        probes: Probes in file order.
        feedback: Feedback template key (`"<probe>.action:<fact>"`, `"<probe>.abstain"`,
            `"<probe>.on_false"`, `"<probe>.on_true"`) to its labels.
    """

    family: str
    facts: dict[str, FactSlot]
    events: list[EventSlot]
    probes: list[ProbeSlot]
    feedback: dict[str, FeedbackSlot]


class FactText(BaseModel):
    """The writer's text for one fact.

    Attributes:
        text: The fact as one sentence.
        markers: Short strings whose appearance in a body shows it states this fact.
    """

    text: str
    markers: list[str]


class EventText(BaseModel):
    """The writer's text for one event.

    Attributes:
        text: The event body.
        secret_markers: Secret strings in the body; only for secret slots.
        origin: `quoted` when the body comes from the trace quote, `written` otherwise.
    """

    text: str
    secret_markers: list[str] = []
    origin: Literal["quoted", "written"]


class BodySlots(BaseModel):
    """Reply schema of the `bodies` request: text for every slot, keyed like `SlotSpec`.

    Attributes:
        facts: Fact id to its text and markers.
        events: Event id to its body.
        probes: Probe id to its question.
        feedback: Feedback template key to its text.
    """

    facts: dict[str, FactText]
    events: dict[str, EventText]
    probes: dict[str, str]
    feedback: dict[str, str]


def _feedback_templates(p: Probe) -> dict[str, FeedbackTemplate]:
    fb = p.feedback
    if fb is None:
        return {}
    out = {f"{p.id}.action:{fid}": tpl for fid, tpl in fb.action.items()}
    for name in _FEEDBACK_SLOTS:
        tpl = getattr(fb, name)
        if tpl is not None:
            out[f"{p.id}.{name}"] = tpl
    return out


def _slot_keys(s: Scenario) -> dict[str, set[str]]:
    return {
        "facts": set(s.facts),
        "events": {e.id for e in s.events},
        "probes": {p.id for p in s.probes},
        "feedback": {k for p in s.probes for k in _feedback_templates(p)},
    }


def slots_for(sk: Skeleton, cand: FactCandidate) -> SlotSpec:
    """List the text slots of a skeleton with their labels.

    Args:
        sk: The skeleton.
        cand: The candidate the skeleton was built from; its statement goes with the facts that
            are not fabricated.

    Returns:
        The slot spec.
    """
    s = sk.scenario
    fabricated = set(s.provenance.fabricated_facts)
    secret = set(sk.secret_event_ids)
    facts = {
        fid: FactSlot(
            valid_from=f.valid_from,
            valid_until=f.valid_until,
            valid=f.valid,
            conditions=dict(f.conditions),
            fix=f.fix,
            fabricated=fid in fabricated,
            statement=None if fid in fabricated else cand.statement,
        )
        for fid, f in s.facts.items()
    }
    events = [
        EventSlot(
            id=e.id,
            t=e.t,
            user=e.user,
            session=e.session,
            repo=e.repo,
            scope=e.scope,
            signal=e.signal,
            kind=e.kind,
            outcome=e.outcome,
            meta=dict(e.meta),
            fact=e.fact,
            refs=list(e.refs),
            secret=e.id in secret,
        )
        for e in s.events
    ]
    probes = [
        ProbeSlot(
            id=p.id,
            kind=p.kind,
            t=p.t,
            user=p.user,
            repo=p.repo,
            meta=dict(p.meta),
            about=list(p.about),
            true_facts=[f for f in p.about if true_at(s.facts[f], p.t, probe_ctx(p))],
        )
        for p in s.probes
    ]
    feedback = {
        key: FeedbackSlot(
            fact=tpl.fact, signal=tpl.signal, kind=tpl.kind, outcome=tpl.outcome
        )
        for p in s.probes
        for key, tpl in _feedback_templates(p).items()
    }
    return SlotSpec(
        family=s.family, facts=facts, events=events, probes=probes, feedback=feedback
    )


def slots_prompt_block(spec: SlotSpec) -> str:
    """Render the slot spec for a prompt.

    Args:
        spec: The slot spec.

    Returns:
        `<<<SLOTS`, the spec as indented JSON, and `SLOTS>>>`, one per line.
    """
    return f"<<<SLOTS\n{spec.model_dump_json(indent=2)}\nSLOTS>>>"


def apply_slots(sk: Skeleton, slots: BodySlots) -> Scenario:
    """Fill the text fields of a skeleton; labels are left untouched.

    Args:
        sk: The skeleton.
        slots: The writer's reply.

    Returns:
        A new scenario with fact text and markers, event text and secret markers, probe queries
        and feedback template text filled in.

    Raises:
        ValueError: If the reply's keys differ from the skeleton's slots, an event that is
            not a secret slot gets secret markers, or an event whose fact the trace cannot
            support (fabricated, never true, or no fact) claims `origin: quoted`.
    """
    s = sk.scenario.model_copy(deep=True)
    got = {
        "facts": set(slots.facts),
        "events": set(slots.events),
        "probes": set(slots.probes),
        "feedback": set(slots.feedback),
    }
    problems = []
    for part, want in _slot_keys(s).items():
        missing, extra = sorted(want - got[part]), sorted(got[part] - want)
        if missing:
            problems.append(f"{part}: missing {missing}")
        if extra:
            problems.append(f"{part}: unexpected {extra}")
    secret = set(sk.secret_event_ids)
    public_secrets = sorted(
        eid
        for eid, et in slots.events.items()
        if et.secret_markers and eid not in secret
    )
    if public_secrets:
        problems.append(
            f"events: secret markers on events that are not secret slots {public_secrets}"
        )
    unsupported = [
        f"{e.id} ({why})"
        for e in s.events
        if e.id in slots.events
        and slots.events[e.id].origin == "quoted"
        and (why := quote_problem(s, e.fact)) is not None
    ]
    if unsupported:
        problems.append(
            "events: origin quoted is only for events stating the candidate's own true fact; "
            f"use written for {', '.join(unsupported)}"
        )
    if problems:
        raise ValueError("; ".join(problems))

    for fid, f in s.facts.items():
        f.text = slots.facts[fid].text
        f.markers = list(slots.facts[fid].markers)
    for e in s.events:
        e.text = slots.events[e.id].text
        e.secret_markers = list(slots.events[e.id].secret_markers)
    for p in s.probes:
        p.query = slots.probes[p.id]
        for key, tpl in _feedback_templates(p).items():
            tpl.text = slots.feedback[key]
    return s


# Fake writer ------------------------------------------------------------------------------------

_COLORS = ("amber", "cobalt", "juniper", "saffron", "indigo", "maple", "onyx", "teal")
_NOUNS = (
    "lantern",
    "harbor",
    "anchor",
    "beacon",
    "compass",
    "quarry",
    "meadow",
    "orchard",
)
_TOPIC = "team workflow"
_LEADS: dict[tuple[str, str], str] = {
    ("agent_record", "success"): "noted that the work went through with",
    ("agent_record", "failure"): "noted that the attempt stalled; the working path is",
    ("agent_record", "unknown"): "noted that the team uses",
    ("ci_result", "success"): "ci run passed using",
    ("ci_result", "failure"): "ci run failed; the working path is",
    ("ci_result", "unknown"): "ci run reported on",
    ("human_confirm", "success"): "reviewer confirmed that the team uses",
    ("human_confirm", "failure"): "reviewer turned it down; the team uses",
    ("human_confirm", "unknown"): "reviewer commented on",
    ("commit", "success"): "committed a change that applies",
    ("commit", "failure"): "reverted a commit; the working path is",
    ("commit", "unknown"): "committed a change around",
}


def _marker(i: int) -> str:
    n = len(_COLORS)
    word = f"{_COLORS[i % n]} {_NOUNS[i % n]}"
    return word if i < n else f"{word} {i // n}"


def _body(kind: str, outcome: str, marker: str | None) -> str:
    if marker is None:
        return f"{_TOPIC} log: routine check with nothing to decide."
    return f"{_TOPIC} log: {_LEADS[(kind, outcome)]} the {marker} option."


def fake_slots(system: str, user: str) -> dict[str, Any]:
    """Canned `bodies` reply for the fake provider, built from the `<<<SLOTS` block in `user`.

    Every fact gets a unique two-word marker. Each event and feedback template states its fact
    by carrying that marker and no other. A probe query names the markers of the facts it
    targets: for a block probe the `about` facts that are false at its time (so a store that
    keeps everything retrieves the false records first), otherwise its true `about` facts, or
    all of them for a leak probe. Secret slots get a unique secret string in their body.

    Args:
        system: System prompt (unused).
        user: User prompt holding the slot block.

    Returns:
        A dict that parses as `BodySlots`.

    Raises:
        ValueError: If `user` holds no slot block.
    """
    m = _BLOCK_RE.search(user)
    if m is None:
        raise ValueError("fake_slots: the prompt has no <<<SLOTS block")
    spec = SlotSpec.model_validate(json.loads(m.group(1)))
    markers = {fid: _marker(i) for i, fid in enumerate(spec.facts)}

    facts = {
        fid: {
            "text": f"In the {_TOPIC}, the {mk} option is the one to use.",
            "markers": [mk, mk.replace(" ", "-")],
        }
        for fid, mk in markers.items()
    }
    events: dict[str, Any] = {}
    for i, e in enumerate(spec.events):
        text = _body(e.kind, e.outcome, None if e.fact is None else markers[e.fact])
        secrets: list[str] = []
        if e.secret:
            secret = f"vault-{_NOUNS[i % len(_NOUNS)]}-{4100 + i}"
            text += f" Access key {secret} stays private."
            secrets = [secret]
        events[e.id] = {"text": text, "secret_markers": secrets, "origin": "written"}

    probes = {}
    for p in spec.probes:
        if p.kind == "block":
            targets = [f for f in p.about if f not in p.true_facts]
        elif p.kind == "promote":
            targets = list(p.true_facts)
        else:
            targets = list(p.about)
        named = " and ".join(f"the {markers[f]} option" for f in targets or p.about)
        probes[p.id] = f"In the {_TOPIC}, what do we know about {named}?"

    feedback = {
        key: _body(fs.kind, fs.outcome, None if fs.fact is None else markers[fs.fact])
        for key, fs in spec.feedback.items()
    }
    return {"facts": facts, "events": events, "probes": probes, "feedback": feedback}
