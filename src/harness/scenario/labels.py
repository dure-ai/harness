"""Label fingerprint: a hash of the fields of a scenario that a reviewer may not change.

Ground-truth labels decide scores, so `review edit` compares the fingerprint before and after
an edit and undoes the edit when it moved (generation design 7.2). The order of events and probes
in the file counts as a label (events at the same time are replayed in file order), and so does
`language`. `secret_markers` and `provenance.secret_events` are labels too: they decide what counts
as a leak, so a body that does not fit its secret string is rejected and generated again, not
patched. Text the reviewer may fix
(fact text and markers, event text, probe queries, feedback template text)
and the whole `review` section are left out, as are the fields the harness itself writes:
`provenance.reviewer_edited` and the two hashes `provenance.label_fingerprint` and
`provenance.generated_hash` (a fingerprint cannot cover itself).

`content_hash` is the other hash: of a whole parsed file, labels and text, used to bind an
approval (`review.approved_hash`) and a generated draft (`provenance.generated_hash`) to the
exact content. The two uses are domain-separated (`purpose`), so a generated hash copied into
`review.approved_hash` does not verify. Both hashes live in the file, so they catch accidental
edits, not deliberate tampering (whoever edits the content can also rewrite the hash); git
history is the evidence.
"""

import hashlib
import json
from collections.abc import Mapping
from datetime import date, datetime, time, timedelta
from typing import Any, Literal

from harness.scenario.model import Evidence, FeedbackTemplate, Probe, Scenario

CONTENT_HASH_VERSION = "v1"
"""Prefix of `content_hash` values; a stored hash with another prefix cannot be verified."""
FINGERPRINT_VERSION = "l1"
"""Prefix of `label_fingerprint` values; a stored fingerprint without it cannot be verified."""
HashPurpose = Literal["approval", "generated"]
"""What a `content_hash` is for. Part of the hashed material, so the two never coincide."""
_HASH_FIELDS = {"reviewer_edited", "label_fingerprint", "generated_hash"}

