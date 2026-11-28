import json
from pathlib import Path

import pytest
from extbench import llm, longmemeval

from tests.bench.lme_fixture import lme_question, write_lme

PROMOTE_ALL = "engine:cortex_mem.engines.baseline:PromoteAllEngine"
EVIDENCE = "my cat is named Miso"
D1, D2 = "2023/05/20 (Sat) 02:21", "2023/05/27 (Sat) 10:00"


def entry(qid: str = "q1", qtype: str = "single-session-user") -> dict:
    sessions = [
        (D1, [("user", "I moved to Seoul last spring"), ("assistant", "How do you like it?")]),
        (D2, [("user", EVIDENCE), ("assistant", "Miso is a lovely name")]),
    ]
    return lme_question(qid, qtype, sessions, answer_idx=[1], question_date="2023/06/01 (Thu) 09:00")


class FakeBackend:
    """A model that answers from the prompt: the gold answer when the evidence is in it."""

    def __init__(self, role: str) -> None:
        self.role = role
        self.calls = self.hits = 0
        self.usage = {"input": 0, "output": 0, "seconds": 0.0}
        self.prompts: list[str] = []

    def describe(self) -> dict:
        return {"backend": "fake", "role": self.role}

    def ask(self, prompt: str) -> str:
        self.calls += 1
        self.prompts.append(prompt)
        self.usage["input"] += 1000
        self.usage["output"] += 10
        if self.role == "reader":
            return "synthetic answer" if EVIDENCE in prompt else "I do not know"
        return "Yes." if "Model Response: synthetic answer" in prompt else "no"


@pytest.fixture
def backends(monkeypatch: pytest.MonkeyPatch) -> dict[str, FakeBackend]:
    made: dict[str, FakeBackend] = {}

    def make(spec: str, root: Path, workers: int = 4, max_tokens: int | None = None, prefix: str = "EXTBENCH"):
        role = "judge" if prefix == "EXTBENCH_JUDGE" else "reader"
        made[role] = FakeBackend(role)
        return made[role]

    monkeypatch.setattr(longmemeval.llm, "make", make)
    monkeypatch.delenv("EXTBENCH_EMBED_URL", raising=False)
    return made


def run(tmp_path: Path, data: Path, *extra: str) -> dict:
    argv = ["--data", str(data), "--root", str(tmp_path / "bench"), "--model", "openai:reader",
            "--judge-model", "openai:judge", "--name", "r", *extra]
    out = longmemeval.main(argv)
    return json.loads((out / "summary.json").read_text())


def test_records_are_one_per_turn_with_date_role_and_ordered_times() -> None:
    # Act
    records, evidence = longmemeval.records_of(entry())

    # Assert
    assert [r.text for r in records] == [
        f"{D1} | user: I moved to Seoul last spring",
        f"{D1} | assistant: How do you like it?",
        f"{D2} | user: {EVIDENCE}",
        f"{D2} | assistant: Miso is a lovely name",
    ]
    assert [r.author for r in records] == ["user", "assistant", "user", "assistant"]
    assert all(a.at < b.at for a, b in zip(records, records[1:], strict=False))
    assert evidence == {"q1-sess-q1-1-0": f"{D2} | user: {EVIDENCE}"}


@pytest.mark.parametrize(
    ("qtype", "phrase"),
    [
        ("temporal-reasoning", "do not penalize off-by-one errors"),
        ("knowledge-update", "updated answer is the required answer"),
        ("single-session-preference", "Rubric: synthetic answer"),
        ("multi-session", "Correct Answer: synthetic answer"),
    ],
)
def test_judge_prompt_follows_the_question_type(qtype: str, phrase: str) -> None:
    # Act
    prompt = longmemeval.judge_prompt(entry(qtype=qtype), "a guess")

    # Assert
    assert phrase in prompt
    assert "Model Response: a guess" in prompt
    assert prompt.endswith("Answer yes or no only.")


def test_abstention_questions_get_the_abstention_prompt_whatever_their_type() -> None:
    # Act
    prompt = longmemeval.judge_prompt(entry("q9_abs", "temporal-reasoning"), "I cannot tell")

    # Assert
    assert prompt.startswith("I will give you an unanswerable question")
    assert "Explanation: synthetic answer" in prompt


def test_unknown_question_type_stops_the_run() -> None:
    # Act / Assert
    with pytest.raises(SystemExit, match="no judge prompt"):
        _ = longmemeval.judge_prompt(entry(qtype="made-up"), "x")


@pytest.mark.parametrize(("reply", "correct"), [("Yes.", True), ("yes", True), ("No.", False), ("", False)])
def test_a_reply_is_correct_when_it_says_yes(reply: str, correct: bool) -> None:
    # Act / Assert
    assert longmemeval.judged_correct(reply) is correct


