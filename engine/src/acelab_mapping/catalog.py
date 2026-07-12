"""Load and index the product catalog as normalized products."""

from __future__ import annotations

import json
import re
from pathlib import Path

from .models import NormalizedProduct, RawProduct
from .normalize import normalize_product


def _norm_name(name: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", " ", name.lower())).strip()


class Catalog:
    def __init__(self, products: list[NormalizedProduct]) -> None:
        self._by_id = {p.product_id: p for p in products}

    @classmethod
    def load(cls, path: Path) -> "Catalog":
        doc = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls([normalize_product(RawProduct(**p)) for p in doc["products"]])

    def __len__(self) -> int:
        return len(self._by_id)

    def get(self, product_id: str) -> NormalizedProduct | None:
        return self._by_id.get(product_id)

    def all(self) -> list[NormalizedProduct]:
        return list(self._by_id.values())

    def in_category(self, category: str) -> list[NormalizedProduct]:
        return [p for p in self._by_id.values() if p.category == category]

    def find_by_name(self, type_name: str | None, category: str | None = None) -> NormalizedProduct | None:
        """Match an element's type name to a catalog product it already names.

        Handles a firm prefix (e.g. "Acme - Northwind Quietude 300" -> "Northwind
        Quietude 300"). Scoped to a category when given, to avoid a floor type
        coincidentally matching a ceiling product name.
        """
        if not type_name:
            return None
        target = _norm_name(type_name)
        if not target:
            return None
        for product in self._by_id.values():
            if category is not None and product.category != category:
                continue
            name = _norm_name(product.name)
            if name and (target == name or target.endswith(" " + name)):
                return product
        return None
