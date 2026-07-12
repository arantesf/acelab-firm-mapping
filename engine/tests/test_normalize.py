import json
from pathlib import Path

import pytest

from acelab_mapping.models import FireClass, HumidityLevel, Material, RawProduct
from acelab_mapping.normalize import (
    normalize_product,
    parse_dcof,
    parse_fire_class,
    parse_humidity,
    parse_material,
    parse_nfpa_285,
    parse_nrc,
    parse_wear_mil,
)

DATA = Path(__file__).parents[2] / "data"


@pytest.mark.parametrize(
    "raw,expected",
    [
        (".90", 0.90),
        ("0.80 (NRC)", 0.80),
        ("0.8", 0.80),
        (0.85, 0.85),
        (".65", 0.65),
        ("0.65 (NRC)", 0.65),
        ("0.90", 0.90),
        (0.7, 0.70),
    ],
)
def test_parse_nrc_messy_forms(raw, expected):
    assert parse_nrc(raw) == pytest.approx(expected)


@pytest.mark.parametrize("raw", ["N/A", "", None, {}, 5.0, -1])
def test_parse_nrc_rejects_junk_and_out_of_range(raw):
    assert parse_nrc(raw) is None


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("ASTM E84 Class A", FireClass.A),
        ("Class A / Class 1", FireClass.A),
        ("A", FireClass.A),
        ("Class A (0-25)", FireClass.A),
        ("Class 1", FireClass.A),  # Class 1 == Class A (ASTM E84)
        ("Class A", FireClass.A),
        ("Class B", FireClass.B),
        ("Class 2", FireClass.B),
        ("Class C", FireClass.C),
    ],
)
def test_parse_fire_class_equivalences(raw, expected):
    assert parse_fire_class(raw) == expected


@pytest.mark.parametrize("raw", ["", "unrated", None, 3])
def test_parse_fire_class_unknown(raw):
    assert parse_fire_class(raw) is None


def test_fire_class_does_not_leak_from_astm_or_e84():
    # "ASTM"/"E84" contain letters/digits that must not be read as a class token.
    assert parse_fire_class("ASTM E84") is None


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("High", HumidityLevel.HIGH),
        ("Standard", HumidityLevel.STANDARD),
        ("Moderate", HumidityLevel.MODERATE),
        ("yes", HumidityLevel.YES),
        ("Yes", HumidityLevel.YES),
        (True, HumidityLevel.YES),
        (None, HumidityLevel.UNKNOWN),
        ("weird", HumidityLevel.UNKNOWN),
    ],
)
def test_parse_humidity(raw, expected):
    assert parse_humidity(raw) == expected


@pytest.mark.parametrize(
    "level,resistant",
    [
        (HumidityLevel.HIGH, True),
        (HumidityLevel.YES, True),
        (HumidityLevel.MODERATE, False),
        (HumidityLevel.STANDARD, False),
        (HumidityLevel.UNKNOWN, False),
    ],
)
def test_humidity_resistance_judgment(level, resistant):
    assert level.is_resistant() is resistant


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("Mineral fiber", Material.MINERAL_FIBER),
        ("Mineral Fiber", Material.MINERAL_FIBER),
        ("Fiberglass", Material.FIBERGLASS),
        ("Metal", Material.METAL),
        ("Wood", Material.WOOD),
        ("Recycled PET", Material.OTHER),
    ],
)
def test_parse_material(raw, expected):
    assert parse_material(raw) == expected


@pytest.mark.parametrize(
    "raw,expected",
    [
        (True, True),
        ("Yes", True),
        ("Pass", True),
        ("Compliant", True),
        ("Compliant (assembly-dependent)", True),
        (None, None),
        ("", None),
    ],
)
def test_parse_nfpa_285(raw, expected):
    assert parse_nfpa_285(raw) is expected


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("20mil", 20.0),
        ("28 mil", 28.0),
        ("12 mil", 12.0),
        ("0.5mm", 19.685),   # 0.5 mm converted to mil ~ just under the 20-mil rule
        ('0.02"', 20.0),     # inches -> mil
    ],
)
def test_parse_wear_mil_mixed_units(raw, expected):
    assert parse_wear_mil(raw) == pytest.approx(expected, abs=0.01)


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("0.42", 0.42),
        ("DCOF 0.42", 0.42),
        ("DCOF >= 0.42", 0.42),
        (">0.42 wet", 0.42),
        ("R10", None),      # ramp rating, no DCOF value -> unverifiable
        ("", None),
    ],
)
def test_parse_dcof(raw, expected):
    result = parse_dcof(raw)
    if expected is None:
        assert result is None
    else:
        assert result == pytest.approx(expected)


def test_normalize_product_carries_provenance():
    raw = RawProduct(
        product_id="cl-1004",
        category="Acoustic Ceilings",
        name="Northwind Quietude 300",
        manufacturer="Northwind Ceilings",
        acelab_url="https://material-hub.acelabusa.com/products/cl-1004",
        attributes={"nrc": "0.80 (NRC)", "fire_rating": "Class A / Class 1",
                    "humidity_resistance": "Standard", "material": "Mineral fiber"},
    )
    p = normalize_product(raw)
    assert p.nrc == pytest.approx(0.80)
    assert p.fire_class is FireClass.A
    assert p.humidity is HumidityLevel.STANDARD
    assert p.material is Material.MINERAL_FIBER
    assert p.url == "https://material-hub.acelabusa.com/products/cl-1004"
    assert p.raw_attributes["nrc"] == "0.80 (NRC)"  # provenance retained


def _load_products() -> list[RawProduct]:
    doc = json.loads((DATA / "products.json").read_text(encoding="utf-8"))
    return [RawProduct(**p) for p in doc["products"]]


def test_normalization_is_total_over_the_real_catalog():
    for raw in _load_products():
        normalize_product(raw)  # must never raise


def test_ceiling_attributes_are_well_covered():
    ceilings = [normalize_product(p) for p in _load_products()
                if p.category == "Acoustic Ceilings"]
    assert len(ceilings) > 100
    parsed_nrc = sum(p.nrc is not None for p in ceilings) / len(ceilings)
    parsed_fire = sum(p.fire_class is not None for p in ceilings) / len(ceilings)
    assert parsed_nrc > 0.95, f"only {parsed_nrc:.0%} of NRC values parsed"
    assert parsed_fire > 0.95, f"only {parsed_fire:.0%} of fire ratings parsed"
