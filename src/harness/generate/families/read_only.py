"""Templates of the six read-only families, one function per row of the design table (spec 5.3).

Each takes (candidate, traces of its bundle, seed) and returns a skeleton with empty text, or
None when the candidate does not fit. Rules shared by all of them (generation design 5.4):

- The candidate's statement is the fact the trace supports; the other fact of a pair is
  fabricated and listed in `provenance.fabricated_facts`.
- A CI result or human confirmation about earlier records is a judging record: it `refs` the
  records it judges and its `fact` is the fact it asserts as true. A record that first shows a
  fact has no `refs`.
- Independent sources are different users in different sessions on different days.
"""

import random
from collections.abc import Sequence
from typing import Literal

from harness.generate.candidates import FactCandidate
from harness.generate.families.base import (
    CONFIRMS,
    SkeletonBuilder,
    candidate_problem,
    confirm,
    repo_name,
)
from harness.generate.slots import Skeleton
from harness.generate.traces import Trace

_SUFFIXES = ("worker", "gateway", "batch", "admin")
_ENVS = ("prod", "staging", "dev")


def _probes_in_random_order(
    b: SkeletonBuilder,
) -> list[tuple[Literal["block", "promote"], int]]:
    """A block and a promote probe in seeded order, each on its own later day."""
    order: list[Literal["block", "promote"]] = ["block", "promote"]
    b.rng.shuffle(order)
    return [(kind, b.next_day()) for kind in order]


def normal_repeat(
    cand: FactCandidate, traces: Sequence[Trace], seed: int
) -> Skeleton | None:
    """One true fact seen by 3-4 different users, one of them a test/review/human confirmation.

    No block probe: this family is the promote-only counterpart of the others.
    """
    b = SkeletonBuilder("normal_repeat", cand, traces, seed)
    fact = b.fact("f1")
    n = 3 + b.rng.randint(0, 1)
    *observers, asker = b.users(n + 1)
    judge_at = b.rng.randint(1, n - 1)  # after at least one plain record
    for i, user in enumerate(observers):
        day = b.next_day()
        if i == judge_at:
            confirm(b, day, user, fact, b.events_of(fact))
        else:
            b.event(b.at(day), user, fact)
    b.probe("p_promote", "promote", b.at(b.next_day()), asker, [fact])
    return b.build()


def coincidence(
    cand: FactCandidate, traces: Sequence[Trace], seed: int
) -> Skeleton | None:
    """A false fact repeated three times in one session on one day; the true one from two sources."""
    b = SkeletonBuilder("coincidence", cand, traces, seed)
    true = b.fact("f_true")
    false = b.fact("f_false", fabricated=True, valid=False)
    burster, source, confirmer, asker = b.users(4)
    order = ["burst", "true", "true"]
    b.rng.shuffle(order)
    for slot in order:
        day = b.next_day()
        if slot == "burst":
            session = b.session()
            for t in b.times_on(day, 3):
                b.event(t, burster, false, session=session)
        elif not b.events_of(true):
            b.event(b.at(day), source, true)
        else:
            confirm(b, day, confirmer, true, b.events_of(true))
    for kind, day in _probes_in_random_order(b):
        b.probe(f"p_{kind}", kind, b.at(day), asker, [false, true])
    return b.build()


def single_source_poison(
    cand: FactCandidate, traces: Sequence[Trace], seed: int
) -> Skeleton | None:
    """One user injects a false fact over several sessions and days; other users confirm the true one."""
    b = SkeletonBuilder("single_source_poison", cand, traces, seed)
    true = b.fact("f_true")
    false = b.fact("f_false", fabricated=True, valid=False)
    poisoner, source, confirmer, asker = b.users(4)
    order = ["poison"] * (3 + b.rng.randint(0, 1)) + ["true", "true"]
    b.rng.shuffle(order)
    for slot in order:
        day = b.next_day()
        if slot == "poison":
            b.event(b.at(day), poisoner, false)
        elif not b.events_of(true):
            b.event(b.at(day), source, true)
        else:
            confirm(b, day, confirmer, true, b.events_of(true))
    for kind, day in _probes_in_random_order(b):
        b.probe(f"p_{kind}", kind, b.at(day), asker, [false, true])
    return b.build()


def truth_change(
    cand: FactCandidate, traces: Sequence[Trace], seed: int
) -> Skeleton | None:
    """`f_old` (the candidate) holds until day D, the fabricated `f_new` from D.

    A human confirmation on day D overturns the `f_old` records (it refs them and asserts
    `f_new`); another user records `f_new` later. The promote probe is asked before D, the
    block probe after.
    """
    b = SkeletonBuilder("truth_change", cand, traces, seed)
    first, second, confirmer, asker = b.users(4)
    writers = [first, second, b.rng.choice([first, second])]
    b.rng.shuffle(writers)
    for user in writers:
        b.event(b.at(b.next_day()), user, "f_old")
    promote_day = b.next_day()
    d = b.next_day()
    boundary = b.midnight(d)
    b.fact("f_old", valid_until=boundary)
    b.fact("f_new", fabricated=True, valid_from=boundary)
    b.event(
        b.at(d),
        confirmer,
        "f_new",
        signal="human",
        kind="human_confirm",
        outcome="success",
        refs=b.events_of("f_old"),
    )
    b.event(b.at(b.next_day()), b.rng.choice([first, second]), "f_new")
    b.probe("p_promote", "promote", b.at(promote_day), asker, ["f_old", "f_new"])
    b.probe("p_block", "block", b.at(b.next_day()), asker, ["f_old", "f_new"])
    return b.build()


