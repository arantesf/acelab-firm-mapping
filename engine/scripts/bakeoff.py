"""Small model bake-off over the ceiling elements.

Chooses the decider model by the property that matters here — reliable, grounded,
lessons-aware structured decisions — instead of by brand. Cheap: a handful of unique
calls per model. Set OPENROUTER_API_KEY and run with the engine venv:

    PYTHONPATH=src ./.venv/Scripts/python.exe scripts/bakeoff.py
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from acelab_mapping.catalog import Catalog
from acelab_mapping.decide import Engine
from acelab_mapping.decider.caching import CachingDecider
from acelab_mapping.decider.openrouter import OpenRouterDecider
from acelab_mapping.firm import load_standards
from acelab_mapping.models import Element

DATA = Path(__file__).resolve().parents[3] / "SDK" / "revit-mapping" / "data"
CACHE_DIR = Path(__file__).resolve().parents[1] / "artifacts" / "bakeoff"

MODELS = ["openai/gpt-4o-mini", "openai/gpt-4.1-mini", "google/gemini-2.5-flash"]

OPEN_OFFICE = {312134, 312121, 312171, 312226, 312071, 312035, 312004, 312089, 312187}
WET = {312297, 312280, 312249}
OPEN_OFFICE_STD = "Acoustic ceilings in open-plan office areas"
WET_STD = "Ceilings in high-humidity rooms (restrooms, locker/natatorium)"


def _ceilings() -> list[Element]:
    model = json.loads((DATA / "sample-model.json").read_text(encoding="utf-8"))
    elems = [Element(**e) for e in model["elements"]]
    return [e for e in elems if e.element_id in OPEN_OFFICE | WET]


def _expected_standard(eid: int) -> str:
    return OPEN_OFFICE_STD if eid in OPEN_OFFICE else WET_STD


def run_model(model: str, catalog: Catalog, standards) -> dict:
    decider = CachingDecider(
        OpenRouterDecider(model=model),
        path=CACHE_DIR / f"{model.replace('/', '_')}.json",
        namespace=model,
    )
    engine = Engine(catalog, standards, decider, sync_date="2026-07-12")

    n = schema_fail = std_ok = grounded = wet_fiberglass = wet_total = abstained = 0
    start = time.perf_counter()
    for element in _ceilings():
        n += 1
        try:
            d = engine.decide_element(element)
        except Exception:  # invalid/failed structured output
            schema_fail += 1
            continue
        if d.action == "abstain":
            abstained += 1
            continue
        if d.matched_standard == _expected_standard(element.element_id):
            std_ok += 1
        product = catalog.get(d.chosen_product.product_id)
        if product is not None:
            grounded += 1
        if element.element_id in WET:
            wet_total += 1
            if product and product.material and product.material.value == "fiberglass":
                wet_fiberglass += 1
    elapsed = time.perf_counter() - start

    return {
        "model": model,
        "n": n,
        "schema_fail": schema_fail,
        "standard_acc": std_ok / n,
        "grounded": grounded / max(1, n - abstained - schema_fail),
        "wet_fiberglass": f"{wet_fiberglass}/{wet_total}",
        "abstained": abstained,
        "seconds": round(elapsed, 1),
    }


def main() -> None:
    catalog = Catalog.load(DATA / "products.json")
    standards = load_standards(DATA / "firm-library.json")
    rows = [run_model(m, catalog, standards) for m in MODELS]

    print(f"{'model':32} {'schema_ok':10} {'std_acc':8} {'grounded':9} {'wet->fbg':8} {'abst':5} {'secs':5}")
    for r in rows:
        schema_ok = f"{r['n'] - r['schema_fail']}/{r['n']}"
        print(f"{r['model']:32} {schema_ok:10} {r['standard_acc']:<8.0%} "
              f"{r['grounded']:<9.0%} {r['wet_fiberglass']:8} {r['abstained']:<5} {r['seconds']:<5}")


if __name__ == "__main__":
    main()
