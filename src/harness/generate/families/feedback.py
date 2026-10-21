"""Templates of the two feedback families (spec 5.3, feedback mode 6.2).

Every probe carries feedback templates with empty text: an action record per `about` fact
(its `fact` is the fact followed), an abstain record (the agent explored on its own and found
what is true now) and an `on_false` CI failure after acting on a wrong basis. Delays are set
on the templates (`ACTION_DELAY`, `FAILURE_DELAY`) rather than left to the run config, and
successive block probes are spaced further apart than `FAILURE_DELAY`, so the failure record
after one block probe is always in before the next.
"""

from collections.abc import Sequence
from datetime import timedelta

from harness.generate.candidates import FactCandidate
from harness.generate.families.base import SkeletonBuilder, confirm
from harness.generate.slots import Skeleton
from harness.generate.traces import Trace
from harness.scenario.model import Feedback, FeedbackTemplate

ACTION_DELAY = timedelta(minutes=5)
"""Delay of the action and abstain records after the probe (spec 6.2)."""
FAILURE_DELAY = timedelta(days=1)
"""Delay of the `on_false` failure record after the probe (spec 6.2)."""
_BLOCK_GAP_DAYS = (2, 3)
"""Day gap between successive block probes; at least 2 days exceeds `FAILURE_DELAY` whatever
the times of day (09:00-17:45)."""


def _feedback(about: list[str], found: str, failure_fact: str | None) -> Feedback:
    """Feedback templates for a probe about `about`.

    Args:
        about: The probe's facts; each gets an action record stating it.
        found: Fact of the abstain record (what direct exploration finds true).
        failure_fact: Fact the CI failure record asserts as true, or None for a bare failure.

    Returns:
        The templates, text left empty, with explicit delays.
    """
    return Feedback(
        action={fid: FeedbackTemplate(text="", fact=fid, delay=ACTION_DELAY) for fid in about},
        abstain=FeedbackTemplate(text="", fact=found, delay=ACTION_DELAY),
        on_false=FeedbackTemplate(
            text="", fact=failure_fact, delay=FAILURE_DELAY, signal="test", kind="ci_result",
            outcome="failure",
        ),
    )


def _block_probes(b: SkeletonBuilder, n: int, asker: str, about: list[str], fb: Feedback) -> None:
    """`n` block probes by `asker`: the first on the next day, the others 2-3 days after the previous."""
    for i in range(n):
        day = b.next_day() if i == 0 else b.next_day(*_BLOCK_GAP_DAYS)
        b.probe(f"p_block{i + 1}", "block", b.at(day), asker, about, feedback=fb)


def echo(cand: FactCandidate, traces: Sequence[Trace], seed: int) -> Skeleton | None:
    """A fabricated false fact is recorded early, then asked about 3-4 times; every answer is written back.

    One user records `f_false` once or twice; afterwards another user records the candidate
    `f_true` and a third confirms it. The same asker then gets 3-4 block probes, each 2-3
    days after the previous, and a final promote probe, all about both facts. Acting on
    `f_false` writes an action record stating it again (the echo, after 5 minutes) and a bare
    CI failure (`fact: null`) a day later, in before the next probe.
    """
    b = SkeletonBuilder("echo", cand, traces, seed)
    true = b.fact("f_true")
    false = b.fact("f_false", fabricated=True, valid=False)
    writer, source, confirmer, asker = b.users(4)
    for _ in range(1 + b.rng.randint(0, 1)):
        b.event(b.at(b.next_day()), writer, false)
    b.event(b.at(b.next_day()), source, true)
    confirm(b, b.next_day(), confirmer, true, b.events_of(true))
    about = [false, true]
    fb = _feedback(about, found=true, failure_fact=None)
    _block_probes(b, 3 + b.rng.randint(0, 1), asker, about, fb)
    b.probe("p_promote", "promote", b.at(b.next_day()), asker, about, feedback=fb)
    return b.build()


def failure_feedback(cand: FactCandidate, traces: Sequence[Trace], seed: int) -> Skeleton | None:
    """The `truth_change` world asked in feedback mode: acting on `f_old` after day D fails in CI.

    `f_old` (the candidate) holds until D, the fabricated `f_new` from D; a human confirmation
    on D overturns the `f_old` records. 2-3 block probes follow D, each 2-3 days after the
    previous one, so the previous probe's failure record (delay 1 day, set on the template) is
    in before it. The failure record says what is true now (`fact: f_new`). Another user then
    records `f_new` and a final promote probe asks about the pair.
    """
    b = SkeletonBuilder("failure_feedback", cand, traces, seed)
    first, second, confirmer, later, asker = b.users(5)
    writers = [first, second, b.rng.choice([first, second])]
    b.rng.shuffle(writers)
    for user in writers:
        b.event(b.at(b.next_day()), user, "f_old")
    d = b.next_day()
    boundary = b.midnight(d)
    b.fact("f_old", valid_until=boundary)
    b.fact("f_new", fabricated=True, valid_from=boundary)
    b.event(
        b.at(d), confirmer, "f_new", signal="human", kind="human_confirm", outcome="success",
        refs=b.events_of("f_old"),
    )
    about = ["f_old", "f_new"]
    fb = _feedback(about, found="f_new", failure_fact="f_new")
    _block_probes(b, 2 + b.rng.randint(0, 1), asker, about, fb)
    b.event(b.at(b.next_day()), later, "f_new")
    b.probe("p_promote", "promote", b.at(b.next_day()), asker, about, feedback=fb)
    return b.build()
