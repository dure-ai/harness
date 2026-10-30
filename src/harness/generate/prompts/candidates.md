You read the trajectory of a coding agent working on a software repository. Your job is to pull out repository knowledge that a different engineer would also need to know on a later task in the same repository.

Keep a fact only if all of these hold:
- It is reusable: it will still help on the next task, not only on this one.
- It is about the repository, its tooling, its conventions, or how its people work. Examples: how to build or run tests, a required configuration, a coding convention, a known workaround, who owns what.
- It is stated or clearly demonstrated in the trajectory, not guessed.

Leave out one-off facts: the contents of this task's bug report, a single file edit, a command output that only matters now, or anything about this specific run.

For each fact, give:
- statement: one self-contained sentence, written so it makes sense without the trajectory.
- kind: one of build, test, config, convention, workaround, ownership, other.
- quote: an exact excerpt copied character for character from the message that supports the fact. Do not paraphrase, translate or fix typos. Keep it short, one sentence or less. Copy it from the message text, not from the "…[N chars]…" clipping markers.
- message_index: the number N from the `[#N role]` header of the message that contains the quote.
- condition: an object of string to string when the fact only holds in a certain setting (for example {"service": "api"}); an empty object otherwise.
- fix: for a workaround-like fact, "root" when the agent fixed the underlying cause and "workaround" when it only worked around it; null when this does not apply.

Return at most {max_candidates} facts, the most reusable first. Return an empty list if there is nothing worth keeping.

Reply with a JSON object of the form {"candidates": [ ... ]} and nothing else.
