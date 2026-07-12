from pathlib import Path

from acelab_mapping.catalog import Catalog
from acelab_mapping.decide import Engine
from acelab_mapping.firm import load_standards
from acelab_mapping.models import Element

DATA = Path(__file__).parents[2] / "data"


class Boom:
    def decide(self, context):
        raise AssertionError("decider must not be called for an already-specified type")


def _catalog():
    return Catalog.load(DATA / "products.json")


def _engine(decider):
    return Engine(_catalog(), load_standards(DATA / "firm-library.json"), decider, sync_date="2026-07-12")


def test_find_by_name_matches_through_a_firm_prefix():
    catalog = _catalog()
    product = catalog.find_by_name("Acme - Northwind Quietude 300", "Acoustic Ceilings")
    assert product is not None and product.product_id == "cl-1004"


def test_find_by_name_ignores_generic_type_names():
    catalog = _catalog()
    assert catalog.find_by_name("Generic - Lay-in", "Acoustic Ceilings") is None
    assert catalog.find_by_name(None, "Acoustic Ceilings") is None


def test_already_specified_type_is_confirmed_not_re_decided():
    # 312071's type is already the product cl-1004; the engine must confirm it and
    # never call the LLM (which might re-spec it to a different approved tile).
    element = Element(element_id=312071, category="Ceilings", family="Compound Ceiling",
                      type="Acme - Northwind Quietude 300", level="Level 3", room="Open Office")
    d = _engine(Boom()).decide_element(element)
    assert d.action == "map"
    assert d.already_specified is True
    assert d.chosen_product.product_id == "cl-1004"
    assert d.revit_write.parameters["Acelab_Product_ID"] == "cl-1004"
    assert "already specified" in d.note
