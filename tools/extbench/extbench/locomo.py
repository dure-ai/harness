"""LoCoMo question answering (Maharana et al., ACL 2024) with a memory arm in front of the reader.

The official runner has no slot for a memory system, so the loop is ours. Kept from the
official code (snap-research/locomo, task_eval/): the reader prompts (QA_PROMPT,
CONV_START_PROMPT, the date hint for category 2) and the scoring (`f1_score`, `f1`,
`eval_question_answering`: stemmed token F1, partial F1 over comma-separated parts for
category 1, the part before ';' for category 3). Scoring needs no model.

Category 5 (adversarial) is left out by default: the released file has no `answer` for
it, the official code path reads one, and published comparisons exclude it.

Categories in the data: 1 multi-hop, 2 temporal, 3 open-domain, 4 single-hop, 5 adversarial.
"""

import argparse
import json
import re
import string
import time
from collections import Counter, defaultdict
from datetime import UTC, datetime, timedelta
from pathlib import Path

from nltk.stem import PorterStemmer

from . import embedding, llm, memory

QA_PROMPT = """
Based on the above context, write an answer in the form of a short phrase for the following question. Answer with exact words from the context whenever possible.

Question: {} Short answer:
"""
CONV_START = ("Below is a conversation between two people: {} and {}. The conversation takes place over multiple days "
              "and the date of each conversation is wriiten at the beginning of the conversation.\n\n")
NAMES = {1: "multi-hop", 2: "temporal", 3: "open-domain", 4: "single-hop", 5: "adversarial"}
_ps = PorterStemmer()


def normalize_answer(s: str) -> str:
    """Lowercase, drop punctuation and articles, as the official scorer does."""
    s = s.replace(",", "").lower()
    s = "".join(ch for ch in s if ch not in set(string.punctuation))
    s = re.sub(r"\b(a|an|the|and)\b", " ", s)
    return " ".join(s.split())


def f1_score(prediction: str, truth: str) -> float:
    """Stemmed token F1 of one prediction against one answer."""
    p = [_ps.stem(w) for w in normalize_answer(prediction).split()]
    t = [_ps.stem(w) for w in normalize_answer(truth).split()]
    same = sum((Counter(p) & Counter(t)).values())
    if same == 0:
        return 0.0
    precision, recall = same / len(p), same / len(t)
    return 2 * precision * recall / (precision + recall)


def f1_multi(prediction: str, truth: str) -> float:
    """Mean over the answer's comma-separated parts of the best F1 any predicted part gets."""
    ps = [x.strip() for x in prediction.split(",")]
    ts = [x.strip() for x in truth.split(",")]
    return sum(max(f1_score(p, t) for p in ps) for t in ts) / len(ts)


def score(category: int, prediction: str, answer) -> float:
    """Score one prediction the way the official code does for its category."""
    answer = str(answer)
    if category == 3:
        answer = answer.split(";")[0].strip()
    return f1_multi(prediction, answer) if category == 1 else f1_score(prediction, answer)


def records_of(conv: dict, unit: str = "turn", times: str = "session") -> tuple[list[memory.Record], dict[str, str]]:
    """Turn a conversation into records, and map each dia_id to its turn's line.

    One record per turn, '<date> | <speaker>: <text>', in order; with unit "session", one
    record per session holding those lines.

    A record's time is its session's date plus one second per turn (times "session"), or a
    made-up clock that only keeps the order (times "order", what the first full run used).
    The date is in the text either way.
    """
    c = conv["conversation"]
    out, by_dia = [], {}
    base = datetime(2023, 1, 1, tzinfo=UTC)
    n = 0
    sessions = sorted((int(k.split("_")[1]) for k in c if re.fullmatch(r"session_\d+", k)))
    for s in sessions:
        date = c.get(f"session_{s}_date_time", "")
        began = datetime.strptime(date, "%I:%M %p on %d %B, %Y").replace(tzinfo=UTC) if times == "session" else None
        lines = []
        for m, turn in enumerate(c[f"session_{s}"]):
            text = turn["text"]
            if turn.get("blip_caption"):
                text += f" [shares {turn['blip_caption']}]"
            line = f"{date} | {turn['speaker']}: {text}"
            n += 1
            at = began + timedelta(seconds=m) if began else base + timedelta(minutes=n)
            by_dia[turn["dia_id"]] = line
            lines.append(line)
            if unit == "turn":
                out.append(memory.Record(f"{conv['sample_id']}-{turn['dia_id']}".replace(":", "_"), line,
                                         turn["speaker"], at))
        if unit == "session" and lines:
            out.append(memory.Record(f"{conv['sample_id']}-S{s}", "\n".join(lines), "speakers", at))
    return out, by_dia


