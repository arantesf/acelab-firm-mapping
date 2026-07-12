"""Golden expectation for every element in sample-model.json.

Pins the decided action of all 38 elements (deterministic FakeDecider), so the
whole model is covered, not just spot-checked. The chosen *product* is left to the
decider and asserted elsewhere via the grounding invariant; here we fix the
per-element *outcome*: every element in a covered category maps, and elements in a
category the firm library does not cover abstain (never map).
"""

import json
from pathlib import Path

import pytest

from acelab_mapping.catalog import Catalog
from acelab_mapping.decide import Engine
from acelab_mapping.decider import FakeDecider
from acelab_mapping.firm import load_standards
from acelab_mapping.models import Element

DATA = Path(__file__).parents[2] / "data"

CEILINGS = {312134, 312121, 312171, 312226, 312071, 312035, 312004,
            312089, 312187, 312297, 312280, 312249}
WALLS_EXTERIOR = {  # -> Exterior Cladding
    312306, 312316, 312454, 312417, 312373,   # exterior Basic Wall
    312447, 312351, 312402, 312315, 312473,   # Curtain Wall Spandrel
}
FLOORS = {312609, 312547, 312506, 312602, 312512, 312824, 312572}  # -> Resilient Flooring

MAP = CEILINGS | WALLS_EXTERIOR | FLOORS


UNCOVERED = {  # no same-category standard exists, so these abstain, never map
    312749, 312703,          # Doors
    312754, 312733,          # Windows
    312785,                  # Furniture
    312660, 312687,          # interior Basic Wall (Generic - 200mm)
    312625, 312672,          # interior partitions (Generic - 100mm Partition)
}


@pytest.fixture
def decisions():
    engine = Engine(
        Catalog.load(DATA / "products.json"),
        load_standards(DATA / "firm-library.json"),
        FakeDecider(),
        sync_date="2026-07-12",
    )
    model = json.loads((DATA / "sample-model.json").read_text(encoding="utf-8"))
    return {d.element_id: d for d in (engine.decide_element(Element(**e)) for e in model["elements"])}


def test_the_two_sets_partition_all_38_elements():
    assert len(MAP) + len(UNCOVERED) == 38
    assert not (MAP & UNCOVERED)


def test_covered_categories_map(decisions):
    for eid in MAP:
        assert decisions[eid].action == "map", eid


def test_uncovered_elements_are_not_mapped(decisions):
    # The Revit-category -> firm-category bridge narrows scope to same-category standards; an
    # element whose category maps to none (doors, windows, interior partitions) has no candidate
    # to choose from, so it abstains before any decider call — it is never mapped.
    for eid in UNCOVERED:
        assert decisions[eid].action == "abstain", eid