def test_select_filters_by_type_and_id_and_keeps_file_order() -> None:
    # Arrange
    data = [entry("a", "multi-session"), entry("b", "temporal-reasoning"), entry("c", "multi-session")]

    # Act / Assert
    assert [e["question_id"] for e in longmemeval.select(data, {"multi-session"}, None, None)] == ["a", "c"]
    assert [e["question_id"] for e in longmemeval.select(data, None, {"c", "b"}, 1)] == ["b"]
    with pytest.raises(SystemExit, match="no such question"):
        _ = longmemeval.select(data, None, {"z"}, None)


def test_run_scores_each_arm_by_type_with_the_official_reading(
    tmp_path: Path, backends: dict[str, FakeBackend]
) -> None:
    # Arrange
    data = write_lme(tmp_path / "lme.json", [entry("q1"), entry("q2_abs", "temporal-reasoning")])

    # Act
    summary = run(tmp_path, data, "--arm", "none", "--arm", "full", "--arm", PROMOTE_ALL)

    # Assert
    arms = summary["arms"]
    assert arms["none"]["by_type"]["single-session-user"] == {"n": 1, "accuracy": 0.0}
    assert arms["full"]["by_type"]["single-session-user"] == {"n": 1, "accuracy": 1.0}
    assert arms["full"]["evidence_recall"] == 1.0
    assert arms[PROMOTE_ALL]["n"] == 2
    assert arms["full"]["abstention"]["n"] == 1
    assert summary["calls"] == {"reader": 6, "judge": 6}
    assert summary["core"]["commit"]
    assert all("Current Date: 2023/06/01 (Thu) 09:00" in p for p in backends["reader"].prompts)


def test_resume_keeps_finished_questions_without_asking_again(
    tmp_path: Path, backends: dict[str, FakeBackend]
) -> None:
    # Arrange
    data = write_lme(tmp_path / "lme.json", [entry("q1"), entry("q2")])
    first = run(tmp_path, data, "--arm", "full", "--only", "q1")
    answers = tmp_path / "bench" / "runs" / "r" / "answers.jsonl"
    earlier = tmp_path / "earlier.jsonl"
    _ = earlier.write_text(answers.read_text())

    # Act
    second = run(tmp_path, data, "--arm", "full", "--resume", str(earlier))

    # Assert
    assert first["arms"]["full"]["n"] == 1
    assert second["kept"] == [["full", "q1"]]
    assert second["calls"]["reader"] == 1
    assert second["arms"]["full"]["n"] == 2
    assert len(answers.read_text().splitlines()) == 2


def test_budget_skips_questions_that_would_pass_it(tmp_path: Path, backends: dict[str, FakeBackend]) -> None:
    # Arrange
    data = write_lme(tmp_path / "lme.json", [entry("q1"), entry("q2")])

    # Act: one question costs (1000 * 0.15 + 10 * 0.60) / 1e6 * 2 calls, about $0.0003
    summary = run(tmp_path, data, "--arm", "full", "--budget", "0.0002")

    # Assert
    assert summary["arms"]["full"]["n"] == 1
    assert summary["skipped_for_budget"] == [["full", "q2"]]


def test_a_judge_endpoint_falls_back_to_the_reader_one(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # Arrange
    monkeypatch.setenv("EXTBENCH_BASE_URL", "http://127.0.0.1:8080/v1")
    monkeypatch.setenv("EXTBENCH_API_KEY", "local")
    monkeypatch.delenv("EXTBENCH_JUDGE_API_KEY", raising=False)
    monkeypatch.setenv("EXTBENCH_JUDGE_BASE_URL", "http://127.0.0.1:8081/v1")

    # Act
    reader = llm.make("openai:qwen", tmp_path)
    judge = llm.make("openai:gemma", tmp_path, prefix="EXTBENCH_JUDGE")

    # Assert
    assert reader.describe()["base_url"] == "http://127.0.0.1:8080/v1"
    assert judge.describe()["base_url"] == "http://127.0.0.1:8081/v1"


def test_a_missing_endpoint_says_what_to_set(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # Arrange
    for name in ("EXTBENCH_BASE_URL", "EXTBENCH_JUDGE_BASE_URL"):
        monkeypatch.delenv(name, raising=False)

    # Act / Assert
    with pytest.raises(SystemExit, match="EXTBENCH_JUDGE_BASE_URL"):
        _ = llm.make("openai:gemma", tmp_path, prefix="EXTBENCH_JUDGE")


def test_locomo_judge_takes_a_local_model_or_a_bare_claude_name(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # Arrange
    from extbench import judge

    monkeypatch.setenv("EXTBENCH_BASE_URL", "http://127.0.0.1:8080/v1")
    monkeypatch.setenv("EXTBENCH_API_KEY", "local")
    monkeypatch.setenv("EXTBENCH_JUDGE_BASE_URL", "http://127.0.0.1:8081/v1")

    # Act
    local = judge.backend("openai:gemma", tmp_path, 2)
    cli = judge.backend("claude-sonnet-5-5", tmp_path, 2)

    # Assert
    assert local.describe()["base_url"] == "http://127.0.0.1:8081/v1"
    assert local.system == judge.SYSTEM
    assert isinstance(cli, llm.ClaudeCli) and cli.model == "claude-sonnet-5-5" and cli.system == judge.SYSTEM
