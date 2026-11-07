"""Core-port engines that differ from promote_all only in the two ways all_store does."""

from cortex_mem.access import knowledge_visible, owner_for
from cortex_mem.contract import Handoff, Knowledge, Promote

from harness.baselines.simple import _rank
from harness.contract import Event as HarnessEvent
from harness.contract import Query


class PromoteEveryKind:
    """Promotes every event but recalls, and keeps the store's search."""

    def observe(self, event, ctx):
        if event.kind == "recall":
            return []
        item = Knowledge(
            id=event.id, kind="knowledge", title=event.id, body=event.text, scope=event.scope,
            owner=owner_for(event), sources=(event.id,), conditions=event.conditions,
        )
        return [Promote(item)]

    def tick(self, now, ctx):
        return []

    def handoff(self, query, caller, scope, ctx, k=6):
        return [
            Handoff(id=item.id, content=item.body, sources=item.sources)
            for item, _ in ctx.knowledge.search(query, caller, scope, k=k)
        ]


class AllStoreOnCore(PromoteEveryKind):
    """Also ranks with all_store's BM25: should reproduce all_store on every probe.

    Reaches into the private `_rank` on purpose: this only tests the bridge, so no real engine
    should copy it.
    """

    def handoff(self, query, caller, scope, ctx, k=6):
        visible = {i.id for i in ctx.knowledge.all() if knowledge_visible(i, caller, scope)}
        events = [
            HarnessEvent(
                id=e.id, t=e.at, user=e.user, session=e.session, repo=e.repo or "", scope=e.scope,
                team=e.team, signal=e.signal, kind="agent_record", outcome=e.outcome, text=e.text,
            )
            for e in ctx.events.get(visible)
        ]
        q = Query(text=query, user=caller.user, team=caller.team, session=caller.session,
                  repo=caller.repo or "", t=caller.at, k=k)
        return [Handoff(id=r.sources[0], content=r.text, sources=tuple(r.sources)) for r in _rank(events, q)]
