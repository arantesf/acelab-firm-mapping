"""The decider is bounded on the output side: whatever it returns, an ungrounded
product, an unknown standard, an explicit abstention, or a low composite score all
collapse to an honest abstention rather than a written guess."""

from pathlib import Path

import pytest

from acelab_mapping.catalog import Catalog
from acelab_mapping.decide import Engine
from acelab_mapping.firm import load_standards
from acelab_mapping.models import DecisionContext, Element, RawDecision

DATA = Path(__file__).parents[2] / "data"
OPEN_OFFICE = "Acoustic ceilings in open-plan office areas"


class StubDecider:
    def __init__(self, decision: RawDecision) -> None:
        self._decision = decision

    def decide(self, context: DecisionContext) -> RawDecision:
        return self._decision


def _engine(decision: RawDecision) -> Engine:
    return Engine(
        Catalog.load(DATA / "products.json"),
        load_standards(DATA / "firm-library.json"),
        StubDecider(decision),
        sync_date="2026-07-12",
    )


def _open_office_ceiling(room: str | None = "Open Office") -> Element:
    return Element(element_id=999, category="Ceilings", family="Compound Ceiling",
                   type="Generic - Lay-in", level="Level 1", room=room)


def test_ungrounded_product_is_rejected_not_written():
    d = _engine(RawDecision(abstain=False, standard_id=OPEN_OFFICE,
                            product_id="cl-9999", confidence=0.99)).decide_element(_open_office_ceiling())
    assert d.action == "abstain"
    assert d.revit_write is None
    assert "outside the grounded candidate set" in d.note


def test_unknown_standard_is_rejected():
    d = _engine(RawDecision(abstain=False, standard_id="Something the firm never wrote",
                            product_id="cl-1004", confidence=0.99)).decide_element(_open_office_ceiling())
    assert d.action == "abstain"
    assert "outside the candidate set" in d.note


def test_explicit_model_abstention_is_honored():
    d = _engine(RawDecision(abstain=True, abstain_reason="room context is unclear")).decide_element(
        _open_office_ceiling())
    assert d.action == "abstain"
    assert d.note == "room context is unclear"


def test_low_composite_score_overrides_a_confident_pick():
    # Valid, grounded pick — but an unknown room plus low self-confidence must drive
    # the composite below threshold and force abstention (two-sided abstention).
    d = _engine(RawDecision(abstain=False, standard_id=OPEN_OFFICE,
                            product_id="cl-1004", confidence=0.2)).decide_element(
        _open_office_ceiling(room=None))
    assert d.action == "abstain"
    assert d.confidence is not None and d.confidence.band == "abstain"
    assert "below write/review threshold" in d.note


def test_grounded_confident_pick_is_written():
    d = _engine(RawDecision(abstain=False, standard_id=OPEN_OFFICE,
                            product_id="cl-1004", confidence=0.9)).decide_element(_open_office_ceiling())
    assert d.action == "map"
    assert d.revit_write.parameters["Acelab_Product_ID"] == "cl-1004"
