# Contributing

한국어: [CONTRIBUTING.ko.md](CONTRIBUTING.ko.md). This English file is the source of truth; a PR that changes one changes the other.

## Workflow

1. **Start from an issue.** Pick one or open one with the bug, feature or task form. A follow-up that comes out of a review is a task.
2. **Branch from `main`.** Name the branch `type/short-name`, for example `fix/engine-start-failure`.
   If your branch is stacked on another branch that is not merged yet, move it after that one is squash-merged: `git rebase --onto origin/main <base-branch> <your-branch>`. The squash commit on `main` replaces the base branch's commits, so changing the PR base alone leaves them in your PR.
3. **Open a pull request** and fill in the template. Link the issue with `Fixes #123`, or `Part of #123` when the PR does not finish it.
4. **Merge by squash** once CI passes and a reviewer approves. The branch is deleted automatically.

## Pull requests

We squash-merge, so each PR becomes one commit on `main`: the PR title is the commit subject and the PR description is the commit body.

- **Title**: `type(scope): description`, for example `fix(bridge): close the session on error`. The scope is optional. CI checks the format.
- **Types**: `feat`, `fix`, `docs`, `data`, `test`, `refactor`, `perf`, `ci`, `build`, `chore`, `revert`. Use `data` for changes to scenario sets.
- **Description**: write it for someone reading `git log` a year from now. Say what changes and why, and how you tested it. Put questions for the reviewer, screenshots and other discussion in PR comments, not in the description.
- **One PR, one change.** Leave unrelated cleanup for another PR.
- Write titles, descriptions and commit messages in English.

## Development

Python 3.12 or later and [uv](https://docs.astral.sh/uv/). The [README](README.md) covers setup and the commands in detail.

```bash
uv sync --extra core            # install, including the cortex-mem engine
uv run ruff check src tests     # lint
uv run basedpyright src         # type check
uv run pytest -q -W error       # tests, warnings as errors
```

CI runs these four on every PR. Run them before you push. The ruff rules are in `pyproject.toml`: docstrings, pyflakes, import order, bugbear, pyupgrade and simplifications; `uv run ruff check --fix src tests` fixes most findings. The code is not run through `ruff format`.

If you change scenarios, also run `uv run harness scenario check <path>`. Approve a scenario with `uv run harness review set <id> approved --reviewer <name>`, not by editing `review.status` by hand. Run outputs under `runs/` are not committed.

[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) describes the structure and the rules the harness must keep, such as no randomness and no LLM on the scoring path.

## Use of AI

AI tools are welcome here, including agents that open issues and pull requests and write their descriptions.

- **You are responsible for what you submit.** Read the whole diff before you ask for review, be able to explain every change in it, and answer review comments yourself.
- **Disclose it in the PR.** Check "AI assistance was used to create this PR" in the template and name the tool. Because the PR description becomes the commit body, this is also the record in git history.
- **Do not credit a model in commits.** No `Co-Authored-By` or similar trailer for an AI tool. The repository's `.claude/settings.json` turns this off for Claude Code; if you use another tool, turn it off there.

Instructions for coding agents are in [AGENTS.md](AGENTS.md).
