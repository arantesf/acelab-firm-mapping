"""With `with_alternatives=True`, every mapped decision offers ranked, grounded alternatives.

Alternatives are opt-in (the default artifact carries only the best pick). When requested, the
guarantees are the same as for the single pick: every option is in the catalog, qualifies for the
matched standard, carries its own composite confidence, and has re-derived write parameters.
`alternatives[0]` is the engine's own primary and mirrors `chosen_product`.
"""

import json
from pathlib import Path

import pytest

from acelab_mapping.catalog import Catalog
from acelab_mapping.decide import Engine
from acelab_mapping.decider import FakeDecider
from acelab_mapping.firm import load_standards
from acelab_mapping.models import DecisionContext, Element, RawAlternative, RawDecision
from acelab_mapping.qualify import qualifies

DATA = Path(__file__).parents[2] / "data"
OPEN_OFFICE = "Acoustic ceilings in open-plan office areas"


@pytest.fixture
def engine():
    catalog = Catalog.load(DATA / "products.json")
    standards = load_standards(DATA / "firm-library.json")
    return Engine(catalog, standards, FakeDecider(), sync_date="2026-07-12", with_alternatives=True), catalog, standards


@pytest.fixture
def decisions(engine):
    eng, _, _ = engine
    model = json.loads((DATA / "sample-model.json").read_text(encoding="utf-8"))
    return [eng.decide_element(Element(**e)) for e in model["elements"]]


def test_every_mapped_decision_has_grounded_alternatives(engine, decisions):
    _, catalog, standards = engine
    by_intent = {s.intent: s for s in standards}
    for d in decisions:
        if d.action != "map":
            assert not d.alternatives
            continue

        assert len(d.alternatives) >= 1
        # primary first, mirroring chosen_product and the decision's confidence
        assert d.alternatives[0].product_id == d.chosen_product.product_id
        assert d.alternatives[0].confidence == d.confidence

        seen = set()
        for alt in d.alternatives:
            assert alt.product_id not in seen  # no duplicates
            seen.add(alt.product_id)
            product = catalog.get(alt.product_id)
            assert product is not None  # grounded: exists in the catalog
            if not d.already_specified:  # a confirmed type may name a product no standard covers
                assert qualifies(product, by_intent[d.matched_standard])
            # each option is independently applicable: its own re-derived write
            assert alt.revit_write.parameters["Acelab_Product_ID"] == alt.product_id

        # only the top 3 carry a score; they come first, the rest are unscored overrides
        scored = [a for a in d.alternatives if a.confidence is not None]
        assert 1 <= len(scored) <= 3
        assert all(a.confidence is not None for a in d.alternatives[: len(scored)])
        assert all(a.confidence is None for a in d.alternatives[len(scored):])


class _Stub:
    def __init__(self, decision):
        self._decision = decision

    def decide(self, context):
        return self._decision


def test_bogus_alternative_ids_are_dropped_and_list_is_padded():
    catalog = Catalog.load(DATA / "products.json")
    standards = load_standards(DATA / "firm-library.json")
    raw = RawDecision(
        abstain=False, standard_id=OPEN_OFFICE, product_id="cl-1004", confidence=0.9,
        alternatives=[RawAlternative(product_id="cl-9999", reason="not real")],
    )
    engine = Engine(catalog, standards, _Stub(raw), sync_date="2026-07-12", with_alternatives=True)
    d = engine.decide_element(
        Element(element_id=999, category="Ceilings", family="Compound Ceiling",
                type="Generic - Lay-in", level="Level 1", room="Open Office")
    )
    assert d.action == "map"
    ids = [a.product_id for a in d.alternatives]
    assert "cl-9999" not in ids  # ungrounded runner-up rejected
    assert ids[0] == "cl-1004"
    # padded from the grounded shortlist up to three real options
    assert len(d.alternatives) >= 2
    by_intent = {s.intent: s for s in standards}
    for a in d.alternatives:
        assert qualifies(catalog.get(a.product_id), by_intent[OPEN_OFFICE])
