"""LongMemEval-S (MIT): long chat histories converted into `ext_update` and `ext_recall` scenarios."""

import json
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from importlib import resources
from pathlib import Path
from typing import Any

import yaml

from harness.bench.convert import Clock
from harness.bench.dataset import Converted, RemoteFile
from harness.scenario.model import ExtFamily, Fact, Probe, Provenance, Scenario, ScenarioEvent, World

REPO = "longmemeval-s"
KU = "knowledge-update"
FACT_CHARS = 300
# Pinned to a dataset revision, so a later upstream change does not make the checksum fail.
_REVISION = "98d7416c24c778c2fee6e6f3006e7a073259d48f"
_URL = f"https://huggingface.co/datasets/xiaowu0162/longmemeval-cleaned/resolve/{_REVISION}/longmemeval_s_cleaned.json"
_SHA256 = "d6f21ea9d60a0d56f34a05b609c79c88a451d2ae03597821ea3d5a9678c3a442"


def parse_lme_date(text: str) -> datetime:
    """Parse `"2023/05/20 (Sat) 02:21"` as a UTC time (the weekday is ignored)."""
    day, _weekday, clock = text.split()
    return datetime.strptime(f"{day} {clock}", "%Y/%m/%d %H:%M").replace(tzinfo=UTC)


LABELS = ("new", "old", "both", "exclude")
LABEL_FILE = "longmemeval_ku_labels.yaml"


def parse_ku_labels(text: str) -> dict[str, str]:
    """Parse a label file: question id to one of `LABELS`.

    Raises:
        ValueError: A label is not one of `LABELS`, naming the id and the value.
    """
    labels = {str(k): str(v) for k, v in yaml.safe_load(text).items()}
    for qid, label in labels.items():
        if label not in LABELS:
            raise ValueError(f"knowledge-update label of {qid} is {label!r}; use one of {', '.join(LABELS)}")
    return labels


def load_ku_labels() -> dict[str, str]:
    """The knowledge-update labels shipped with the package: question id to new/old/both/exclude."""
    return parse_ku_labels(
        (resources.files("harness.bench.datasets") / LABEL_FILE).read_text(encoding="utf-8")
    )


@dataclass(frozen=True, slots=True)
class _Session:
    sid: str
    t: datetime
    turns: list[dict[str, Any]]


