"""LongMemEval-S question answering (Wu et al., ICLR 2025) with a memory arm in front of the reader.

Each question carries its own chat history (about 50 sessions). For every arm and question the
history is stored afresh, the memory is asked for `k` texts, a reader answers from them, and a
judge model labels the answer, as the official evaluation does. Nothing is converted into
harness scenarios.

Kept from the official code (xiaowu0162/LongMemEval, MIT): the reader prompt for retrieved
history without chain of thought (src/generation/run_generation.py: temperature 0, at most 500
tokens), and the judge prompts per question type with the abstention prompt for `_abs` questions
(src/evaluation/evaluate_qa.py: temperature 0, at most 10 tokens, correct when the reply contains
"yes"). Accuracy per type groups questions as the official script does, abstention questions
included in their type; abstention is also reported on its own.

Deviations, recorded in each summary: the reader and judge get a short system message (the
backends always send one), and the history is the texts the arm hands over, one line per turn
(`<session date> | <role>: <content>`), rather than the official JSON per session.
"""

import argparse
import json
import time
from collections import defaultdict
from datetime import timedelta
from pathlib import Path

from . import embedding, llm, memory

READER_PROMPT = (
    "I will give you several history chats between you and a user. Please answer the question based on "
    "the relevant chat history.\n\n\nHistory Chats:\n\n{}\n\nCurrent Date: {}\nQuestion: {}\nAnswer:"
)
_STANDARD = (
    "I will give you a question, a correct answer, and a response from a model. Please answer yes if the "
    "response contains the correct answer. Otherwise, answer no. If the response is equivalent to the correct "
    "answer or contains all the intermediate steps to get the correct answer, you should also answer yes. If the "
    "response only contains a subset of the information required by the answer, answer no."
)
_QA = (
    "\n\nQuestion: {}\n\nCorrect Answer: {}\n\nModel Response: {}\n\n"
    "Is the model response correct? Answer yes or no only."
)
JUDGE_PROMPTS = {
    "single-session-user": _STANDARD + " " + _QA,
    "single-session-assistant": _STANDARD + " " + _QA,
    "multi-session": _STANDARD + " " + _QA,
    "temporal-reasoning": _STANDARD + " In addition, do not penalize off-by-one errors for the number of days. "
    "If the question asks for the number of days/weeks/months, etc., and the model makes off-by-one errors "
    "(e.g., predicting 19 days when the answer is 18), the model's response is still correct. " + _QA,
    "knowledge-update": "I will give you a question, a correct answer, and a response from a model. Please answer "
    "yes if the response contains the correct answer. Otherwise, answer no. If the response contains some previous "
    "information along with an updated answer, the response should be considered as correct as long as the "
    "updated answer is the required answer." + _QA,
    "single-session-preference": "I will give you a question, a rubric for desired personalized response, and a "
    "response from a model. Please answer yes if the response satisfies the desired response. Otherwise, answer "
    "no. The model does not need to reflect all the points in the rubric. The response is correct as long as it "
    "recalls and utilizes the user's personal information correctly.\n\nQuestion: {}\n\nRubric: {}\n\nModel "
    "Response: {}\n\nIs the model response correct? Answer yes or no only.",
}
ABSTENTION_PROMPT = (
    "I will give you an unanswerable question, an explanation, and a response from a model. Please answer yes if "
    "the model correctly identifies the question as unanswerable. The model could say that the information is "
    "incomplete, or some other information is given but the asked information is not.\n\nQuestion: {}\n\n"
    "Explanation: {}\n\nModel Response: {}\n\nDoes the model correctly identify the question as unanswerable? "
    "Answer yes or no only."
)
READER_TOKENS, JUDGE_TOKENS = 500, 10
ABSTENTION = "abstention"
BOUND_ARMS = ("none", "full")  # their handed texts are not worth a copy per question


def is_abstention(entry: dict) -> bool:
    """Whether a question has no answer in its history (the official `_abs` id suffix)."""
    return entry["question_id"].endswith("_abs")


