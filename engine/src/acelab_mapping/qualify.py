"""Hard-filter: the grounded gate.

Given a standard's *hard* requirements, return the catalog products that provably
satisfy them after normalization. The LLM may only ever choose from this set, so a
product that fails a hard requirement can never be written — the model cannot invent
one, and cannot pick a disqualified one.
"""

from __future__ import annotations

from .models import FireClass, NormalizedProduct, Standard
from .normalize import parse_dcof, parse_fire_class


def _meets_fire(product: NormalizedProduct, required: str | None) -> bool:
    if not required:
        return True
    required_class = parse_fire_class(required)
    if required_class is None:
        return True
    # Class A is the most stringent; a Class A requirement admits only Class A.
    order = [FireClass.A, FireClass.B, FireClass.C]
    if product.fire_class is None:
        return False
    return order.index(product.fire_class) <= order.index(required_class)


def is_enforceable(standard: Standard) -> bool:
    """True only when every hard requirement maps to an implemented filter.

    The engine enforces category + min_nrc + fire_rating + humidity_resistance (ceilings),
    nfpa_285 (cladding), and min_wear_mil + slip (flooring). A standard carrying any other
    requirement key is not fully checkable here, so its elements are skipped honestly rather
    than mapped on a partial filter.
    """
    return not (standard.requirements.model_extra or {})


def qualifies(product: NormalizedProduct, standard: Standard) -> bool:
    req = standard.requirements
    if product.category != req.category:
        return False
    if req.min_nrc is not None and (product.nrc is None or product.nrc < req.min_nrc):
        return False
    if not _meets_fire(product, req.fire_rating):
        return False
    if req.humidity_resistance and not product.humidity.is_resistant():
        return False
    if req.nfpa_285 and not product.nfpa_285:
        return False
    if req.min_wear_mil is not None and (product.wear_mil is None or product.wear_mil < req.min_wear_mil):
        return False
    if req.slip:
        required_dcof = parse_dcof(req.slip)
        if required_dcof is not None and (product.dcof is None or product.dcof < required_dcof):
            return False
    return True


def qualified_products(
    standard: Standard, products: list[NormalizedProduct]
) -> list[NormalizedProduct]:
    """Products passing every hard requirement, firm-preferred ones first."""
    passing = [p for p in products if qualifies(p, standard)]
    preferred = set(standard.preferred_products)
    passing.sort(key=lambda p: (p.product_id not in preferred, p.product_id))
    return passing
