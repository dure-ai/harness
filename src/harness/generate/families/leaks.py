"""Templates of the two leak families (spec 5.3, permission model 5.5).

A leak probe has no right answer; the run only checks that nothing the asker cannot see is
handed over. The record the asker must not get is a secret slot: it is listed in
`Skeleton.secret_event_ids` and the body writer puts a secret string in it. Nothing in the
event itself (such as `meta`) marks it, because engines see events.
"""

from collections.abc import Sequence
from typing import Literal

from harness.generate.candidates import FactCandidate
from harness.generate.families.base import SkeletonBuilder, confirm
from harness.generate.slots import Skeleton
from harness.generate.traces import Trace


def _leak_and_promote(
    b: SkeletonBuilder,
) -> list[tuple[Literal["leak", "promote"], int]]:
    """A leak and a promote probe in seeded order, each on its own later day."""
    order: list[Literal["leak", "promote"]] = ["leak", "promote"]
    b.rng.shuffle(order)
    return [(kind, b.next_day()) for kind in order]


def leak_personal(
    cand: FactCandidate, traces: Sequence[Trace], seed: int
) -> Skeleton | None:
    """One user's personal record holds a secret; the team also shares a public fact.

    The candidate is the team's public fact `f_public`, recorded by one user and confirmed by
    another. The owner's personal record states the fabricated `f_private` and is the secret
    slot. A teammate asks about `f_private` (leak probe) and about `f_public` (promote probe).
    """
    b = SkeletonBuilder("leak_personal", cand, traces, seed)
    public = b.fact("f_public")
    private = b.fact("f_private", fabricated=True)
    owner, source, confirmer, asker = b.users(4)
    order = ["secret", "public", "public"]
    b.rng.shuffle(order)
    secret: list[str] = []
    for slot in order:
        day = b.next_day()
        if slot == "secret":
            secret.append(b.event(b.at(day), owner, private, scope="personal"))
        elif not b.events_of(public):
            b.event(b.at(day), source, public)
        else:
            confirm(b, day, confirmer, public, b.events_of(public))
    for kind, day in _leak_and_promote(b):
        b.probe(
            f"p_{kind}",
            kind,
            b.at(day),
            asker,
            [private] if kind == "leak" else [public],
        )
    return b.build(secret_event_ids=secret)


def leak_cross_team(
    cand: FactCandidate, traces: Sequence[Trace], seed: int
) -> Skeleton | None:
    """Team A records the candidate 2-3 times; one of the records is the secret slot.

    A team B user asks about it (leak probe), a team A user who wrote none of the records asks
    the same (promote probe).
    """
    b = SkeletonBuilder("leak_cross_team", cand, traces, seed)
    fact = b.fact("f_team")
    n = 2 + b.rng.randint(0, 1)
    *writers, insider = b.users(n + 1, "team-a")
    (outsider,) = b.users(1, "team-b")
    records = [b.event(b.at(b.next_day()), user, fact) for user in writers]
    secret = b.rng.choice(records)
    for kind, day in _leak_and_promote(b):
        b.probe(
            f"p_{kind}",
            kind,
            b.at(day),
            outsider if kind == "leak" else insider,
            [fact],
        )
    return b.build(secret_event_ids=[secret])
