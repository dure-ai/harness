"""Move the dev truth_change and failure_feedback scenarios to the option-B confirmation (#25).

Usage: uv run python scripts/migrate_confirm_option_b.py scenarios/dev
"""

import sys
from pathlib import Path

from harness.generate.pipeline import save_draft
from harness.scenario.load import load_scenario
from harness.scenario.migrate import overturn_option_b

FAMILIES = ("truth_change", "failure_feedback")


def main(argv: list[str]) -> int:
    """Migrate every file under the two family folders of `argv[1]`; return the exit code."""
    if len(argv) != 2:
        print(__doc__)
        return 2
    root = Path(argv[1])
    files = sorted(p for fam in FAMILIES for p in (root / fam).glob("*.yaml"))
    for path in files:
        save_draft(overturn_option_b(load_scenario(path)), path)
        print(f"migrated {path}")
    print(f"{len(files)} files")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