def records_of(entry: dict) -> tuple[list[memory.Record], dict[str, str]]:
    """Turn a question's history into records, one per turn, and map evidence record ids to their texts.

    A turn's time is its session's date plus one second per turn, so the order within a session holds.
    """
    from harness.bench.datasets.longmemeval import parse_lme_date

    out: list[memory.Record] = []
    evidence: dict[str, str] = {}
    sessions = zip(entry["haystack_session_ids"], entry["haystack_dates"], entry["haystack_sessions"], strict=True)
    for sid, date, turns in sessions:
        start = parse_lme_date(date)
        for t, turn in enumerate(turns):
            rid = f"{entry['question_id']}-{sid}-{t}".replace(":", "_")
            text = f"{date} | {turn['role']}: {turn['content']}"
            out.append(memory.Record(rid, text, turn["role"], start + timedelta(seconds=t)))
            if turn.get("has_answer"):
                evidence[rid] = text
    return out, evidence


def reader_prompt(entry: dict, handed: list[str]) -> str:
    """The official no-CoT reader prompt over the texts an arm handed over."""
    return READER_PROMPT.format("\n".join(handed), entry["question_date"], entry["question"])


def judge_prompt(entry: dict, prediction: str) -> str:
    """The official judge prompt for the question's type, or the abstention one.

    Raises:
        SystemExit: If the type has no official prompt.
    """
    if is_abstention(entry):
        return ABSTENTION_PROMPT.format(entry["question"], entry["answer"], prediction)
    template = JUDGE_PROMPTS.get(entry["question_type"])
    if template is None:
        raise SystemExit(f"no judge prompt for question type {entry['question_type']!r}")
    return template.format(entry["question"], entry["answer"], prediction)


def judged_correct(reply: str) -> bool:
    """The official reading of a judge reply."""
    return "yes" in reply.lower()


def evidence_recall(mem, handed: list[str], evidence: dict[str, str]) -> float | None:
    """Share of the evidence turns the arm handed over; None when the question marks none."""
    if not evidence:
        return None
    if hasattr(mem, "sources"):  # tools rewrite what they store; go by the record ids they report
        got = set(mem.sources)
        hits = [rid in got for rid in evidence]
    else:
        hits = [any(text in h for h in handed) for text in evidence.values()]
    return sum(hits) / len(hits)


def select(data: list[dict], types: set[str] | None, only: set[str] | None, limit: int | None) -> list[dict]:
    """The questions to run, in file order.

    Raises:
        SystemExit: If `only` names a question that is not in the data.
    """
    if only and (missing := only - {e["question_id"] for e in data}):
        raise SystemExit(f"no such question: {sorted(missing)}")
    chosen = [e for e in data if (not types or e["question_type"] in types) and (not only or e["question_id"] in only)]
    return chosen[:limit]


def _rate(rows: list[dict]) -> dict:
    n = len(rows)
    return {"n": n, "accuracy": round(sum(r["correct"] for r in rows) / n, 4) if n else None}


def summarize(rows: list[dict], arms: list[str]) -> dict:
    """Accuracy per arm, overall, per question type (official grouping) and for abstention questions."""
    out = {}
    for arm in arms:
        mine = [r for r in rows if r["arm"] == arm]
        by_type = defaultdict(list)
        for r in mine:
            by_type[r["question_type"]].append(r)
        recalls = [r["evidence_recall"] for r in mine if r["evidence_recall"] is not None]
        out[arm] = {**_rate(mine), "by_type": {t: _rate(v) for t, v in sorted(by_type.items())},
                    ABSTENTION: _rate([r for r in mine if r[ABSTENTION]]),
                    "evidence_recall": round(sum(recalls) / len(recalls), 4) if recalls else None}
    return out


