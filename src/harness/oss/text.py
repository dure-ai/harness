"""Body text, query text, namespace ids and visibility units shared by the OSS adapters."""

import hashlib

from harness.contract import Event, Outcome, Query, Signal

_SIGNAL_PHRASES: dict[tuple[Signal, Outcome], str] = {
    ("test", "success"): "(tests passed)",
    ("test", "failure"): "(tests failed)",
    ("test", "unknown"): "(tests ran)",
    ("review", "success"): "(review approved)",
    ("review", "failure"): "(review rejected)",
    ("review", "unknown"): "(reviewed)",
    ("human", "success"): "(confirmed by a person)",
    ("human", "failure"): "(rejected by a person)",
    ("human", "unknown"): "(noted by a person)",
    ("doc", "success"): "(from docs)",
    ("doc", "failure"): "(from docs)",
    ("doc", "unknown"): "(from docs)",
}


def _prefix(repo: str, meta: dict[str, str]) -> str:
    head = f"[{repo}] "
    if meta:
        head += "{" + ", ".join(f"{k}={v}" for k, v in sorted(meta.items())) + "} "
    return head


def format_event(event: Event) -> str:
    """Build the body text an engine stores for one recorded event.

    Args:
        event: The observation to format. Its id and `refs` are left out, since they mean
            nothing to an open-source engine.

    Returns:
        Text shaped like "[repo] {k=v, ...} user: text (commit) (signal phrase)". The meta
        part is left out when empty, and so is any suffix with no words in it.
    """
    suffix_parts: list[str] = []
    if event.kind == "commit":
        suffix_parts.append("(commit)")
    phrase = _SIGNAL_PHRASES.get((event.signal, event.outcome))
    if phrase:
        suffix_parts.append(phrase)
    body = f"{_prefix(event.repo, event.meta)}{event.user}: {event.text}"
    suffix = " ".join(suffix_parts)
    return f"{body} {suffix}" if suffix else body


def format_query(query: Query) -> str:
    """Build the query text an engine searches with.

    Args:
        query: The question the simulated agent asks.

    Returns:
        Text shaped like "[repo] {k=v, ...} question". It has no author, unlike event bodies.
    """
    return f"{_prefix(query.repo, query.meta)}{query.text}"


def engine_ns(namespace: str) -> str:
    """Derive the engine-side namespace id from the replay's namespace.

    The replay's namespace is already opaque: it holds no scenario id, only a keyed HMAC digest
    of it. Hashing again is for character limits, not for hiding anything: the result uses only
    lowercase letters, digits and dashes, so it fits URL paths and filter values unchanged.
    It stays a pure function of the namespace, so a different namespace format cannot leak
    scenario information through it.

    Args:
        namespace: The replay namespace, e.g. "<run_id>-r<round>:<16 hex digits>"; the part after
            the colon is a keyed HMAC of the scenario id that only the run's blind seed can
            reproduce.

    Returns:
        "t5-" followed by the first 24 hex digits of the SHA-256 of `namespace`.
    """
    return "t5-" + hashlib.sha256(namespace.encode()).hexdigest()[:24]


def event_unit(event: Event) -> str:
    """Name the visibility unit an event belongs to.

    Args:
        event: The event to classify.

    Returns:
        "user:<user>" for a personal event, otherwise "team:<team>".
    """
    if event.scope == "personal":
        return f"user:{event.user}"
    return f"team:{event.team}"


def query_units(query: Query) -> list[str]:
    """List the visibility units a query may see.

    Args:
        query: The question being asked.

    Returns:
        The asker's own unit followed by the asker's team unit: ["user:...", "team:..."].
    """
    return [f"user:{query.user}", f"team:{query.team}"]
