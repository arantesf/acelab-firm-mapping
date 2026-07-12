"""Typed data model for the mapping engine.

Three layers, deliberately separated:
- input models (`Element`, `RawProduct`, `Standard`) mirror the source JSON as-is;
- `NormalizedProduct` is the canonical, deterministic view the engine reasons over;
- decision/result models are emitted as the machine-readable artifact.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


class FireClass(str, Enum):
    """ASTM E84 surface-burning class. Class A == Class I (flame spread 0-25)."""

    A = "A"
    B = "B"
    C = "C"


class HumidityLevel(str, Enum):
    UNKNOWN = "unknown"
    STANDARD = "standard"
    MODERATE = "moderate"
    HIGH = "high"
    YES = "yes"  # generic manufacturer assertion of humidity resistance

    def is_resistant(self) -> bool:
        """Whether this level satisfies a `humidity_resistance: true` requirement.

        HIGH and a generic YES pass; STANDARD/MODERATE/UNKNOWN do not — a wet-room
        standard should not accept a tile the catalog only rates as "standard".
        """
        return self in (HumidityLevel.HIGH, HumidityLevel.YES)


class Material(str, Enum):
    MINERAL_FIBER = "mineral_fiber"
    FIBERGLASS = "fiberglass"
    METAL = "metal"
    WOOD = "wood"
    OTHER = "other"


# --- input models -----------------------------------------------------------

class Element(BaseModel):
    """One placed element, mirroring a FilteredElementCollector row."""

    element_id: int
    category: str
    family: Optional[str] = None
    type: Optional[str] = None
    level: Optional[str] = None
    room: Optional[str] = None
    exterior: bool = False


class RawProduct(BaseModel):
    """A catalog product exactly as ingested — attributes are heterogeneous."""

    product_id: str
    category: str
    name: str
    manufacturer: Optional[str] = None
    acelab_url: Optional[str] = None
    masterformat: Optional[dict[str, Any]] = None
    attributes: dict[str, Any] = Field(default_factory=dict)


class Requirements(BaseModel):
    category: str
    # acoustic ceilings
    min_nrc: Optional[float] = None
    fire_rating: Optional[str] = None
    humidity_resistance: Optional[bool] = None
    # exterior cladding
    nfpa_285: Optional[bool] = None
    # resilient flooring
    min_wear_mil: Optional[float] = None
    slip: Optional[str] = None
    # any further key is treated as an unenforceable requirement (see qualify.is_enforceable)
    model_config = {"extra": "allow"}


class Standard(BaseModel):
    intent: str
    context: Optional[str] = None
    requirements: Requirements
    preferred_products: list[str] = Field(default_factory=list)
    lessons_learned: Optional[str] = None
    tags: list[str] = Field(default_factory=list)


# --- canonical model --------------------------------------------------------

class NormalizedProduct(BaseModel):
    """Deterministic, typed view of a `RawProduct`.

    Every field is parsed by code (never by the LLM). `raw_attributes` is retained
    so the artifact can show provenance: canonical value <- original string.
    """

    product_id: str
    category: str
    name: str
    manufacturer: Optional[str] = None
    url: Optional[str] = None

    nrc: Optional[float] = None
    fire_class: Optional[FireClass] = None
    humidity: HumidityLevel = HumidityLevel.UNKNOWN
    material: Optional[Material] = None
    # exterior cladding / resilient flooring
    nfpa_285: Optional[bool] = None
    wear_mil: Optional[float] = None
    dcof: Optional[float] = None

    raw_attributes: dict[str, Any] = Field(default_factory=dict)


# --- decision context (what the decider sees) -------------------------------

class CandidateProduct(BaseModel):
    """A grounded, hard-filter-passing product offered to the decider."""

    product_id: str
    name: str
    manufacturer: Optional[str] = None
    nrc: Optional[float] = None
    fire_class: Optional[FireClass] = None
    humidity: HumidityLevel = HumidityLevel.UNKNOWN
    material: Optional[Material] = None
    nfpa_285: Optional[bool] = None
    wear_mil: Optional[float] = None
    dcof: Optional[float] = None
    is_preferred: bool = False
    # a curated slice of raw attributes the lessons-learned may reference
    # (e.g. cost_sf, emissions_cert, wind_load) — for the decider's soft judgment.
    attributes: dict[str, Any] = Field(default_factory=dict)


class CandidateStandard(BaseModel):
    id: str  # the standard's intent, used as a stable handle
    intent: str
    context: Optional[str] = None
    requirements_summary: str
    lessons_learned: Optional[str] = None
    tags: list[str] = Field(default_factory=list)
    qualified: list[CandidateProduct]


class DecisionContext(BaseModel):
    element: Element
    candidates: list[CandidateStandard]


class RawAlternative(BaseModel):
    """A runner-up product the decider ranked below the primary — still one of the grounded
    candidates it was shown (validated upstream), with a one-line reason. Best-first."""

    product_id: str
    reason: str = ""


class RawDecision(BaseModel):
    """The decider's output — schema-validated, never trusted for written values."""

    abstain: bool
    standard_id: Optional[str] = None
    product_id: Optional[str] = None
    confidence: float = 0.0
    # How well the element's room/context fits the chosen standard's intent, judged by the
    # decider itself (never from keyword lists): "match" | "weak" | "none".
    room_fit: str = "none"
    rationale: str = ""
    honors_lessons: list[str] = Field(default_factory=list)
    violates_lessons: list[str] = Field(default_factory=list)
    abstain_reason: Optional[str] = None
    # Up to a couple of runner-up products, best-first. Defaulted so pre-existing decider caches
    # (recorded before this field existed) still deserialize; the engine pads to three from the
    # grounded shortlist when the decider offers fewer.
    alternatives: list[RawAlternative] = Field(default_factory=list)