def load(path: str | None) -> tuple[list[dict], dict]:
    """The questions, from `path` or from the harness bench cache after its sha256 check.

    Raises:
        SystemExit: If no path is given and the dataset was not downloaded.
    """
    if path:
        return json.loads(Path(path).read_text(encoding="utf-8")), {"path": path, "sha256": None}
    from harness.bench.dataset import bench_cache, verify_cached
    from harness.bench.datasets import DATASETS

    dataset = DATASETS["longmemeval-s"]
    file = dataset.files()[0]
    data_path = bench_cache(dataset.name) / file.name
    if not data_path.is_file():
        raise SystemExit("LongMemEval-S is not downloaded: uv run harness bench fetch --dataset longmemeval-s")
    digests = verify_cached(dataset)
    return json.loads(data_path.read_text(encoding="utf-8")), {"path": str(data_path), "sha256": digests[file.name]}


def read_prior(path: str | None) -> dict[tuple[str, str], dict]:
    """Rows of an earlier run's answers.jsonl, by (arm, question id)."""
    if not path:
        return {}
    with open(path, encoding="utf-8") as f:
        rows = [json.loads(line) for line in f if line.strip()]
    return {(r["arm"], r["question_id"]): r for r in rows}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """The command line; see the README for what each option does."""
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=None, help="LongMemEval JSON; default: the harness bench cache")
    ap.add_argument("--root", required=True, help="bench directory (cache/, cwd/, runs/)")
    ap.add_argument("--model", required=True, help="reader: openai:<model> or claude-cli:<model>")
    ap.add_argument("--judge-model", required=True,
                    help="judge: openai:<model> (EXTBENCH_JUDGE_BASE_URL, else EXTBENCH_BASE_URL) or claude-cli:<model>")
    ap.add_argument("--arm", action="append", required=True)
    ap.add_argument("--k", type=int, default=20)
    ap.add_argument("--types", default=None, help="comma-separated question types")
    ap.add_argument("--only", default=None, help="comma-separated question ids")
    ap.add_argument("--questions", type=int, default=None, help="first N questions after the filters")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--name", default=None)
    ap.add_argument("--resume", default=None, help="answers.jsonl of an earlier run; its questions are kept")
    ap.add_argument("--budget", type=float, default=None,
                    help="stop before a question that would pass this (USD, estimated)")
    ap.add_argument("--per-record", type=float, default=0.001, help="estimated USD a tool spends storing one record")
    ap.add_argument("--price", default="0.15,0.60", help="reader and judge USD per million input,output tokens")
    return ap.parse_args(argv)


