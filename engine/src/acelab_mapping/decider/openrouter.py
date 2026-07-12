"""LLM decider over OpenRouter (OpenAI-compatible).

The model is used only where judgment is genuinely needed: which standard fits the
element's room, and which *already-qualified* product best honors the firm's
lessons-learned prose. It is fenced in on every side:

- it is shown only hard-filtered candidates and may pick nothing else (the id it
  returns is validated against that set upstream in `decide.py`);
- `temperature=0` and a strict JSON schema make the output deterministic and typed;
- values written to Revit are re-derived from the catalog, never taken from its text.

Design notes captured for this decider (from review):
- the candidate list is every product that passes the hard filter (approved first), so the
  decider weighs all valid options; the hard filter is the only silent cut, and it drops only
  the provably-disqualified. For very large catalogs a cost cap can bound the list, but the
  right tool there is recall-preserving retrieval, not a blind numeric cut;
- wrap this in `CachingDecider` so identical elements cost one call and responses can
  be replayed in tests without spend.
"""

from __future__ import annotations

import json
import os
from typing import Optional

from ..models import DecisionContext, RawDecision
from .base import SequentialBatchDecider

_SYSTEM = """You inherit an architecture firm's material library onto one Revit model element.

You are given the element and, per candidate firm standard, a GROUNDED list of catalog
products that already pass that standard's hard requirements.

Rules:
- Choose a product ONLY from the provided candidate lists. Never invent a product or id.
- Prefer the firm's approved products (is_preferred=true). Use each standard's
  lessons_learned to break ties and to REJECT a technically-qualifying product the firm's
  guidance warns against.
- A standard only governs the kind of element it describes. Match the element's CATEGORY to the
  standard's intent first (a ceiling standard is for ceilings, not walls, floors or doors), and
  respect its context (an exterior-cladding standard applies only to exterior walls). If no
  standard describes this element — or its context is too ambiguous to decide responsibly —
  ABSTAIN; do not force a fit.
- Among the standards that fit the category, choose the one whose INTENDED ROOMS best include this
  element's room. Rooms that share a purpose route together even when named differently — a men's
  or women's room is a restroom (a wet room), so it takes a high-humidity ceiling standard, not the
  open-plan office one, even though both are ceilings. Only settle for a weaker-fitting standard if
  none fits better, and then report the honest, lower room_fit.
- Judge room_fit strictly, and consistently with your own rationale:
  - "match" ONLY when the room truly IS the kind of space the standard targets (an open-plan
    office; a restroom / locker / pool; a corridor / lobby).
  - "weak" when the room is merely adjacent or plausible — a conference, meeting, admin, reception
    or private office is NOT an open-plan office, so it is "weak" for that standard, not "match".
  - "none" when the room contradicts the standard (a restroom is "none" for the open-plan office
    standard). If you had to settle for a standard whose room does not really fit, say "none".
  Reason from the standard's intent/context/tags; there is no precomputed hint.
- Give an honest confidence in [0,1]; list the lessons you honored and any you had to violate.
- After the primary product, propose up to TWO other products from the SAME chosen standard's
  candidate list as ranked alternatives (`alternatives`), best-first, each with a one-line reason a
  human would use to pick it over the primary (e.g. higher NRC, firm-approved, better wear layer).
  Use only product_ids from that candidate list; never repeat the primary; never invent ids. If no
  sensible runner-up exists, return an empty list.
Return only the structured decision."""

_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "abstain": {"type": "boolean"},
        "standard_id": {"type": ["string", "null"]},
        "product_id": {"type": ["string", "null"]},
        "confidence": {"type": "number"},
        "room_fit": {"type": "string", "enum": ["match", "weak", "none"]},
        "rationale": {"type": "string"},
        "honors_lessons": {"type": "array", "items": {"type": "string"}},
        "violates_lessons": {"type": "array", "items": {"type": "string"}},
        "abstain_reason": {"type": ["string", "null"]},
        "alternatives": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "product_id": {"type": "string"},
                    "reason": {"type": "string"},
                },
                "required": ["product_id", "reason"],
            },
        },
    },
    "required": [
        "abstain", "standard_id", "product_id", "confidence", "room_fit",
        "rationale", "honors_lessons", "violates_lessons", "abstain_reason", "alternatives",
    ],
}

DEFAULT_MODEL = "openai/gpt-4.1-mini"


class OpenRouterDecider(SequentialBatchDecider):
    def __init__(
        self,
        model: Optional[str] = None,
        api_key: Optional[str] = None,
        temperature: float = 0.0,
        max_retries: int = 6,
        timeout: float = 60.0,
    ) -> None:
        from openai import OpenAI

        self.model = model or os.environ.get("ACELAB_MODEL", DEFAULT_MODEL)
        self.temperature = temperature
        key = api_key or os.environ.get("OPENROUTER_API_KEY")
        if not key:
            raise SystemExit("OPENROUTER_API_KEY is not set")
        # Batching fans out many calls at once, so transient 429s / dropped connections are
        # expected under load. Let the SDK absorb them with exponential backoff + jitter that
        # honors the server's Retry-After header — the default of 2 retries is too few once the
        # transport dispatches a dozen calls concurrently, so raise it and give each call a
        # bounded timeout instead of hanging.
        self._client = OpenAI(
            base_url="https://openrouter.ai/api/v1",
            api_key=key,
            max_retries=max_retries,
            timeout=timeout,
        )

    def decide(self, context: DecisionContext) -> RawDecision:
        response = self._client.chat.completions.create(
            model=self.model,
            temperature=self.temperature,
            messages=[
                {"role": "system", "content": _SYSTEM},
                {"role": "user", "content": self._render(context)},
            ],
            response_format={
                "type": "json_schema",
                "json_schema": {"name": "firm_mapping_decision", "strict": True, "schema": _SCHEMA},
            },
        )
        return RawDecision.model_validate_json(response.choices[0].message.content)

    def _render(self, context: DecisionContext) -> str:
        element = context.element
        payload = {
            "element": {
                "category": element.category,
                "family": element.family,
                "type": element.type,
                "level": element.level,
                "room": element.room,
                "exterior": element.exterior,
            },
            "candidate_standards": [
                {
                    "standard_id": c.id,
                    "intent": c.intent,
                    "context": c.context,
                    "requirements": c.requirements_summary,
                    "lessons_learned": c.lessons_learned,
                    "tags": c.tags,
                    "products": [
                        {
                            "product_id": p.product_id,
                            "name": p.name,
                            "manufacturer": p.manufacturer,
                            "nrc": p.nrc,
                            "fire_class": p.fire_class.value if p.fire_class else None,
                            "humidity": p.humidity.value,
                            "nfpa_285": p.nfpa_285,
                            "wear_mil": p.wear_mil,
                            "dcof": p.dcof,
                            "is_preferred": p.is_preferred,
                            "attributes": p.attributes,
                        }
                        for p in c.qualified
                    ],
                }
                for c in context.candidates
            ],
        }
        return json.dumps(payload, ensure_ascii=False, indent=2)
