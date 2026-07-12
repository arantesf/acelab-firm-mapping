"""Firm library: load standards and the one deterministic, data-safe signal.

The library expresses *intent*, not an element-type -> product table. The only rule
decided here is the Revit-category -> firm-category bridge. Whether a standard applies
to an element, and how well its room/context fits, is judged by the decider from the
standard's own intent/context/tags — never from keyword lists baked into this code.
"""

from __future__ import annotations

import json
from pathlib import Path

from .models import Standard

# Revit category -> firm category. Interior partitions are intentionally absent:
# the firm library does not cover them, so they abstain (flagged for review), not guessed.
_REVIT_TO_FIRM = {
    "Ceilings": "Acoustic Ceilings",
    "Floors": "Resilient Flooring",
    "Walls": "Exterior Cladding",  # exterior only; see map_category
}


def map_category(element) -> str | None:
    """Firm category for an element, or None when the library does not cover it."""
    firm = _REVIT_TO_FIRM.get(element.category)
    if firm == "Exterior Cladding" and not element.exterior:
        return None
    return firm


def load_standards(path: Path) -> list[Standard]:
    doc = json.loads(Path(path).read_text(encoding="utf-8"))
    return [Standard(**s) for s in doc["standards"]]