_FACT = {"valid_from", "valid_until", "valid", "conditions", "fix"}
_EVENT = {"id", "t", "user", "session", "repo", "scope", "team", "signal", "kind", "outcome", "fact", "refs", "meta", "secret_markers"}
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
        "provenance": s.provenance.model_dump(mode="json", exclude=_HASH_FIELDS),
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
        `"l1:"` followed by the hex SHA-256 of the sorted JSON of those fields. Changing only
        editable text leaves it unchanged.
    """
    blob = json.dumps(_label_view(s), sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return f"{FINGERPRINT_VERSION}:{hashlib.sha256(blob.encode('utf-8')).hexdigest()}"


FingerprintState = Literal["verified", "changed", "unverified"]


def fingerprint_state(s: Scenario) -> FingerprintState:
    """Compare the labels of `s` with the fingerprint recorded in `provenance.label_fingerprint`.

    Args:
        s: The scenario.

    Returns:
        "verified" when the recorded fingerprint equals `label_fingerprint(s)`, "changed" when it
        is a current-version fingerprint that differs (labels edited outside `review edit`), and
        "unverified" when none is recorded or it has no known version prefix (an older file).
    """
    stored = s.provenance.label_fingerprint
    if stored is None or not stored.startswith(f"{FINGERPRINT_VERSION}:"):
        return "unverified"
    return "verified" if stored == label_fingerprint(s) else "changed"


def _diff(a: Any, b: Any, path: str, out: list[str]) -> None:
    if isinstance(a, dict) and isinstance(b, dict):
        for key in sorted(a.keys() | b.keys()):
            _diff(a.get(key, _ABSENT), b.get(key, _ABSENT), f"{path}.{key}" if path else key, out)
    elif a != b:
        out.append(path)


def _is_unquoted(before: Evidence, after: Evidence) -> bool:
    """Whether `after` is `before` with its quote given up: quoted, then written with no trace fields."""
    return (
        before.origin == "quoted"
        and after.origin == "written"
        and after.trace_id is None
        and after.message_index is None
        and after.quote is None
    )


def unquoted_evidence(a: Scenario, b: Scenario) -> list[str]:
    """List the events whose evidence a reviewer turned from quoted into written.

    Args:
        a: The scenario before an edit.
        b: The scenario after it.

    Returns:
        Ids of events whose evidence entry is `quoted` in `a` and `written` with `trace_id`,
        `message_index` and `quote` all null in `b`. Any other change of an entry is not listed.
    """
    after = {e.event_id: e for e in b.provenance.evidence}
    return [
        e.event_id
        for e in a.provenance.evidence
        if e.event_id in after and _is_unquoted(e, after[e.event_id])
    ]


def changed_label_fields(a: Scenario, b: Scenario, allow_unquote: bool = False) -> list[str]:
    """List the non-editable fields in which two scenarios differ.

    Args:
        a: The scenario before an edit.
        b: The scenario after it.
        allow_unquote: True to leave a quoted-to-written evidence change (`unquoted_evidence`) out
            of the result, as `review edit` does; the reverse change is still reported.

    Returns:
        Sorted dotted paths such as `facts.f_old.valid_until`, `events.e3.refs` or
        `probes.p_block.about`. A fact, event or probe present on one side only is reported as
        its own path (`events.e9`); a list or mapping-valued field such as `refs`, `meta` or
        `provenance.evidence` is reported as one path. A reordering is reported as `event_order`
        or `probe_order`.
    """
    out: list[str] = []
    if allow_unquote and (freed := set(unquoted_evidence(a, b))):
        # Compare as if those entries had stayed quoted; every other difference still shows.
        kept = [
            next(o for o in a.provenance.evidence if o.event_id == e.event_id) if e.event_id in freed else e
            for e in b.provenance.evidence
        ]
        b = b.model_copy(update={"provenance": b.provenance.model_copy(update={"evidence": kept})})
    _diff(_label_view(a), _label_view(b), "", out)
    return out


def _canonical(value: Any) -> Any:
    """`value` as plain JSON data: mapping keys as strings, YAML timestamps as ISO strings."""
    if isinstance(value, Mapping):
        return {str(k): _canonical(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_canonical(v) for v in value]
    if isinstance(value, datetime | date | time):
        return value.isoformat()
    if isinstance(value, timedelta):
        return value.total_seconds()
    if isinstance(value, bytes):
        return value.hex()
    return value


def content_hash(raw: Mapping[str, Any], purpose: HashPurpose) -> str:
    """Hash a scenario file's parsed content, for binding an approval or a generated draft to it.

    It hashes the parsed YAML, not the validated model, so a schema field added later with a
    default does not change the hash of a file that does not write it. Comments, key order and
    the quoting of strings do not count either, but a change of a scalar's type does (an ISO
    timestamp string unquoted into a YAML timestamp, `1` written as `1.0`): such an edit fails
    safe, the hash differs and the file must be approved again. Left out: the whole `review` section (approving changes it)
    and `provenance.generated_hash` and `provenance.label_fingerprint` (they are recorded after
    the hash is taken). Everything else counts, labels and text alike.

    Args:
        raw: The parsed file (`yamlio.load_yaml`), or a model's `model_dump(mode="json")`.
        purpose: `approval` for `review.approved_hash`, `generated` for
            `provenance.generated_hash`. It is hashed with the content, so the same file has a
            different hash for each purpose.

    Returns:
        `"v1:"` followed by the hex SHA-256 of the purpose and the canonical JSON (sorted keys,
        UTF-8).
    """
    data = {k: v for k, v in _canonical(raw).items() if k != "review"}
    prov = data.get("provenance")
    if isinstance(prov, dict):
        data["provenance"] = {k: v for k, v in prov.items() if k not in ("generated_hash", "label_fingerprint")}
    blob = json.dumps(data, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str)
    material = f"{purpose}\n{blob}"
    return f"{CONTENT_HASH_VERSION}:{hashlib.sha256(material.encode('utf-8')).hexdigest()}"


ApprovalState = Literal["verified", "changed", "unverified", "missing"]


def approval_state(raw: Mapping[str, Any], s: Scenario) -> ApprovalState:
    """Check an approved scenario's content against the hash recorded when it was approved.

    Args:
        raw: The parsed file `s` was validated from.
        s: The scenario; its `review.status` should be `approved`.

    Returns:
        "verified" when `review.approved_hash` matches `content_hash(raw, "approval")`, "changed"
        when it is a current-version hash that does not match (edited after approval, or a
        generated hash copied in), "unverified" when there is none or it has an unknown version
        prefix in a file without the fields the current tooling writes (an older file), and
        "missing" when there is none or it has an unknown prefix in a file that has
        `provenance.generated_hash` or `provenance.label_fingerprint`: the tooling records a
        current-version hash on every approval, so the status (or the hash) was set by hand.
    """
    stored = s.review.approved_hash
    if stored is None or not stored.startswith(f"{CONTENT_HASH_VERSION}:"):
        prov = s.provenance
        return "missing" if prov.generated_hash or prov.label_fingerprint else "unverified"
    return "verified" if stored == content_hash(raw, "approval") else "changed"
