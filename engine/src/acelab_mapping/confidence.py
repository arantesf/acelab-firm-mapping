"""Composite, auditable confidence.

The decider's self-reported confidence is only one input. It is tempered by
structural signals the model cannot fake — how well the room matched, whether the
product is firm-approved, and whether it violates a lesson-learned — so the number
reflects the evidence, not the model's mood. Every component is reported in the
artifact and the band drives a two-sided abstention: code can abstain even when the
model was confident.
"""

from __future__ import annotations

from .models import Confidence

WRITE_THRESHOLD = 0.75
REVIEW_THRESHOLD = 0.55

_ROOM_SCORE = {"match": 1.0, "weak": 0.5, "none": 0.4}


def _clamp(x: float) -> float:
    return max(0.0, min(1.0, x))


def composite(
    llm_confidence: float, room_fit: str, is_preferred: bool, has_violations: bool
) -> Confidence:
    room = _ROOM_SCORE.get(room_fit, 0.4)
    approved = 1.0 if is_preferred else 0.7
    llm = _clamp(llm_confidence)

    base = 0.40 * room + 0.25 * approved + 0.35 * llm
    score = _clamp(base * 0.6 if has_violations else base)

    band = "write" if score >= WRITE_THRESHOLD else "review" if score >= REVIEW_THRESHOLD else "abstain"
    return Confidence(
        score=round(score, 3),
        band=band,
        components={
            "room": round(room, 3),
            "approved": round(approved, 3),
            "llm": round(llm, 3),
            "lessons_penalty": 0.6 if has_violations else 1.0,
        },
    )
