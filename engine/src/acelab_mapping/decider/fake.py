"""Deterministic stand-in for the LLM.

NOT the product's intelligence — it exists so the pipeline can run and be tested
offline, and as a transparent baseline. It reads each standard's OWN language (intent
+ context + tags, all from the firm library) to judge whether that standard governs an
element (its category and exterior context) and how well its room fits, then picks the
first firm-approved qualified product. There are no keyword lists in this file. It
cannot weigh the lessons-learned prose — that is exactly the judgment the real LLM adds.
"""

from __future__ import annotations

import re

from ..models import CandidateStandard, DecisionContext, Element, RawAlternative, RawDecision
from .base import SequentialBatchDecider

_RANK = {"match": 2, "weak": 1, "none": 0}
_CONFIDENCE = {"match": 0.9, "weak": 0.6, "none": 0.45}


def _text(candidate: CandidateStandard) -> str:
    return " ".join(
        [candidate.intent or "", candidate.context or "", " ".join(candidate.tags)]
    ).lower()


def _mentions(source: str | None, text: str) -> bool:
    """True if any word of `source` appears in the standard's text (substring, so 'restroom'
    matches 'restrooms')."""
    return any(w in text for w in re.findall(r"[a-z]{3,}", (source or "").lower()))


def _fit(candidate: CandidateStandard, element: Element) -> str | None:
    """Room fit if this standard plausibly governs the element, else None (does not apply)."""
    text = _text(candidate)
    if not _mentions(element.category, text):
        return None  # the standard does not describe this kind of element
    if "exterior" in text and not element.exterior:
        return None  # standard is for the exterior envelope; this element is not exterior
    if element.room:
        return "match" if _mentions(element.room, text) else "weak"
    return "match" if element.exterior else "weak"


class FakeDecider(SequentialBatchDecider):
    def decide(self, context: DecisionContext) -> RawDecision:
        applicable = [
            (c, fit)
            for c in context.candidates
            if c.qualified and (fit := _fit(c, context.element)) is not None
        ]
        if not applicable:
            return RawDecision(
                abstain=True,
                abstain_reason="no firm standard describes this element",
            )
        best, fit = max(applicable, key=lambda cf: _RANK[cf[1]])
        product = next((p for p in best.qualified if p.is_preferred), best.qualified[0])
        # Offer up to two runners-up from the same standard, approved-first, minus the primary —
        # the deterministic stand-in for the LLM's ranked alternatives.
        others = [p for p in best.qualified if p.product_id != product.product_id]
        others.sort(key=lambda p: not p.is_preferred)
        alternatives = [
            RawAlternative(
                product_id=p.product_id,
                reason=("firm-approved alternative" if p.is_preferred else "qualifying alternative"),
            )
            for p in others[:2]
        ]
        return RawDecision(
            abstain=False,
            standard_id=best.id,
            product_id=product.product_id,
            confidence=_CONFIDENCE[fit],
            room_fit=fit,
            rationale=(
                f"Room fit '{fit}' for standard '{best.intent}'; "
                f"selected approved product {product.product_id}."
            ),
            alternatives=alternatives,
        )
