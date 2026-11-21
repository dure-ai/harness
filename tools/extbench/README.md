# extbench: question answering over LoCoMo with a memory in front of the reader

A small runner that answers the questions of an external benchmark with a reader model, once per
memory "arm", and scores the answers with the benchmark's own scorer. It was written to get
reference numbers for existing memory tools (Mem0, Hindsight) next to two bounds: no memory and
the whole conversation.

It is not part of the pre-registered measurement and not part of `harness bench`. Both of those
keep models off the scoring path; here a model writes every answer, and a second model can judge
them. That is why it lives under `tools/` and has its own outputs. It reuses two things from the
harness: the Mem0 and Hindsight adapters (`harness.oss`) and the pinned core.

## What one run does

For each arm and each conversation:

1. Turn the conversation into records and hand them to the arm's memory.
2. For each question, ask the memory for `k` texts (default 20).
3. Give the reader the official LoCoMo prompt with those texts and take a short answer.
4. Score the answer with the official stemmed token F1.

`judge` is a separate step over finished runs: a model labels each answer CORRECT or WRONG against
the gold answer, without seeing which arm wrote it.

## Records and events

- One record per turn (`--unit turn`, the default) or per session (`--unit session`).
- A record's text is `<session date> | <speaker>: <text>`, for example
  `1:56 pm on 8 May, 2023 | Caroline: I went to a LGBTQ support group yesterday`. An image turn
  gets ` [shares <caption>]` appended. So every text an arm hands over carries its session date.
- A record's time is the session's date plus one second per turn (`--times session`, the default).
  `--times order` uses a made-up clock that only keeps the order; the first full run used it,
  before the session date was wired in.
- Engines get `Event(kind="record", signal="self", scope="team", user=<speaker>)`. Tools get
  the harness event `kind="agent_record", signal="self", outcome="unknown", repo="chat"`.
  LoCoMo has no outside checks, so nothing is ever confirmed or refuted.

## Arms

| arm | what the reader gets |
| --- | --- |
| `none` | nothing |
| `full` | every record, in order |
| `engine:<module>:<Class>` | `Runtime.handoff(question, scope="team", k)` of a core engine, built with no arguments, over the in-memory stores |
| `oss:mem0`, `oss:hindsight` | one search of the self-hosted tool through the harness adapter |

- The harness's `CoreBridge` and its baselines (`all_store` and the others) are not used. The
  "simple search" row below is core's `PromoteAllEngine` over core's in-memory store.
- The engine arm imports whatever `cortex_mem` is first on the path. The harness pin
  (`a02369d`) predates the in-memory BM25 ranking and gives lower numbers. Set
  `EXTBENCH_CORE=<core checkout>/src` to run a newer core. Core `main` at `0c3f85d` reproduces
  the simple-search row below exactly (checked on `conv-26`: the same prompts for all 152
  questions).
- A tool stores each history once. `ingested/` holds a marker per stored history and a
  `.partial.json` while one is in progress, so a stopped run continues from the record it
  reached. Histories stored with `--times order` are not reused by `--times session`.
- For tools, evidence recall is computed from the record ids the tool reports as sources, since
  tools rewrite what they store. For other arms it is a substring match on the handed texts.

## Running

```bash
uv sync --extra core
cp tools/extbench/env.example.sh tools/extbench/env.sh   # fill in the key; env.sh is ignored by git
. tools/extbench/env.sh
```

