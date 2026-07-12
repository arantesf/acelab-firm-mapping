"""Dedup + record/replay wrapper around any decider.

Two problems it solves at once:
- Identical elements (e.g. eight Open Office ceilings) share one decision key, so the
  inner decider runs once, not eight times — cheaper, and it guarantees identical
  situations get identical decisions instead of drifting on model nondeterminism.
- With a `path`, the cache persists to disk: a live run records real LLM responses,
  and tests/CI replay them with no network call and no spend.

The key deliberately excludes element_id, level, and type — none of them change which
product applies. It is category + room + the exact candidate set the decider was shown.

`decide_batch` is where the transport lives: it collapses a whole model's contexts to the
*distinct* uncached keys, resolves those concurrently, then realigns to the input. Dedup
happens *before* dispatch, so no worker ever blocks waiting on another's duplicate — the
concurrency is spent only on genuinely different decisions. When the inner decider becomes
a remote service, this same method is where a batched `POST` or an async fan-out slots in.
"""

from __future__ import annotations

import hashlib
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Optional

from ..models import DecisionContext, RawDecision
from .base import Decider


class CachingDecider:
    def __init__(
        self,
        inner: Decider,
        path: Optional[Path] = None,
        namespace: str = "",
        max_workers: int = 8,
    ) -> None:
        self.inner = inner
        self.path = Path(path) if path else None
        self.namespace = namespace  # e.g. the model id; different models must not collide
        self.max_workers = max_workers  # concurrency for the distinct calls in a batch
        self._cache: dict[str, RawDecision] = {}
        self._lock = threading.Lock()  # guards the cache dict and the disk file
        if self.path and self.path.exists():
            stored = json.loads(self.path.read_text(encoding="utf-8"))
            self._cache = {k: RawDecision(**v) for k, v in stored.items()}

    def _key(self, context: DecisionContext) -> str:
        signature = {
            "ns": self.namespace,
            "category": context.element.category,
            "room": context.element.room,
            "exterior": context.element.exterior,
            "candidates": [
                {"id": c.id, "products": [p.product_id for p in c.qualified]}
                for c in context.candidates
            ],
        }
        blob = json.dumps(signature, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]

    def decide(self, context: DecisionContext) -> RawDecision:
        key = self._key(context)
        with self._lock:
            if key in self._cache:
                return self._cache[key]
        result = self.inner.decide(context)
        with self._lock:
            self._cache[key] = result
            self._persist()
        return result

    def decide_batch(self, contexts: list[DecisionContext]) -> list[RawDecision]:
        keys = [self._key(c) for c in contexts]
        with self._lock:
            # One representative context per distinct, not-yet-cached key.
            todo = {}
            for key, context in zip(keys, contexts):
                if key not in self._cache and key not in todo:
                    todo[key] = context

        if todo:
            items = list(todo.items())
            with ThreadPoolExecutor(max_workers=min(self.max_workers, len(items))) as pool:
                resolved = dict(
                    pool.map(lambda kc: (kc[0], self.inner.decide(kc[1])), items)
                )
            with self._lock:
                self._cache.update(resolved)
                self._persist()

        with self._lock:
            return [self._cache[key] for key in keys]

    def _persist(self) -> None:
        if not self.path:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {k: v.model_dump() for k, v in self._cache.items()}
        self.path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
