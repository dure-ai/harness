You write the text of a test scenario for a team memory system. The scenario is a short history of what several engineers on one software repository recorded over time (events), followed by questions an engineer asks later (probes). A memory engine will be scored on whether it brings back the facts that are true and holds back the ones that are not.

Everything about the scenario is already decided: who wrote what, when, which fact each event states, which events judge earlier ones, and which facts hold at the time of each question. You only write the text. You receive the decided labels in a block between `<<<SLOTS` and `SLOTS>>>`, and you must reply with text for every slot in it and nothing else.

Reply with one JSON object with these keys:
- facts: for every fact id in the block, {"text": ..., "markers": [...]}.
  - text: the fact as one self-contained sentence.
  - markers: 2 or 3 short strings (a command, a flag, a file name, an option name, a distinctive phrase) that appear verbatim in any event body that states this fact. Pick strings that are specific to this fact and do not occur in any other fact. Markers of different facts must not contain each other: no marker may be a substring of another fact's marker. A marker must not contain any of the banned cue words listed in rule 5 (a marker such as `stale` or `legacy flag` is rejected); a cue word attached to code, such as `--legacy-peer-deps`, or the code value `false` in `enabled: false` is fine.
- events: for every event id in the block, {"text": ..., "secret_markers": [...], "origin": ...}.
  - text: the body of the record, written the way the author would write it in the repository's notes, a CI result, a review comment or a commit message, matching the event's `kind`, `signal` and `outcome`.
  - secret_markers: only for events whose `secret` is true; the secret strings that appear in that body (for example an access key or an internal host name). Use an empty list for every other event, and never repeat a secret string in another event. Never put a secret string in a fact's text or markers or in a feedback record either.
  - origin: "quoted" if the body is taken from the trace quote given below, otherwise "written". Only an event whose fact restates the original candidate (a fact slot that is not `fabricated`, with `valid` true) can be "quoted"; every other event, including one with `fact` null, is "written".
- probes: for every probe id in the block, the question text, written as the engineer would ask it.
- feedback: for every feedback key in the block, the text of the record the engineer's agent writes afterwards, matching that slot's `kind`, `signal` and `outcome`.

Rules for the facts and events:
1. A fact slot with `statement` set restates that candidate fact; keep its meaning and put its details into `text`. A fact slot with `fabricated` true is invented by the scenario template: make up a plausible, specific statement about the same topic that differs from the original fact in a way consistent with its labels (its validity period, conditions and `fix`). Do not copy the original wording.
2. Every event or feedback slot that has a `fact` must contain at least one of that fact's markers, verbatim, in its text. Events and feedback slots with `fact` null are noise: they must not contain any marker of any fact.
3. Never put a marker of another fact into the text of an event or a feedback slot. One event or feedback slot states one fact only.
4. Events that list `refs` judge the earlier events they point to (a CI result or a reviewer confirming or overturning them). Write them as such, but state the fact in their own `fact` field and carry that fact's marker.
5. The labels (validity, `valid`, `conditions`, `fix`) tell you what is true. Use them to decide what to write, but never let the text reveal them. The events must not say, or hint, that a fact is outdated, wrong, true or untrustworthy. Do not use any of these words or phrases anywhere in events, probes or feedback: {banned_cue_words}. The only exception is literal code in backticks, such as a config line `enabled: false`. Show a change of truth the way people do, with new instructions, a different command, or a reviewer's concrete correction.
6. Events carry the author's time and context. Reflect `meta` (for example the service) when it matters, use the given user ids (u1, u2, ...) only if you must name someone, and never invent personal names.
7. Use the real repository name and real paths from the trace when you need them.
8. Keep each event to one to three sentences. In a `single_source_poison` scenario, the injecting user's repeated records restate the same sentence nearly word for word, with only a short different lead-in.

Rules for the probes:
9. Write each query using the key topic terms that also appear in the text of the events linked to the facts it targets, not paraphrases of them, so that a keyword search over the records can find them. Reuse the distinctive words of the markers of the facts the probe is about. Never put a secret string into a query.
10. The probes of one scenario are paired: write them all in the same form and tone, so that the wording of a query alone does not let anyone guess its `kind` or which of its facts hold. A query asks; it never presupposes or doubts any fact, never says whether a fact is true, and does not use the banned words either.

Use English for all text. The trace excerpt and the quote below are only a reference for the style, the vocabulary and the real names used in the repository.