class Run:
    """One invocation: the backends, the outputs, and what was spent, kept and skipped."""

    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.root = Path(args.root)
        self.reader = llm.make(args.model, self.root, args.workers, max_tokens=READER_TOKENS)
        self.judge = llm.make(args.judge_model, self.root, args.workers, max_tokens=JUDGE_TOKENS,
                              prefix="EXTBENCH_JUDGE")
        self.engines = any(a.startswith("engine:") for a in args.arm)
        self.embed_settings = embedding.settings_from_env() if self.engines else None
        self.embedder = embedding.make(self.embed_settings, self.root) if self.embed_settings else None
        self.out = self.root / "runs" / (args.name or time.strftime("longmemeval-%Y%m%d-%H%M%S"))
        self.out.mkdir(parents=True, exist_ok=True)
        self.answers, self.retrieved = self.out / "answers.jsonl", self.out / "retrieved.jsonl"
        _ = self.answers.write_text("")
        _ = self.retrieved.write_text("")
        self.p_in, self.p_out = (float(x) / 1e6 for x in args.price.split(","))
        self.stored = 0
        self.rows: list[dict] = []
        self.skipped: list[list[str]] = []
        self.failed: list[list[str]] = []
        self.kept: list[list[str]] = []

    def spent(self) -> float:
        """Estimated USD so far: reader and judge tokens, plus records a tool stored in this run."""
        tokens = sum(b.usage["input"] * self.p_in + b.usage["output"] * self.p_out for b in (self.reader, self.judge))
        return tokens + self.stored * self.args.per_record

    def write(self, path: Path, item: dict) -> None:
        """Append one JSON line, so a stopped run keeps what it finished."""
        with path.open("a", encoding="utf-8") as f:
            _ = f.write(json.dumps(item, ensure_ascii=False) + "\n")

    def question(self, arm: str, entry: dict) -> None:
        """Store the history, recall, answer and judge one question for one arm."""
        qid = entry["question_id"]
        records, evidence = records_of(entry)
        ns = f"longmemeval|{qid}"
        fresh = arm.startswith("oss:") and not memory.stored(arm, records, f"{ns}|{arm}", self.root)
        cost = len(records) * self.args.per_record if fresh else 0
        if self.args.budget is not None and self.spent() + cost > self.args.budget:
            self.skipped.append([arm, qid])
            return
        self.stored += len(records) if fresh else 0
        try:
            mem = memory.make(arm, records, ns, self.root, self.embedder, self.embed_settings)
            handed = mem.recall(entry["question"], self.args.k)
        except Exception as exc:  # one question's failure must not end the night
            self.failed.append([arm, qid, repr(exc)[:300]])
            print(f"{arm} {qid}: FAILED: {exc!r}"[:400], flush=True)
            return
        prompt = reader_prompt(entry, handed)
        prediction = self.reader.ask(prompt).strip()
        reply = self.judge.ask(judge_prompt(entry, prediction)).strip()
        row = {"arm": arm, "question_id": qid, "question_type": entry["question_type"],
               ABSTENTION: is_abstention(entry), "question": entry["question"], "answer": entry["answer"],
               "prediction": prediction, "judge_reply": reply, "correct": judged_correct(reply),
               "evidence_recall": evidence_recall(mem, handed, evidence), "handed": len(handed),
               "prompt_chars": len(prompt)}
        self.rows.append(row)
        self.write(self.answers, row)
        if arm not in BOUND_ARMS:
            self.write(self.retrieved, {"arm": arm, "question_id": qid, "texts": handed,
                                        "sources": list(getattr(mem, "sources", []))})
        print(f"{arm} {qid}: {'correct' if row['correct'] else 'wrong'}, estimated spend {self.spent():.2f}", flush=True)

    def summary(self, source: dict, questions: list[dict]) -> dict:
        """Everything a reader of the results needs: settings, usage, failures, scores per arm."""
        args, reader, judge = self.args, self.reader, self.judge
        return {
            "benchmark": "longmemeval-s", "data": source, "reader": reader.describe(), "judge": judge.describe(),
            "k": args.k, "questions": len(questions), "types": args.types, "only": args.only,
            "deviations": ["system message sent to reader and judge", "history is the arm's texts, one line per turn"],
            "calls": {"reader": reader.calls, "judge": judge.calls},
            "cache_hits": {"reader": reader.hits, "judge": judge.hits},
            "usage": {"reader": reader.usage, "judge": judge.usage},
            "estimated_usd": round(self.spent(), 2), "records_stored": self.stored, "per_record_usd": args.per_record,
            "core": memory.core_facts() if self.engines else None, "embedding": self.embed_settings,
            "embedding_cache": None if self.embedder is None
            else {"hits": self.embedder.hits, "misses": self.embedder.misses},
            "skipped_for_budget": self.skipped, "kept_from": args.resume, "kept": self.kept, "failed": self.failed,
            "arms": summarize(self.rows, args.arm),
        }


def main(argv: list[str] | None = None) -> Path:
    """Run the arms over the questions; return the run directory."""
    args = parse_args(argv)
    data, source = load(args.data)
    split = lambda s: set(s.split(",")) if s else None  # noqa: E731
    questions = select(data, split(args.types), split(args.only), args.questions)
    prior = read_prior(args.resume)
    run = Run(args)
    try:
        for arm in args.arm:
            for entry in questions:
                if (key := (arm, entry["question_id"])) in prior:  # written only once answered and judged
                    run.rows.append(prior[key])
                    run.kept.append(list(key))
                    run.write(run.answers, prior[key])
                    continue
                run.question(arm, entry)
        summary = run.summary(source, questions)
    finally:
        if run.embedder is not None:
            run.embedder.close()
    _ = (run.out / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False))
    print(json.dumps(summary["arms"], indent=2, ensure_ascii=False))
    print("written to", run.out)
    return run.out


if __name__ == "__main__":
    _ = main()
