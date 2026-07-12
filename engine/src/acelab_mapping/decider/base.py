"""The decider port.

The engine assembles a grounded `DecisionContext` and hands it to a `Decider`.
The LLM implementation lives behind this seam; a deterministic `FakeDecider`
implements the same contract so the whole pipeline runs and is tested without a
network call. Swapping providers is a one-file change, never a rewrite.

Two methods: `decide` for one context, and `decide_batch` for many. The engine's
whole-model path calls `decide_batch` — the same shape a hosted service exposes as
`POST /decisions` with a list of contexts and a list of decisions back. Where the
round-trips are batched, deduped or fanned out concurrently is the transport's
concern, hidden behind this port; `SequentialBatchDecider` gives the trivial default
for deciders (like the fake) that have nothing smarter to do.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from ..models import DecisionContext, RawDecision


@runtime_checkable
class Decider(Protocol):
    def decide(self, context: DecisionContext) -> RawDecision: ...

    def decide_batch(self, contexts: list[DecisionContext]) -> list[RawDecision]: ...


class SequentialBatchDecider:
    """Mixin: resolve a batch one context at a time. Deciders whose transport can do better —
    dedup, concurrency, a batched endpoint — override `decide_batch` (see `CachingDecider`)."""

    def decide_batch(self, contexts: list[DecisionContext]) -> list[RawDecision]:
        return [self.decide(context) for context in contexts]
