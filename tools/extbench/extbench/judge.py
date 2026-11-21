"""Second score for answers already written: a model says whether each answer means the gold answer.

Reads answers.jsonl files of finished runs; nothing is stored or searched again. The judge
sees question, gold answer and the answer, never the arm. Identical (question, gold, answer)
triples are judged once. Items go out several per call (the CLI adds its own context to every
call). The wording follows the lenient judge used in published LoCoMo comparisons: same fact
in other words is correct, a date in another format is correct.
"""

import argparse
import json
import random
import re
import time
from collections import defaultdict
from pathlib import Path

from . import llm

SYSTEM = "You grade answers to questions about a conversation. You reply with JSON lines only."
PROMPT = """Each item has a question, the gold answer, and a generated answer. Label the generated answer CORRECT or WRONG.

Be generous about wording: the generated answer is CORRECT when it states the same fact as the gold answer, even if it is longer, shorter, or phrased differently. For dates and times, a different format is CORRECT, and a relative form is CORRECT only when it works out to the same date or period as the gold answer; a different date or period is WRONG. When the gold answer lists several things, the generated answer is CORRECT if it gives at least half of them, and WRONG if it gives fewer. An answer that says it does not know, gives a different fact, or contradicts the gold answer is WRONG.

Reply with exactly one line per item, in order, and nothing else:
{{"id": <id>, "label": "CORRECT" or "WRONG"}}

{items}"""
NAMES = {1: "multi-hop", 2: "temporal", 3: "open-domain", 4: "single-hop"}


def parse(text: str, ids: list[int]) -> dict[int, bool] | None:
    """Read the labels out of a reply; None unless it covers exactly the ids asked."""
    got = {}
    for m in re.finditer(r'\{\s*"id"\s*:\s*(\d+)\s*,\s*"label"\s*:\s*"(CORRECT|WRONG)"\s*\}', text):
        got[int(m.group(1))] = m.group(2) == "CORRECT"
    return got if sorted(got) == sorted(ids) else None


def judge(model, triples: list[tuple], batch: int) -> tuple[list[bool], int]:
    """One label per triple; a reply that does not cover its items is asked again in halves."""
    labels: dict[int, bool] = {}
    split = 0
    todo = [list(range(i, min(i + batch, len(triples)))) for i in range(0, len(triples), batch)]
    while todo:
        prompts = [PROMPT.format(items="\n\n".join(
            f"id: {i}\nquestion: {triples[i][0]}\ngold answer: {triples[i][1]}\ngenerated answer: {triples[i][2]}"
            for i in ids)) for ids in todo]
        again = []
        for ids, text in zip(todo, model.ask_many(prompts)):
            got = parse(text, ids)
            if got is not None:
                labels.update(got)
            elif len(ids) == 1:
                raise RuntimeError(f"judge gave no label for one item: {text[:200]!r}")
            else:
                split += 1
                again += [ids[: len(ids) // 2], ids[len(ids) // 2:]]
        todo = again
    return [labels[i] for i in range(len(triples))], split


def main() -> None:
    """Judge the answers of finished runs and write judged.jsonl and summary.json."""
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True)
    ap.add_argument("--answers", action="append", required=True, help="answers.jsonl of a finished run")
    ap.add_argument("--model", required=True, help="e.g. claude-haiku-5-5 (through the local CLI)")
    ap.add_argument("--questions", type=int, default=None, help="judge a sample: this many questions, every arm")
    ap.add_argument("--batch", type=int, default=20)
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--name", required=True)
    args = ap.parse_args()

    root = Path(args.root)
    rows = [json.loads(line) for f in args.answers for line in open(f)]
    if args.questions:  # the same questions for every arm, categories in proportion
        by_cat = defaultdict(set)
        for r in rows:
            by_cat[r["category"]].add((r["conversation"], r["q"]))
        total = sum(len(v) for v in by_cat.values())
        rng = random.Random(args.seed)
        picked = set()
        for c, qs in sorted(by_cat.items()):
            picked |= set(rng.sample(sorted(qs), max(1, round(args.questions * len(qs) / total))))
        rows = [r for r in rows if (r["conversation"], r["q"]) in picked]

    triples = sorted({(r["question"], str(r["answer"]), r["prediction"]) for r in rows})
    random.Random(args.seed).shuffle(triples)
    model = llm.ClaudeCli(args.model, llm.Cache(root / "cache"), cwd=root / "cwd", system=SYSTEM, workers=args.workers)
    started = time.time()
    labels, split = judge(model, triples, args.batch)
    label = dict(zip(triples, labels))
    for r in rows:
        r["judged"] = label[(r["question"], str(r["answer"]), r["prediction"])]

    out = root / "runs" / args.name
    out.mkdir(parents=True, exist_ok=True)
    with open(out / "judged.jsonl", "w") as f:
        f.writelines(json.dumps(r, ensure_ascii=False) + "\n" for r in rows)
    mean = lambda xs: round(sum(xs) / len(xs), 4) if xs else None  # noqa: E731
    arms = {}
    for arm in dict.fromkeys(r["arm"] for r in rows):
        mine = [r for r in rows if r["arm"] == arm]
        arms[arm] = {"n": len(mine), "judged_correct": mean([r["judged"] for r in mine]), "f1": mean([r["f1"] for r in mine]),
                     "by_category": {NAMES[c]: {"n": len(v), "judged_correct": mean([r["judged"] for r in v]),
                                                "f1": mean([r["f1"] for r in v])}
                                     for c in sorted(NAMES) if (v := [r for r in mine if r["category"] == c])}}
    summary = {"judge": model.describe(), "prompt": PROMPT, "rows": len(rows), "distinct_judged": len(triples),
               "batch": args.batch, "calls": model.calls, "cache_hits": model.hits, "replies_split": split,
               "usage": model.usage, "wall_seconds": round(time.time() - started, 1), "arms": arms}
    (out / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False))
    print(json.dumps({k: v for k, v in summary.items() if k != "prompt"}, indent=2, ensure_ascii=False))
    print("written to", out)


if __name__ == "__main__":
    main()
