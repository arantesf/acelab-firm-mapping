"""Deterministic normalization of messy catalog attributes.

This is where "keep the model honest" starts: the LLM never parses a numeric or a
label. Code turns the heterogeneous strings into typed, canonical values, and any
value the engine later writes to Revit is re-derived from here — not from the model.

Every parser is total: unparseable input yields `None`/`UNKNOWN`, never an
exception, so one bad row cannot break a run.
"""

from __future__ import annotations

import re
from typing import Any, Optional

from .models import FireClass, HumidityLevel, Material, NormalizedProduct, RawProduct

_NUMBER = re.compile(r"-?\d*\.?\d+")


def parse_nrc(raw: Any) -> Optional[float]:
    """`.90`, `0.80 (NRC)`, `0.8`, `0.85`, `"0.65"` -> float in [0, 1]."""
    if isinstance(raw, (int, float)):
        value = float(raw)
    elif isinstance(raw, str):
        match = _NUMBER.search(raw)
        if not match:
            return None
        value = float(match.group())
    else:
        return None
    return value if 0.0 <= value <= 1.0 else None


# Class A == Class 1 == Class I (ASTM E84, flame spread 0-25); B == 2 == II; C == 3 == III.
_FIRE_TOKENS = {
    "A": FireClass.A, "1": FireClass.A, "I": FireClass.A,
    "B": FireClass.B, "2": FireClass.B, "II": FireClass.B,
    "C": FireClass.C, "3": FireClass.C, "III": FireClass.C,
}


def parse_fire_class(raw: Any) -> Optional[FireClass]:
    """Collapse the many labels ("ASTM E84 Class A", "Class A / Class 1", "A",
    "Class A (0-25)", "Class 1") onto a canonical class. Only tokens that follow
    the word CLASS are considered, so "E84"/"ASTM" never leak a false match."""
    if not isinstance(raw, str):
        return None
    text = raw.upper()
    found: set[FireClass] = set()
    for class_token in re.findall(r"CLASS\s+([A-Z0-9]+)", text):
        if class_token in _FIRE_TOKENS:
            found.add(_FIRE_TOKENS[class_token])
    if not found and text.strip() in _FIRE_TOKENS:  # bare "A"
        found.add(_FIRE_TOKENS[text.strip()])
    if not found:
        return None
    # A single product may carry equivalent labels ("Class A / Class 1"); if it
    # ever names distinct classes, report the most stringent (A < B < C).
    return min(found, key=lambda c: [FireClass.A, FireClass.B, FireClass.C].index(c))


def parse_humidity(raw: Any) -> HumidityLevel:
    """`High`, `Standard`, `Moderate`, `yes`/`Yes`/`True`, missing -> level."""
    if raw is True:
        return HumidityLevel.YES
    if raw is False or raw is None:
        return HumidityLevel.UNKNOWN
    if not isinstance(raw, str):
        return HumidityLevel.UNKNOWN
    value = raw.strip().lower()
    return {
        "high": HumidityLevel.HIGH,
        "moderate": HumidityLevel.MODERATE,
        "standard": HumidityLevel.STANDARD,
        "yes": HumidityLevel.YES,
        "true": HumidityLevel.YES,
    }.get(value, HumidityLevel.UNKNOWN)


def parse_material(raw: Any) -> Optional[Material]:
    if not isinstance(raw, str):
        return None
    value = raw.strip().lower()
    if "mineral" in value:
        return Material.MINERAL_FIBER
    if "fiberglass" in value or "glass" in value:
        return Material.FIBERGLASS
    if "metal" in value:
        return Material.METAL
    if "wood" in value:
        return Material.WOOD
    return Material.OTHER


def parse_nfpa_285(raw: Any) -> Optional[bool]:
    """`True`, `Yes`, `Pass`, `Compliant`, `Compliant (assembly-dependent)` -> True.

    Missing -> None (unknown, not a failure). "Assembly-dependent" still counts as
    compliant here; the caveat is left for the decider to weigh via lessons-learned.
    """
    if raw is True:
        return True
    if raw is False:
        return False
    if not isinstance(raw, str):
        return None
    value = raw.strip().lower()
    if not value:
        return None
    return any(token in value for token in ("yes", "pass", "compliant", "true"))


def parse_wear_mil(raw: Any) -> Optional[float]:
    """Wear-layer thickness to mils, across mixed units: `20mil`, `28 mil`, `0.5mm`.

    1 mil = 0.0254 mm; inches are x1000. A bare number is read as mils.
    """
    if isinstance(raw, (int, float)):
        return float(raw)
    if not isinstance(raw, str):
        return None
    match = _NUMBER.search(raw)
    if not match:
        return None
    value = float(match.group())
    text = raw.lower()
    if "mm" in text:
        return value / 0.0254
    if '"' in raw or "inch" in text or re.search(r"\bin\b", text):
        return value * 1000.0
    return value


def parse_dcof(raw: Any) -> Optional[float]:
    """Static coefficient of friction: `0.42`, `DCOF 0.42`, `DCOF >= 0.42`, `>0.42 wet`.

    Ramp ratings like `R10` carry no DCOF value and cannot be verified -> None.
    """
    if isinstance(raw, (int, float)):
        value = float(raw)
        return value if 0.0 <= value <= 1.0 else None
    if not isinstance(raw, str):
        return None
    match = _NUMBER.search(raw)
    if not match:
        return None
    value = float(match.group())
    return value if 0.0 <= value <= 1.0 else None


def normalize_product(raw: RawProduct) -> NormalizedProduct:
    attrs = raw.attributes
    return NormalizedProduct(
        product_id=raw.product_id,
        category=raw.category,
        name=raw.name,
        manufacturer=raw.manufacturer,
        url=raw.acelab_url,
        nrc=parse_nrc(attrs.get("nrc")),
        fire_class=parse_fire_class(attrs.get("fire_rating")),
        humidity=parse_humidity(attrs.get("humidity_resistance")),
        material=parse_material(attrs.get("material")),
        nfpa_285=parse_nfpa_285(attrs.get("nfpa_285")),
        wear_mil=parse_wear_mil(attrs.get("wear_layer")),
        dcof=parse_dcof(attrs.get("slip_resistance")),
        raw_attributes=attrs,
    )
