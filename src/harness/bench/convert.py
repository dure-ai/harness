"""Shared pieces of the dataset converters: scenario text for files and a strictly ordered clock."""

from datetime import datetime, timedelta

from harness.scenario.model import Scenario
from harness.yamlio import dump_yaml


def scenario_text(s: Scenario) -> str:
    """The YAML text of `s`, in the format of `generate.pipeline.draft_text`.

    Args:
        s: The scenario.

    Returns:
        The YAML document.
    """
    return dump_yaml(s.model_dump(mode="json"))


class Clock:
    """Hands out strictly increasing times: a wanted time, or one second after the last one."""

    def __init__(self) -> None:
        self._last: datetime | None = None

    def peek(self, wanted: datetime) -> datetime:
        """The time `at(wanted)` would hand out, without handing it out."""
        if self._last is not None and wanted <= self._last:
            return self._last + timedelta(seconds=1)
        return wanted

    def at(self, wanted: datetime) -> datetime:
        """Return `wanted`, or one second after the previous time if that is not later."""
        self._last = t = self.peek(wanted)
        return t