The data is not in this repository. Get `data/locomo10.json` from
[snap-research/locomo](https://github.com/snap-research/locomo) and pass its path with `--data`.
Mem0 and Hindsight are started from `engines/docker/` as for the harness itself.

```bash
# One conversation, temporal questions only, two engines. A few cents.
EXTBENCH_CORE=../core/src tools/extbench/run.sh locomo --data locomo10.json \
  --model openai:solar-pro4 --only conv-26 --categories 2 \
  --arm engine:cortex_mem.engines.baseline:PromoteAllEngine \
  --arm engine:cortex_mem.engines.supersede:SupersedeEngine

# Judge finished runs with a model through the local Claude Code CLI.
tools/extbench/run.sh judge --model claude-sonnet-5-5 --name judged \
  --answers runs/a/answers.jsonl --answers runs/b/answers.jsonl
```

Options worth knowing:

- `--only conv-26,conv-30`, `--conversations N`, `--categories 1,2,3,4`, `--per-category N`
  make a run small. Categories: 1 multi-hop, 2 temporal, 3 open-domain, 4 single-hop. Category 5
  (adversarial) has no gold answer in the released file and is left out.
- `--resume <answers.jsonl>` keeps the finished conversations of an earlier run as they are.
- `--budget <usd>` stops before a conversation whose estimated cost would pass the amount. The
  estimate uses `--price` for the reader and `--per-record` for a tool storing one record.
- `--workers N` is the number of reader calls in flight. Keep it at 2 with the `full` arm.
- Reader and judge replies are cached on disk by (backend, model, system, prompt, limits), so a
  repeated run costs nothing. `EXTBENCH_ROOT` moves `cache/`, `runs/` and `ingested/` elsewhere.

Outputs, under `runs/<name>/` (not committed):

- `answers.jsonl`: one line per question with the gold answer, the prediction, F1, evidence
  recall, and the size of the prompt.
- `retrieved.jsonl`: the texts and source ids each arm handed over (not for `none` and `full`).
- `summary.json`: the settings, token usage, estimated cost, failures, and scores per arm.
- `judged.jsonl` and its `summary.json` from `judge`.

## Results of the first full run (18 to 20 November 2026)

All ten conversations (5,882 turns), 1,540 questions, `k` = 20, one record per turn,
`--times order`. Reader and the tools' own LLM: Upstage `solar-pro4` (temperature 0, 32 output
tokens); embeddings `solar-embedding-2-passage`. Mem0 2.2.1 and Hindsight 1.0.0, self-hosted.
Judge: Claude Sonnet 5.5. No question failed or was skipped.

| arm | judged correct | F1 | evidence recall | prompt chars |
| --- | --- | --- | --- | --- |
| none | 0.02 | 0.03 | 0 | 260 |
| simple search (`PromoteAllEngine`) | 0.49 | 0.44 | 0.61 | 4,300 |
| Hindsight | 0.63 | 0.51 | 0.81 | 4,100 |
| Mem0 | 0.65 | 0.52 | 0.74 | 3,300 |
| full | 0.67 | 0.57 | 1.00 | 104,900 |

Judged correct by category:

| category | questions | simple search | Mem0 | Hindsight | full |
| --- | --- | --- | --- | --- | --- |
| single-hop | 841 | 0.68 | 0.79 | 0.74 | 0.86 |
| temporal | 321 | 0.23 | 0.47 | 0.50 | 0.28 |
| multi-hop | 282 | 0.34 | 0.54 | 0.58 | 0.65 |
| open-domain | 96 | 0.23 | 0.31 | 0.31 | 0.36 |

Differences in judged correct, with paired bootstrap 95% intervals over questions:

| comparison | difference | interval |
| --- | --- | --- |
| Mem0 − Hindsight | 0.014 | −0.011 to 0.040 |
| Mem0 − simple search | 0.153 | 0.126 to 0.179 |
| Hindsight − simple search | 0.138 | 0.111 to 0.166 |
| full − Mem0 | 0.023 | −0.002 to 0.049 |
| full − Hindsight | 0.038 | 0.011 to 0.064 |
| temporal only: full − Mem0 | −0.190 | −0.255 to −0.125 |

Temporal questions, split by whether the gold answer is a relative date such as "the week before
9 June 2023" (94 questions) or a date (227): simple search 0.12 / 0.27, full 0.15 / 0.34,
Mem0 0.34 / 0.53, Hindsight 0.40 / 0.53. With the whole conversation and its dates in the prompt,
the reader often answers with the session date itself.

On `conv-26` (152 questions), `SupersedeEngine` and `EvidenceEngine` hand over exactly the same
20 texts as `PromoteAllEngine` for every question, as expected when every record is `self`.

The whole run cost USD 15.39 on Upstage, including a first attempt that ran both tools at once
and failed on rate limits. The judge ran on a Claude subscription.

### How far these numbers go

- One reader model and one run. Rankings can change with another reader, and run-to-run spread
  was not measured.
- Not comparable with published LoCoMo figures, which use other models and judges.
- The judge is wrong on about 4% of answers (3 doubtful labels in a hand-read sample of 75),
  which is more than the difference between Mem0 and Hindsight.
- LoCoMo is everyday talk between two people. It has no stale or conflicting records, no
  sharing across people, and no outside checks.

## Rate limits met on the way

- Run one tool at a time. Two tools storing at once, or a tool next to the `full` arm, hit the
  provider's chat limit.
- Searches are spaced one second apart (`SEARCH_GAP`): with cached answers a tool is searched
  faster than the embedding endpoint's per-minute limit allows.
- A tool call that fails with a 429 is retried with a growing wait. Hindsight can still leave
  background operations failed after a 429, and then the whole conversation fails; it is
  recorded under `failed` in the summary and not scored.
