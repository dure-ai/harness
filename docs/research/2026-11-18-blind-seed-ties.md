# Blind seeds and score ties: measurements on dev

As of 2026-11-18. Harness `origin/main` `f827138`, core pinned at rev `a02369d` (`pyproject.toml`). Only the dev set (`scenarios/dev`, 40 approved scenarios) was used; the sealed test set was not touched. Engines that need an API key were not run.

## Summary

- Six local engines (none, all_store, repeat_n, oracle, core `PromoteAllEngine`, core `NoMemoryEngine`) were run with 12 seeds. **Only two probes change with the seed, both for PromoteAll.** The four harness baselines and NoMemory are identical down to each probe across the 12 seeds. Core at the pinned rev has no `EvidenceEngine` (`engines/baseline.py` holds only NoMemory and PromoteAll).
- In both probes the order changes among results with **exactly equal** top scores (a three-way tie and a four-way tie). Core orders ties by id, and that id is an opaque id derived from the seed.
- This is neither a harness bug nor a scenario error. The questions name both answers on purpose (unbiased questions), so an engine that only counts shared words is expected to tie, and the harness's opaque ids shuffle the tie evenly across seeds. With fixed ids, the scenario's writing order (e1 first) would always win.
- With core moved to `daaba07` (the pin in PR #29: core#3 BM25 store and core#4 EvidenceEngine), **no probe flips** across the same 12 seeds, EvidenceEngine included (section 6). Integer scores became real-valued BM25 scores, so the exact ties are gone. The rule that orders ties by id is unchanged, so the effect returns whenever ties come back (identical bodies, or another engine whose scores tie).
- A single-seed PromoteAll number is therefore one draw of the tie order. Dev M1 moves between 12/17 (0.706) and 13/17 (0.765), with an expected value of 12.67/17 (0.745). **To compare, use `harness seeds` with at least 5 seeds and read the mean and [min, max]; do not read a difference inside that range as an effect.**

## 1. Measurement

12 seeds: `1, 2, 3, 7, 42, 7391846205173948, 482910376145820931, 1181772346203451187, 9007199254740993, 15485863278812345, 2718281828459045235, 3141592653589793238`. Each engine ran once per seed, with a new engine per scenario (the same `run_scenario` and `blind_key` path as `harness run`).

| Engine | M1 (read_only) | M2 | Probes that changed |
|---|---|---|---|
| none | 0/17 | 0/31 | 0 |
| all_store | 17/17 | 29/31 | 0 |
| repeat_n | 5/17 | 0/31 | 0 |
| oracle | 0/17 | 31/31 | 0 |
| core NoMemoryEngine | 0/17 | 0/31 | 0 |
| core PromoteAllEngine | 12–13/17 | 25/31 | 2 |

Probes that changed for PromoteAll:

| Probe | Mode | Results over 12 seeds | Tie | Expected P(FP) |
|---|---|---|---|---|
| `conditional-geoarrow-python-b93126/p_block` | read_only | FP 8, BLOCKED 4 | three-way tie at score 9 (f_a 2, f_b 1) | 2/3 |
| `echo-meson-python-128a90/p_block3` | feedback | FP 10, BLOCKED 2 | four-way tie at score 10 (f_false 3, f_true 1) | 3/4 |

The first probe moves the headline M1 and the conditional family M1 (1–2/3); the second moves the feedback echo family M1 (11–12/15). The observed frequencies match the expected values (8/12 ≈ 2/3, 10/12 ≈ 3/4).

On `fix/confirm-option-b` (30 approved scenarios, the 10 awaiting re-approval excluded), the same two probes are the only ones that change (FP 8 and BLOCKED 4; FP 10 and BLOCKED 2). PromoteAll M1 is 8–9/12.

**Check for latent ties.** For each of PromoteAll's 85 judged probes at seed 1, I checked whether the first relevant result the agent followed had the same score as a result stating another fact. Only the two probes above do, so there are no hidden tied probes today.

## 2. Evidence of the ties

PromoteAll promotes one knowledge item per record as `k-{event id}`, and its search score is `|query words ∩ knowledge words|` (an integer). Core's in-memory store scans in id order and sorts stably by score, so ties stay in id order. Event ids are `HMAC(blind_key(seed), ...)`, so the order inside a tie changes at random with the seed.

### conditional-geoarrow-python-b93126 / p_block

Question (condition `service: reports`): "For reports, which file does as_wkb go through and how are kernel options packed (Kernel.as_wkb in _kernel.py, or encode_options_json in reports/geo_export.py)?"

| Score | Event | Fact | For this probe |
|---|---|---|---|
| 9 | e1 | f_b (reports path) | right |
| 9 | e3 | f_a (geoarrow-python path) | other condition → FP |
| 9 | e4 | f_a | other condition → FP |
| 5 | e6 | f_b | right |

Seeds 1 and 1181772346203451187 put e3 or e4 first (FP); seed 2 puts e1 first (BLOCKED). The only signal that separates the three results is the question's condition (`service`) and the events' conditions, and PromoteAll ignores conditions.

### echo-meson-python-128a90 / p_block3

Question: "Which part of mesonpy.py decides the Python tag in the wheel filename, `Project.python_tag` or `_Tag.python`, and how is `abi_tag` related to it?"

| Score | Event | Fact |
|---|---|---|
| 10 | e1 | f_false (the original record) |
| 10 | e4 | f_false (feedback echo: "Following the earlier note: " + e1's body) |
| 10 | e6 | f_false (echo) |
| 10 | e2 | f_true |

An echo adds a phrase to the original, so its word set contains the original's, and it shares as many words with the question as the original does. The true record shares the same number. Seeds 1181772346203451187 and 15485863278812345 put e2 first (BLOCKED).

## 3. Cause

| Probe | Scenario design | Engine | Harness |
|---|---|---|---|
| conditional p_block | The question names both answers (on purpose, so it does not give the answer away). The bodies are not near-identical; only the condition separates the answers | PromoteAll ignores conditions and its integer scores are coarse, so it ties. The engine cannot answer this probe, and the result is a draw | Correct. Opaque ids shuffle the tie evenly |
| echo p_block3 | The echo family stacks near-identical bodies by design, and the question names both answers | With word-set scores an echo cannot raise its score, only add tickets to the draw (3 of 4). In expectation, "echoes dilute" is measured correctly | Correct |

Both probes depend on a tie, but for an engine that only reads words that tie is intended. Removing it would make the question carry more of one answer's words, and word overlap would then give the answer away. They should not be changed.

## 4. Alternatives not chosen

- **Change core's tie rule (option A)**: this changes the contract of three stores, and the randomness only moves to another key (time, insertion order). Insertion order is the scenario's writing order, which would be a systematic bias.
- **Fix one seed (option C)**: reproducible, but tied to that seed's draw. For example, seed 7391846205173948, used for PR measurements, gives PromoteAll M1 12/17, the low end of the 12.
- **Let the harness break ties**: reordering an engine's results means intervening in the engine. Not done.

## 5. Recommendations and changes

1. **Add a `harness seeds` command.** It runs the baselines and candidates once per seed, writes M1, M2 and M3 overall, per family and per feedback family as `mean [min, max] (k/n)`, and lists the probes whose result changed across seeds (only a count with `--sealed`). It does not judge or rank. Six local engines × 12 seeds take a little over a second on dev.

   ```bash
   uv run harness seeds --scenarios scenarios/dev --engine cortex_mem.engines.baseline:PromoteAllEngine --n 5
   ```

2. **Comparison procedure.** A before/after comparison of an engine or core change (for example, a table in a PR description) runs both sides with the same seed list (repeat `--blind-seed`, taking the seeds from the earlier report's `engine_ids.json`) and reports the mean with [min, max]. A difference inside both ranges may be a different tie draw. Also check whether every seed moved in the same direction (with the same seed list, the values pair up).
3. **The 11-28 validation.** Use at least 5 seeds by default (option B). This applies to every engine that can tie, word-based or embedding-based. For LLM engines each seed costs API calls, and since `seeds` runs a candidate once per seed, sampling noise is part of the spread too. Look at sampling noise on its own with the flip rate of `run --repeat`.
4. **Reading one seed's numbers.** Summarized in the README section "Reading the numbers of one seed".

## 6. Measured again on core `daaba07` (PR #29)

Measured again with the same 12 seeds on `origin/chore/bump-core-pin` (core `daaba07`).

| Engine | M1 | M2 | Probes that changed |
|---|---|---|---|
| none / all_store / repeat_n / oracle / NoMemory | as in section 1 | as in section 1 | 0 |
| core PromoteAllEngine | 13/17 | 29/31 | 0 |
| core EvidenceEngine | 5/17 | 31/31 | 0 |

Core#3 changed the in-memory store's score from `|query words ∩ body words|` (an integer) to BM25 over words and bigrams (a real number normalized by document length). The tied results in the two probes above have bodies of different lengths, so their scores now differ. Therefore:

- The PromoteAll spread under the old pin (`a02369d`) went away when #29 merged. PromoteAll's M1 and M2 themselves change, though (12–13/17 → 13/17, 25/31 → 29/31), so use `seeds` for comparisons across the pin change too.
- Added 2026-11-22: after #31 (5 unconfirmed_poison scenarios) and #32 (option B confirmations) merged, the same 12 seeds on `main`'s dev set (45 scenarios) still flip no probe. PromoteAll M1 18/22 and M2 33/36, EvidenceEngine 5/22 and 35/36, NoMemory 0/22 and 0/36.
- The tie rule did not change. Two records with identical bodies, or an engine or store whose scores are coarse (for example, a store that rounds embedding distances), will flip with the seed again. The "probes that changed across seeds" list in `seeds` is the detector for that.

## Reproduction

The measurement scripts ran outside the repository (in a session scratchpad). The same results can be obtained with:

```bash
uv sync --extra core
uv run harness seeds --scenarios scenarios/dev \
  --engine cortex_mem.engines.baseline:PromoteAllEngine \
  --engine cortex_mem.engines.baseline:NoMemoryEngine \
  --blind-seed 1 --blind-seed 2 --blind-seed 3 --blind-seed 7 --blind-seed 42 \
  --blind-seed 7391846205173948 --blind-seed 482910376145820931 --blind-seed 1181772346203451187 \
  --blind-seed 9007199254740993 --blind-seed 15485863278812345 \
  --blind-seed 2718281828459045235 --blind-seed 3141592653589793238 --out runs
```

(Small seeds print a warning, which does not matter for dev measurements.)
