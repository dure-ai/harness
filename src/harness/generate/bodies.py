"""Body writing: ask the model for the text of a skeleton and record where it came from."""

from collections.abc import Sequence
from datetime import UTC, datetime

from harness.generate.candidates import FactCandidate, load_prompt
from harness.generate.config import GenConfig
from harness.generate.llm import LLMClient
from harness.generate.slots import BodySlots, Skeleton, apply_slots, slots_for, slots_prompt_block
from harness.generate.traces import Trace, compress
from harness.scenario.model import Evidence, Scenario

_STYLE_CHARS = 4000


def _prompt(sk: Skeleton, cand: FactCandidate, traces: Sequence[Trace]) -> str:
    """The user prompt: the candidate with its quote, the slot block and the start of the trace."""
    cond = ", ".join(f"{k}={v}" for k, v in cand.condition.items()) or "none"
    parts = [
        f"Repository: {traces[0].repo if traces else 'manual'}",
        "Original fact candidate (from the source trace):",
        f"- statement: {cand.statement}",
        f"- category: {cand.kind}",
        f"- condition: {cond}",
        f"- fix: {cand.fix or 'none'}",
        f"- quote (message #{cand.message_index}): {cand.quote}",
        "",
        "Slots to fill (labels are decided; write text only):",
        slots_prompt_block(slots_for(sk, cand)),
    ]
    if traces:
        parts += ["", f"Start of the source trace, for style only:\n{compress(traces[0])[:_STYLE_CHARS]}"]
    return "\n".join(parts)


def write_bodies(
    client: LLMClient,
    sk: Skeleton,
    cand: FactCandidate,
    traces: Sequence[Trace],
    gen: GenConfig,
) -> Scenario:
    """Have the model write every text field of a skeleton.

    The reply is applied with `apply_slots`, so labels stay as the template set them. A reply
    whose keys do not match the slots is sent back once with the error (see `LLMClient`).

    Args:
        client: LLM client.
        sk: The skeleton from a family template.
        cand: The candidate the skeleton was built from.
        traces: Traces of the candidate's bundle, the candidate's own first; may be empty.
        gen: Generation config (prompt version and banned cue words).

    Returns:
        The scenario with text filled in and provenance written: author `llm`, provider, the model
        that served the reply (the configured one when the provider does not say), prompt version, time of generation and one evidence record per event.

    Raises:
        LLMError: If the call fails or the reply is still invalid after one retry.
    """
    system = load_prompt("bodies.md").replace("{banned_cue_words}", ", ".join(gen.banned_cue_words))

    def check(slots: BodySlots) -> None:
        apply_slots(sk, slots)

    slots = client.complete_json("bodies", system, _prompt(sk, cand, traces), BodySlots, check)
    s = apply_slots(sk, slots)
    trace_id = traces[0].trajectory_id if traces else None
    evidence = []
    for e in s.events:
        origin = slots.events[e.id].origin
        quoted = origin == "quoted"
        evidence.append(
            Evidence(
                event_id=e.id,
                trace_id=trace_id if quoted else None,
                message_index=cand.message_index if quoted else None,
                quote=cand.quote if quoted else None,
                origin=origin,
            )
        )
    prov = s.provenance
    prov.author = "llm"
    prov.provider = client.name
    prov.llm_model = client.last_model or client.model
    prov.prompt_version = gen.prompt_version
    prov.generated_at = datetime.now(UTC)
    prov.evidence = evidence
    return s
