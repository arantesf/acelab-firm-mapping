from pathlib import Path

from acelab_mapping.catalog import Catalog
from acelab_mapping.firm import load_standards
from acelab_mapping.models import Element
from acelab_mapping.qualify import qualified_products, qualifies

DATA = Path(__file__).parents[2] / "data"


def _standards():
    return load_standards(DATA / "firm-library.json")


def _catalog():
    return Catalog.load(DATA / "products.json")


def test_open_office_standard_qualifies_only_high_nrc_class_a():
    catalog, standards = _catalog(), _standards()
    open_office = standards[0]
    assert open_office.requirements.min_nrc == 0.8
    qualified = qualified_products(open_office, catalog.all())

    ids = {p.product_id for p in qualified}
    assert {"cl-1004", "cl-1022", "cl-1028"} <= ids  # firm-approved products pass
    for p in qualified:
        assert p.category == "Acoustic Ceilings"
        assert p.nrc is not None and p.nrc >= 0.8
        assert p.fire_class is not None and p.fire_class.value == "A"


def test_preferred_products_are_listed_first():
    catalog, standards = _catalog(), _standards()
    qualified = qualified_products(standards[0], catalog.all())
    preferred = set(standards[0].preferred_products)
    lead = [p.product_id for p in qualified[: len(preferred)]]
    assert set(lead) <= preferred


def test_humidity_standard_excludes_the_open_office_tile():
    # cl-1004 is approved for open offices but is only "Standard" humidity; the
    # wet-room standard must not accept it ("do not reuse the open-office tile here").
    catalog, standards = _catalog(), _standards()
    humidity = standards[1]
    assert humidity.requirements.humidity_resistance is True

    qualified_ids = {p.product_id for p in qualified_products(humidity, catalog.all())}
    assert {"cl-1038", "cl-1124"} <= qualified_ids
    assert "cl-1004" not in qualified_ids
    assert not qualifies(catalog.get("cl-1004"), humidity)


def test_cladding_standard_requires_nfpa_285():
    catalog, standards = _catalog(), _standards()
    cladding = next(s for s in standards if s.requirements.category == "Exterior Cladding")
    assert cladding.requirements.nfpa_285 is True
    qualified = qualified_products(cladding, catalog.all())
    ids = {p.product_id for p in qualified}
    assert set(cladding.preferred_products) <= ids  # approved cladding passes
    for p in qualified:
        assert p.category == "Exterior Cladding"
        assert p.nfpa_285 is True


def test_flooring_standard_enforces_wear_layer_and_slip():
    catalog, standards = _catalog(), _standards()
    flooring = next(s for s in standards if s.requirements.category == "Resilient Flooring")
    assert flooring.requirements.min_wear_mil == 20
    qualified = qualified_products(flooring, catalog.all())
    assert set(flooring.preferred_products) <= {p.product_id for p in qualified}
    for p in qualified:
        assert p.wear_mil is not None and p.wear_mil >= 20
        assert p.dcof is not None and p.dcof >= 0.42  # R10-only products are excluded


def test_all_standards_are_enforceable_now():
    from acelab_mapping.qualify import is_enforceable
    assert all(is_enforceable(s) for s in _standards())


def test_fake_decider_routes_ceiling_by_room_from_library_vocabulary():
    """The room fit is data-driven (the standard's own intent/context/tags), not hardcoded
    keyword lists: an Open Office ceiling routes to the office standard, a wet room to the
    humidity one."""
    from acelab_mapping.decide import Engine
    from acelab_mapping.decider import FakeDecider

    eng = Engine(_catalog(), _standards(), FakeDecider(), sync_date="2026-07-12")

    office = eng.decide_element(Element(element_id=1, category="Ceilings", type="Generic", room="Open Office"))
    assert office.action == "map"
    assert "open-plan office" in office.matched_standard

    wet = eng.decide_element(Element(element_id=2, category="Ceilings", type="Generic", room="Locker Room"))
    assert wet.action == "map"
    assert "high-humidity" in wet.matched_standard
