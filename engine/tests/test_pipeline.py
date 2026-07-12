import json
from pathlib import Path

import pytest

from acelab_mapping.catalog import Catalog
from acelab_mapping.decide import Engine
from acelab_mapping.decider import FakeDecider
from acelab_mapping.firm import load_standards
from acelab_mapping.models import Element
from acelab_mapping.qualify import qualifies

DATA = Path(__file__).parents[2] / "data"


@pytest.fixture
def engine():
    catalog = Catalog.load(DATA / "products.json")
    standards = load_standards(DATA / "firm-library.json")
    return Engine(catalog, standards, FakeDecider(), sync_date="2026-07-12"), catalog, standards


@pytest.fixture
def decisions(engine):
    eng, _, _ = engine
    model = json.loads((DATA / "sample-model.json").read_text(encoding="utf-8"))
    return {d.element_id: d for d in (eng.decide_element(Element(**e)) for e in model["elements"])}


def test_grounding_invariant_no_mapped_decision_is_ungrounded(engine, decisions):
    """Every mapped product exists in the catalog, qualifies for the matched
    standard, and every written value is re-derivable from that product."""
    _, catalog, standards = engine
    by_intent = {s.intent: s for s in standards}
    for d in decisions.values():
        if d.action != "map":
            continue
        product = catalog.get(d.chosen_product.product_id)
        assert product is not None
        assert qualifies(product, by_intent[d.matched_standard])
        params = d.revit_write.parameters
        assert params["Acelab_Product_ID"] == product.product_id
        assert params["Acelab_Manufacturer"] == (product.manufacturer or "")
        assert params["Acelab_Product_URL"] == (product.url or "")


def test_open_office_ceiling_maps_to_approved_acoustic_tile(engine, decisions):
    _, _, standards = engine
    open_office = next(s for s in standards if "open-plan office" in s.intent)
    d = decisions[312121]  # Generic - Lay-in, Open Office
    assert d.action == "map"
    assert d.matched_standard == open_office.intent
    assert d.chosen_product.product_id in open_office.preferred_products


def test_wet_room_ceiling_gets_humidity_resistant_not_the_office_tile(decisions):
    for element_id in (312249, 312280, 312297):  # Restroom, Locker, Pool Deck
        d = decisions[element_id]
        assert d.action == "map"
        assert "high-humidity" in d.matched_standard
        assert d.chosen_product.product_id != "cl-1004"  # the office tile must not leak here


def test_uncovered_elements_are_not_mapped(decisions):
    # The decider judges applicability itself now (no hardcoded category table); elements no
    # firm standard describes must never be mapped — they are left for a human, not guessed.
    for element_id in (312749, 312754, 312785, 312625):  # Door, Window, Furniture, interior wall
        assert decisions[element_id].action != "map"


def test_decide_all_matches_per_element(engine):
    # The whole-model batch path must produce exactly what element-by-element does — same order,
    # same decisions. It only changes *how* the decider is called (one batch vs many calls).
    eng, _, _ = engine
    elements = [Element(**e) for e in json.loads((DATA / "sample-model.json").read_text())["elements"]]
    batch = eng.decide_all(elements)
    one_by_one = [eng.decide_element(e) for e in elements]
    assert [d.model_dump() for d in batch] == [d.model_dump() for d in one_by_one]


def test_summary_counts_are_consistent(decisions):
    actions = [d.action for d in decisions.values()]
    assert len(actions) == 38
    # ceilings + exterior walls (cladding) + floors (resilient) all map; the categories the
    # firm library does not describe are left unmapped (the decider abstains on them).
    assert actions.count("map") == 29
    assert actions.count("map") + actions.count("skip") + actions.count("abstain") == 38
