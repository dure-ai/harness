"""Label fingerprint: a hash of the fields of a scenario that a reviewer may not change.

Ground-truth labels decide scores, so `review edit` compares the fingerprint before and after
an edit and undoes the edit when it moved (generation design 7.2). The order of events and probes
in the file counts as a label (events at the same time are replayed in file order), and so does
`language`. Text the reviewer may fix
(fact text and markers, event text and secret markers, probe queries, feedback template text)
and the whole `review` section are left out, as is `provenance.reviewer_edited`, which the
harness itself writes.
"""

import hashlib
import json
from typing import Any

from harness.scenario.model import FeedbackTemplate, Probe, Scenario

_FACT = {"valid_from", "valid_until", "valid", "conditions", "fix"}
_EVENT = {"id", "t", "user", "session", "repo", "scope", "team", "signal", "kind", "outcome", "fact", "refs", "meta"}
_PROBE = {"id", "kind", "t", "user", "session", "repo", "about", "meta", "k"}
_TEMPLATE = {"fact", "signal", "kind", "outcome", "scope", "delay"}
_ABSENT = object()


def _template_view(tpl: FeedbackTemplate | None) -> dict[str, Any] | None:
    return None if tpl is None else tpl.model_dump(mode="json", include=_TEMPLATE)


def _probe_view(p: Probe) -> dict[str, Any]:
    view = p.model_dump(mode="json", include=_PROBE)
    fb = p.feedback
    view["feedback"] = (
        None
        if fb is None
        else {
            "action": {fid: _template_view(tpl) for fid, tpl in fb.action.items()},
            "abstain": _template_view(fb.abstain),
            "on_false": _template_view(fb.on_false),
            "on_true": _template_view(fb.on_true),
        }
    )
    return view


def _label_view(s: Scenario) -> dict[str, Any]:
    """The non-editable fields of `s` as JSON-ready nested dicts keyed by fact, event and probe id."""
    return {
        "id": s.id,
        "family": s.family,
        "mode": s.mode,
        "language": s.language,
        "world": s.world.model_dump(mode="json"),
        "provenance": s.provenance.model_dump(mode="json", exclude={"reviewer_edited"}),
        "facts": {fid: f.model_dump(mode="json", include=_FACT) for fid, f in s.facts.items()},
        "event_order": [e.id for e in s.events],
        "events": {e.id: e.model_dump(mode="json", include=_EVENT) for e in s.events},
        "probe_order": [p.id for p in s.probes],
        "probes": {p.id: _probe_view(p) for p in s.probes},
    }


def label_fingerprint(s: Scenario) -> str:
    """Hash the fields of a scenario that a reviewer may not edit.

    Args:
        s: The scenario.

    Returns:
        The hex SHA-256 of the sorted JSON of those fields. Changing only editable text leaves
        it unchanged.
    """
    blob = json.dumps(_label_view(s), sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _diff(a: Any, b: Any, path: str, out: list[str]) -> None:
    if isinstance(a, dict) and isinstance(b, dict):
        for key in sorted(a.keys() | b.keys()):
            _diff(a.get(key, _ABSENT), b.get(key, _ABSENT), f"{path}.{key}" if path else key, out)
    elif a != b:
        out.append(path)


def changed_label_fields(a: Scenario, b: Scenario) -> list[str]:
    """List the non-editable fields in which two scenarios differ.

    Args:
        a: The scenario before an edit.
        b: The scenario after it.

    Returns:
        Sorted dotted paths such as `facts.f_old.valid_until`, `events.e3.refs` or
        `probes.p_block.about`. A fact, event or probe present on one side only is reported as
        its own path (`events.e9`); a list or mapping-valued field such as `refs`, `meta` or
        `provenance.evidence` is reported as one path. A reordering is reported as `event_order`
        or `probe_order`.
    """
    out: list[str] = []
    _diff(_label_view(a), _label_view(b), "", out)
    return out