def convert_question(q: dict[str, Any], labels: Mapping[str, str]) -> Converted:
    """Convert one LongMemEval record; a question that cannot be converted soundly is skipped with a reason."""
    qid: str = q["question_id"]
    qtype: str = q["question_type"]

    def skip(reason: str) -> Converted:
        return Converted(None, qid, qtype, reason)

    if qid.endswith("_abs"):
        return skip("abstention")
    label = None
    if qtype == KU:
        label = labels.get(qid)
        if label is None:
            return skip("no label")
        if label == "exclude":
            return skip("excluded by label")

    qdate = parse_lme_date(q["question_date"])
    answer_ids = set(q["answer_session_ids"])
    sessions: list[_Session] = []
    for sid, date, turns in zip(q["haystack_session_ids"], q["haystack_dates"], q["haystack_sessions"], strict=True):
        t = parse_lme_date(date)
        if t >= qdate:  # a session at the question's time has no turn before the probe either
            if sid in answer_ids:
                return skip("evidence session at or after the question")
            continue
        sessions.append(_Session(sid, t, turns))
    sessions.sort(key=lambda s: s.t)  # stable: equal dates keep file order

    events: list[ScenarioEvent] = []
    facts: dict[str, Fact] = {}
    first_evidence: dict[str, datetime] = {}
    clock = Clock()
    for si, sess in enumerate(sessions):
        is_answer = sess.sid in answer_ids
        evidence = [turn["content"] for turn in sess.turns if is_answer and turn.get("has_answer")]
        if is_answer:
            if not evidence:
                return skip("answer session without evidence turns")
            facts[f"f{si}"] = Fact(text="\n".join(evidence)[:FACT_CHARS])
        for ti, turn in enumerate(sess.turns):
            fact = f"f{si}" if is_answer and turn.get("has_answer") else None
            # Turns are a minute apart, so a session that starts just before the question can run
            # past it; such turns would come after the probe. Dropped turns do not move the clock.
            if clock.peek(sess.t + timedelta(minutes=ti)) >= qdate:
                if fact is not None:
                    return skip("evidence turn pushed past the question by turn spacing")
                continue
            t = clock.at(sess.t + timedelta(minutes=ti))
            if fact is not None:
                first_evidence.setdefault(fact, t)
            events.append(
                ScenarioEvent(
                    id=f"s{si}-t{ti}",
                    t=t,
                    user="u1" if turn["role"] == "user" else "assistant",
                    session=f"sess-{si}",
                    repo=REPO,
                    scope="team",
                    signal="self",
                    kind="agent_record",
                    outcome="unknown",
                    text=turn["content"],
                    meta={"role": turn["role"]},
                    fact=fact,
                )
            )

    ids = list(facts)  # evidence sessions in date order
    if label is not None and len(ids) != 2:
        return skip("knowledge-update without exactly two evidence sessions")

    def promote(pid: str, session: str, about: list[str]) -> Probe:
        return Probe(
            id=pid, kind="promote", t=qdate, user="u1", session=session, repo=REPO, query=q["question"], about=about
        )

    # Probe sessions continue the event numbering, so no session id tells a block probe from a promote probe.
    next_session = len(sessions)
    family: ExtFamily
    match label:
        case "new":  # the new value is the answer: acting on the old one is a block FP
            old, new = ids[0], ids[-1]
            facts[old].valid_until = first_evidence[new]
            probes = [
                Probe(
                    id="p-block", kind="block", t=qdate, user="u1", session=f"sess-{next_session}", repo=REPO,
                    query=q["question"], about=[old, new],
                ),
                promote("p-promote", f"sess-{next_session + 1}", [new]),
            ]
            family = "ext_update"
        case "old":  # the question asks about the earlier value
            probes, family = [promote("p-promote", f"sess-{next_session}", ids[:1])], "ext_recall"
        case "both":
            probes, family = [promote("p-promote", f"sess-{next_session}", [ids[0], ids[-1]])], "ext_recall"
        case _:  # not a knowledge-update question: every evidence session is the answer
            probes, family = [promote("p-promote", f"sess-{next_session}", ids)], "ext_recall"

    scenario = Scenario(
        id=f"lme-{qid}",
        family=family,
        mode="read_only",
        language="en",
        world=World(timezone="UTC", teams={"t1": ["u1", "assistant"]}),
        facts=facts,
        events=events,
        probes=probes,
        provenance=Provenance(author="generator", source_traces=[qid]),
    )
    return Converted(scenario, qid, qtype, None)


class LongMemEvalS:
    """LongMemEval-S, cleaned version (500 questions, about 115k tokens of history each)."""

    name: str = "longmemeval-s"
    license: str = "MIT"
    commit_ok: bool = False  # converted scenarios hold the dataset's text; keep exports git-ignored
    converter_version: int = 1
    label_file: str | None = LABEL_FILE

    def files(self) -> list[RemoteFile]:
        """The one JSON file of the dataset."""
        return [RemoteFile("longmemeval_s_cleaned.json", _URL, 277383467, _SHA256)]

    def convert(self, cache: Path, *, types: set[str] | None, limit: int | None) -> Iterator[Converted]:
        """Convert the cached file; `types` filters by `question_type`, `limit` counts converted and skipped items."""
        records: list[dict[str, Any]] = json.loads((cache / self.files()[0].name).read_bytes())
        labels = load_ku_labels()
        n = 0
        for q in records:
            if types is not None and q["question_type"] not in types:
                continue
            if limit is not None and n >= limit:
                return
            n += 1
            yield convert_question(q, labels)