def main() -> None:
    """Run the arms over the conversations and write answers, retrieved texts and a summary."""
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True)
    ap.add_argument("--root", required=True, help="bench directory (cache/, cwd/, runs/)")
    ap.add_argument("--model", required=True, help="openai:<model> (EXTBENCH_BASE_URL, EXTBENCH_API_KEY) or claude-cli:<model>")
    ap.add_argument("--arm", action="append", required=True)
    ap.add_argument("--k", type=int, default=20)
    ap.add_argument("--unit", choices=["turn", "session"], default="turn", help="what one stored record is")
    ap.add_argument("--categories", default="1,2,3,4")
    ap.add_argument("--conversations", type=int, default=None, help="first N conversations")
    ap.add_argument("--only", default=None, help="comma-separated sample ids, e.g. conv-26,conv-30")
    ap.add_argument("--times", choices=["session", "order"], default="session",
                    help="a record's time: its session's date, or a made-up clock that keeps the order")
    ap.add_argument("--per-category", type=int, default=None, help="first N questions per category per conversation")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--name", default=None)
    ap.add_argument("--resume", default=None, help="answers.jsonl of an earlier run; its finished conversations are kept as they are")
    ap.add_argument("--budget", type=float, default=None, help="stop before a conversation that would pass this (USD, estimated)")
    ap.add_argument("--per-record", type=float, default=0.001, help="estimated USD a tool spends storing one record")
    ap.add_argument("--price", default="0.15,0.60", help="reader USD per million input,output tokens")
    args = ap.parse_args()

    root = Path(args.root)
    model = llm.make(args.model, root, args.workers, max_tokens=32)  # official: 32 tokens, temperature 0
    engines = any(a.startswith("engine:") for a in args.arm)
    embed_settings = embedding.settings_from_env() if engines else None
    embedder = embedding.make(embed_settings, root) if embed_settings else None
    cats = {int(c) for c in args.categories.split(",")}
    data = json.load(open(args.data))[: args.conversations]
    if args.only:
        wanted = set(args.only.split(","))
        if missing := wanted - {c["sample_id"] for c in data}:
            raise SystemExit(f"no such conversation: {sorted(missing)}")
        data = [c for c in data if c["sample_id"] in wanted]
    rows = []
    ingest: Counter = Counter()
    name = args.name or time.strftime("locomo-%Y%m%d-%H%M%S")
    out = root / "runs" / name
    out.mkdir(parents=True, exist_ok=True)
    (out / "answers.jsonl").write_text("")
    (out / "retrieved.jsonl").write_text("")
    p_in, p_out = (float(x) / 1e6 for x in args.price.split(","))
    stored_now = 0  # records this run had a tool store
    skipped, failed = [], []

    def spent() -> float:
        return model.usage["input"] * p_in + model.usage["output"] * p_out + stored_now * args.per_record

    prior = defaultdict(list)
    if args.resume:
        for line in open(args.resume):
            r = json.loads(line)
            prior[(r["arm"], r["conversation"])].append(r)
    kept = []

    for arm in args.arm:
        for conv in data:
            if (arm, conv["sample_id"]) in prior:  # written only once a conversation was finished
                mine = prior[(arm, conv["sample_id"])]
                rows.extend(mine)
                kept.append([arm, conv["sample_id"]])
                with open(out / "answers.jsonl", "a") as f:
                    f.writelines(json.dumps(r, ensure_ascii=False) + "\n" for r in mine)
                print(f"{arm} {conv['sample_id']}: {len(mine)} questions kept from the earlier run", flush=True)
                continue
            records, by_dia = records_of(conv, args.unit, args.times)
            # A tool keeps what it was given, times included, so histories stored with other times are not reused.
            ns = f"locomo|{conv['sample_id']}|{args.unit}" + ("" if args.times == "order" else "|session-times")
            fresh = arm.startswith("oss:") and not memory.stored(arm, records, f"{ns}|{arm}", root)
            if args.budget is not None and spent() + (len(records) * args.per_record if fresh else 0) > args.budget:
                skipped.append([arm, conv["sample_id"]])
                print(f"{arm} {conv['sample_id']}: skipped, estimated spend {spent():.2f} near budget", flush=True)
                continue
            stored_now += len(records) if fresh else 0
            done = len(rows)
            try:
                mem = memory.make(arm, records, ns, root, embedder, embed_settings)
            except Exception as exc:  # a tool that failed on one history must not end the night
                failed.append([arm, conv["sample_id"], repr(exc)[:300]])
                print(f"{arm} {conv['sample_id']}: FAILED storing: {exc!r}"[:400], flush=True)
                continue
            ingest[arm] += getattr(mem, "ingest_seconds", 0.0)
            c = conv["conversation"]
            start = CONV_START.format(c["speaker_a"], c["speaker_b"])
            taken: Counter = Counter()
            jobs = []
            retrieved = []
            for i, qa in enumerate(conv["qa"]):
                cat = qa["category"]
                if cat not in cats or "answer" not in qa:
                    continue
                taken[cat] += 1
                if args.per_category and taken[cat] > args.per_category:
                    continue
                question = qa["question"] + (" Use DATE of CONVERSATION to answer with an approximate date."
                                             if cat == 2 else "")
                try:
                    handed = mem.recall(qa["question"], args.k)
                except Exception as exc:
                    failed.append([arm, conv["sample_id"], f"q{i}: {exc!r}"[:300]])
                    continue
                dias = [e for e in qa.get("evidence", []) if e in by_dia]
                if hasattr(mem, "sources"):  # tools rewrite what they store; go by the record ids they report
                    got = set(mem.sources)
                    hits = [f"{conv['sample_id']}-{e}".replace(":", "_") in got for e in dias]
                else:
                    hits = [any(by_dia[e] in h for h in handed) for e in dias]
                found = sum(hits) / len(dias) if dias else None
                if arm not in ("none", "full"):  # the whole conversation is not worth a copy per question
                    retrieved.append({"arm": arm, "conversation": conv["sample_id"], "q": i, "texts": handed,
                                      "sources": list(getattr(mem, "sources", []))})
                prompt = (start + "\n".join(handed) + "\n\n" if handed else "") + QA_PROMPT.format(question)
                jobs.append((i, qa, prompt, found, len(handed)))
            answers = model.ask_many([j[2] for j in jobs])
            for (i, qa, prompt, found, n), text in zip(jobs, answers):
                rows.append({"arm": arm, "conversation": conv["sample_id"], "q": i, "category": qa["category"],
                             "question": qa["question"], "answer": qa["answer"], "prediction": text.strip(),
                             "f1": score(qa["category"], text.strip(), qa["answer"]), "evidence_recall": found,
                             "handed": n, "prompt_chars": len(prompt)})
            with open(out / "answers.jsonl", "a") as f:
                f.writelines(json.dumps(r, ensure_ascii=False) + "\n" for r in rows[done:])
            with open(out / "retrieved.jsonl", "a") as f:
                f.writelines(json.dumps(r, ensure_ascii=False) + "\n" for r in retrieved)
            print(f"{arm} {conv['sample_id']}: {len(jobs)} questions, ingest {getattr(mem, 'ingest_seconds', 0.0)}s, "
                  f"estimated spend {spent():.2f}", flush=True)

    summary: dict = {"benchmark": "locomo", "data": args.data, "model": model.describe(), "k": args.k,
                     "categories": sorted(cats), "conversations": len(data), "per_category": args.per_category,
                     "unit": args.unit, "times": args.times, "calls": model.calls, "cache_hits": model.hits, "usage": model.usage,
                     "estimated_usd": round(spent(), 2), "records_stored": stored_now, "per_record_usd": args.per_record,
                     "core": memory.core_facts() if engines else None, "embedding": embed_settings,
                     "embedding_cache": None if embedder is None else {"hits": embedder.hits, "misses": embedder.misses},
                     "skipped_for_budget": skipped, "kept_from": args.resume, "kept": kept, "failed": failed, "arms": {}}
    for arm in args.arm:
        mine = [r for r in rows if r["arm"] == arm]
        by_cat = defaultdict(list)
        for r in mine:
            by_cat[r["category"]].append(r)
        mean = lambda xs: round(sum(xs) / len(xs), 4) if xs else None  # noqa: E731
        summary["arms"][arm] = {
            "n": len(mine), "f1": mean([r["f1"] for r in mine]), "ingest_seconds": round(ingest[arm], 1),
            "evidence_recall": mean([r["evidence_recall"] for r in mine if r["evidence_recall"] is not None]),
            "prompt_chars": mean([r["prompt_chars"] for r in mine]),
            "by_category": {NAMES[c]: {"n": len(v), "f1": mean([r["f1"] for r in v])} for c, v in sorted(by_cat.items())},
        }
    (out / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False))
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    print("written to", out)
    if embedder is not None:
        embedder.close()


if __name__ == "__main__":
    main()