# --- result artifact --------------------------------------------------------

class Confidence(BaseModel):
    score: float
    band: str  # "write" | "review" | "abstain"
    components: dict[str, float]


class RevitWrite(BaseModel):
    target_level: str = "type"
    parameters: dict[str, str]


class ChosenProduct(BaseModel):
    product_id: str
    name: str
    manufacturer: Optional[str] = None
    url: Optional[str] = None


class Alternative(BaseModel):
    """One of the ranked options offered for a mapped element. Each is a fully grounded pick:
    its own re-derived `revit_write` and composite `confidence`, so a human (or the adapter)
    can apply whichever option is chosen without the engine re-deciding. `alternatives[0]` is
    the engine's own primary and equals the decision's `chosen_product`/`confidence`."""

    product_id: str
    name: str
    manufacturer: Optional[str] = None
    url: Optional[str] = None
    is_preferred: bool = False
    reason: Optional[str] = None
    # normalized specs, for display alongside the option
    nrc: Optional[float] = None
    fire_class: Optional[FireClass] = None
    humidity: HumidityLevel = HumidityLevel.UNKNOWN
    nfpa_285: Optional[bool] = None
    wear_mil: Optional[float] = None
    dcof: Optional[float] = None
    confidence: Confidence
    revit_write: RevitWrite


class Decision(BaseModel):
    element_id: int
    category: str
    type: Optional[str] = None
    room: Optional[str] = None
    action: str  # "map" | "abstain" | "skip"
    matched_standard: Optional[str] = None
    how: Optional[str] = None
    chosen_product: Optional[ChosenProduct] = None
    # Ranked options for this element, best-first; alternatives[0] mirrors chosen_product.
    alternatives: list[Alternative] = Field(default_factory=list)
    why: Optional[str] = None
    confidence: Optional[Confidence] = None
    honors_lessons: list[str] = Field(default_factory=list)
    violates_lessons: list[str] = Field(default_factory=list)
    candidates_considered: int = 0
    needs_review: bool = False
    already_specified: bool = False
    revit_write: Optional[RevitWrite] = None
    note: Optional[str] = None


class RunSummary(BaseModel):
    elements_total: int
    mapped: int
    abstained: int
    skipped: int
    needs_review: int
    products_considered: int


class RunResult(BaseModel):
    run: dict[str, Any]
    summary: RunSummary
    decisions: list[Decision]
