"""Shared pieces of the dataset converters: scenario text for files and a strictly ordered clock."""

from datetime import datetime, timedelta

import yaml

from harness.scenario.model import Scenario


def scenario_text(s: Scenario) -> str:
    """The YAML text of `s`, in the format of `generate.pipeline.draft_text`.

    Args:
        s: The scenario.

    Returns:
        The YAML document.
    """
    return yaml.safe_dump(s.model_dump(mode="json"), sort_keys=False, allow_unicode=True)


class Clock:
    """Hands out strictly increasing times: a wanted time, or one second after the last one."""

    def __init__(self) -> None:
        self._last: datetime | None = None

    def at(self, wanted: datetime) -> datetime:
        """Return `wanted`, or one second after the previous time if that is not later."""
        t = wanted
        if self._last is not None and t <= self._last:
            t = self._last + timedelta(seconds=1)
        self._last = t
        return t
