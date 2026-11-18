"""Move an approved scenario to the option-B confirmation (#25).

The old shape had one successful `human_confirm` that asserted `f_new` while its `refs` pointed at
`f_old` events. Option B splits it: a failure confirmation of the same id and time (no fact), and
a separate human record of `f_new` 30 minutes later that keeps the reviewed text.
"""

from datetime import timedelta

from harness.scenario.labels import fingerprint_state, label_fingerprint
from harness.scenario.model import Review, Scenario, ScenarioEvent

OVERTURN_GAP = timedelta(minutes=30)
FAILURE_TEXT = "Review on {refs_text}: please don't follow these steps; see my follow-up note."
REVIEW_NOTE = "11/18 #25 옵션 B로 옮김(확인 이벤트를 실패 확인 + 사람 기록으로 나눔)"


def _refs_text(refs: list[str]) -> str:
    return ", ".join(refs)


def _is_old_confirmation(s: Scenario, e: ScenarioEvent) -> bool:
    if e.kind != "human_confirm" or e.outcome != "success" or e.fact is None or not e.refs:
        return False
    by_id = {x.id: x for x in s.events}
    judged = {by_id[r].fact for r in e.refs if r in by_id}
    return e.fact not in judged


def _next_id(s: Scenario) -> str:
    used = {e.id for e in s.events}
    numbers = [int(e.id[1:]) for e in s.events if e.id[:1] == "e" and e.id[1:].isdigit()]
    n = max(numbers, default=0) + 1
    while f"e{n}" in used:
        n += 1
    return f"e{n}"


def overturn_option_b(s: Scenario) -> Scenario:
    """Return `s` with its old-shape confirmation split into a failure confirmation and a record.

    Args:
        s: A scenario with exactly one successful `human_confirm` that asserts a fact other than
            the one(s) of the events it refs.

    Returns:
        A new scenario (`s` is not modified), as a draft needing re-approval.

    Raises:
        ValueError: If `s` has not exactly one old-shape confirmation, or the new record would
            come after the next event or at or after the next probe (a probe after the confirmation).
    """
    was = fingerprint_state(s)
    if was == "changed":
        raise ValueError(f"scenario {s.id}: labels were changed outside review; refusing to migrate")
    old = [i for i, e in enumerate(s.events) if _is_old_confirmation(s, e)]
    if len(old) != 1:
        raise ValueError(f"scenario {s.id}: expected exactly one old-shape confirmation, found {len(old)}")
    idx = old[0]
    conf = s.events[idx]
    new_t = conf.t + OVERTURN_GAP
    if idx + 1 < len(s.events) and new_t > s.events[idx + 1].t:
        raise ValueError(f"scenario {s.id}: the new record at {new_t} would come after event {s.events[idx + 1].id}")
    after = [p for p in s.probes if p.t >= conf.t]  # probes before the confirmation never see it
    if after and new_t >= min(p.t for p in after):
        raise ValueError(f"scenario {s.id}: the new record at {new_t} would not come before the next probe")

    record = ScenarioEvent(
        id=_next_id(s),
        t=new_t,
        user=conf.user,
        session=conf.session,
        repo=conf.repo,
        scope=conf.scope,
        team=conf.team,
        signal="human",
        kind="agent_record",
        outcome="unknown",
        text=conf.text,
        meta=dict(conf.meta),
        refs=[],
        fact=conf.fact,
        secret_markers=list(conf.secret_markers),
    )
    failure = conf.model_copy(
        update={
            "outcome": "failure",
            "fact": None,
            "secret_markers": [],
            "text": FAILURE_TEXT.format(refs_text=_refs_text(conf.refs)),
        }
    )
    events = [*s.events[:idx], failure, record, *s.events[idx + 1 :]]
    review = Review(
        status="draft",
        reviewer=None,
        notes=f"{s.review.notes} | {REVIEW_NOTE}" if s.review.notes else REVIEW_NOTE,
        approved_hash=None,
        check_config_hash=None,
    )
    provenance = s.provenance.model_copy(update={"reviewer_edited": True})
    out = s.model_copy(update={"events": events, "review": review, "provenance": provenance})
    if was == "verified":  # keep it approvable: the labels moved on purpose, so re-record them
        out.provenance.label_fingerprint = label_fingerprint(out)
    return out