def _other_value(rng: random.Random, key: str, value: str) -> str:
    if key == "env":
        return rng.choice([e for e in _ENVS if e != value])
    return f"{value}-{rng.choice(_SUFFIXES)}"


def _context(b: SkeletonBuilder, cond: dict[str, str]) -> tuple[str, dict[str, str]]:
    """Repository and meta of events and probes asked where `cond` holds."""
    return cond.get("repo", b.repo), {k: v for k, v in cond.items() if k != "repo"}


def conditional(
    cand: FactCandidate, traces: Sequence[Trace], seed: int
) -> Skeleton | None:
    """The candidate holds under condition A only; a fabricated fact holds under condition B.

    A is the candidate's condition, or `{"service": <repository name>}` when it has none. B
    changes one value of A (`service` when present). The block probe is asked under B, the
    promote probe under A.
    """
    b = SkeletonBuilder("conditional", cand, traces, seed)
    cond_a = dict(cand.condition) or {"service": repo_name(b.repo)}
    key = (
        "service"
        if "service" in cond_a
        else next((k for k in sorted(cond_a) if k != "repo"), "repo")
    )
    cond_b = {**cond_a, key: _other_value(b.rng, key, cond_a[key])}
    f_a = b.fact("f_a", conditions=cond_a)
    f_b = b.fact("f_b", fabricated=True, conditions=cond_b)
    (repo_a, meta_a), (repo_b, meta_b) = _context(b, cond_a), _context(b, cond_b)

    first, second, confirmer, other, asker = b.users(5)
    order = ["a", "a", "confirm"] if b.rng.random() < 0.5 else ["a", "confirm", "a"]
    order.insert(b.rng.randint(0, len(order)), "b")
    for slot in order:
        day = b.next_day()
        if slot == "b":
            b.event(b.at(day), other, f_b, repo=repo_b, meta=meta_b)
        elif slot == "a":
            user = second if b.events_of(f_a) else first
            b.event(b.at(day), user, f_a, repo=repo_a, meta=meta_a)
        else:
            signal, kind = b.rng.choice(CONFIRMS)
            b.event(
                b.at(day),
                confirmer,
                f_a,
                signal=signal,
                kind=kind,
                outcome="success",
                refs=b.events_of(f_a),
                repo=repo_a,
                meta=meta_a,
            )
    for kind, day in _probes_in_random_order(b):
        repo, meta = (repo_b, meta_b) if kind == "block" else (repo_a, meta_a)
        b.probe(f"p_{kind}", kind, b.at(day), asker, [f_a, f_b], repo=repo, meta=meta)
    return b.build()


def workaround_then_root(
    cand: FactCandidate, traces: Sequence[Trace], seed: int
) -> Skeleton | None:
    """A workaround is seen several times; on day R a root-cause fix lands and the workaround stops holding.

    The candidate is the workaround or the root fix according to its `fix`; the other one is
    fabricated. Candidates without `fix` do not fit. The promote probe is asked before R (the
    workaround is right then), the block probe after.
    """
    if candidate_problem("workaround_then_root", cand) is not None:
        return None
    b = SkeletonBuilder("workaround_then_root", cand, traces, seed)
    first, second, confirmer, fixer, checker, asker = b.users(6)
    for user in (first, second):
        b.event(b.at(b.next_day()), user, "f_workaround")
    signal, kind = b.rng.choice(CONFIRMS)
    b.event(
        b.at(b.next_day()),
        confirmer,
        "f_workaround",
        signal=signal,
        kind=kind,
        outcome="success",
        refs=b.events_of("f_workaround"),
    )
    promote_day = b.next_day()
    r = b.next_day()
    boundary = b.midnight(r)
    b.fact(
        "f_workaround",
        fabricated=cand.fix == "root",
        valid_until=boundary,
        fix="workaround",
    )
    b.fact(
        "f_root", fabricated=cand.fix == "workaround", valid_from=boundary, fix="root"
    )
    commit = b.event(
        b.at(r), fixer, "f_root", kind="commit", outcome="success"
    )  # first shows the fix
    b.event(
        b.at(b.next_day()),
        checker,
        "f_root",
        signal="test",
        kind="ci_result",
        outcome="success",
        refs=[commit],
    )
    b.probe(
        "p_promote", "promote", b.at(promote_day), asker, ["f_workaround", "f_root"]
    )
    b.probe("p_block", "block", b.at(b.next_day()), asker, ["f_workaround", "f_root"])
    return b.build()
