"""Per-element orchestration: assemble grounded context, run the decider, then
validate, ground, and score its output.

The decider's freedom is bounded on both ends: it only ever sees hard-filtered
candidates, and every field it returns is checked before it becomes a decision.
An ungrounded product, an unknown standard, or a low composite score all collapse
to an honest abstention rather than a guess.
"""

from __future__ import annotations

from .catalog import Catalog
from .confidence import composite
from .decider.base import Decider
from .firm import map_category
from .models import (
    Alternative,
    CandidateProduct,
    CandidateStandard,
    ChosenProduct,
    Confidence,
    Decision,
    DecisionContext,
    Element,
    RevitWrite,
    Standard,
)
from .qualify import is_enforceable, qualified_products, qualifies


def _requirements_summary(standard: Standard) -> str:
    req = standard.requirements
    parts = [f"category={req.category}"]
    if req.min_nrc is not None:
        parts.append(f"min_nrc>={req.min_nrc}")
    if req.fire_rating:
        parts.append(f"fire={req.fire_rating}")
    if req.humidity_resistance:
        parts.append("humidity_resistance=required")
    return "; ".join(parts)


class Engine:
    def __init__(
        self,
        catalog: Catalog,
        standards: list[Standard],
        decider: Decider,
        sync_date: str,
        max_alternatives: int | None = None,
    ) -> None:
        self.catalog = catalog
        self.standards = standards
        self.decider = decider
        self.sync_date = sync_date
        # The only silent cut is the hard filter, which drops just the *provably* disqualified.
        # Every product that survives it is a valid choice, so by default all of them are offered
        # to the decider (approved first) — the choice among valid options is qualitative, driven
        # by the firm's lessons-learned, and that judgment is exactly the decider's job. A cut by
        # any deterministic proxy (id, a numeric margin) can't see that qualitative signal, so it
        # would risk discarding the very product the decider would pick. `max_alternatives` is an
        # optional cost valve for very large catalogs; there, recall-preserving retrieval (e.g.
        # vector search over each product's description) is the right tool, not a blind cap.
        self.max_alternatives = max_alternatives

    _LESSON_ATTRS = ("material", "cost_sf", "emissions_cert", "wind_load", "moisture_limit", "recycled_content")

    def _shortlist(self, qualified, preferred: set[str]):
        approved = [p for p in qualified if p.product_id in preferred]
        alternatives = [p for p in qualified if p.product_id not in preferred]
        if self.max_alternatives is not None:
            alternatives = alternatives[: self.max_alternatives]
        return approved + alternatives

    def _lesson_attrs(self, product) -> dict:
        return {k: product.raw_attributes[k] for k in self._LESSON_ATTRS if k in product.raw_attributes}

    def _candidates(
        self, element: Element, standards: list[Standard]
    ) -> tuple[list[CandidateStandard], int]:
        candidates: list[CandidateStandard] = []
        qualified_total = 0
        for standard in standards:
            preferred = set(standard.preferred_products)
            qualified = qualified_products(standard, self.catalog.all())
            qualified_total += len(qualified)
            candidates.append(
                CandidateStandard(
                    id=standard.intent,
                    intent=standard.intent,
                    context=standard.context,
                    requirements_summary=_requirements_summary(standard),
                    lessons_learned=standard.lessons_learned,
                    tags=standard.tags,
                    qualified=[
                        CandidateProduct(
                            product_id=p.product_id,
                            name=p.name,
                            manufacturer=p.manufacturer,
                            nrc=p.nrc,
                            fire_class=p.fire_class,
                            humidity=p.humidity,
                            material=p.material,
                            nfpa_285=p.nfpa_285,
                            wear_mil=round(p.wear_mil, 1) if p.wear_mil is not None else None,
                            dcof=p.dcof,
                            is_preferred=p.product_id in preferred,
                            attributes=self._lesson_attrs(p),
                        )
                        for p in self._shortlist(qualified, preferred)
                    ],
                )
            )
        return candidates, qualified_total

    def decide_all(self, elements: list[Element]) -> list[Decision]:
        """Map a whole model. Plan every element first (pure, no I/O), then resolve only the
        contexts that actually need the model through the decider's *batch* seam — the same call
        shape a hosted service would expose as ``POST /decisions`` with a list. Dedup, concurrency
        and (later) an HTTP fan-out live behind that seam, so nothing in the engine changes when
        the decider stops being in-process. Results are aligned back to the input element order."""
        plans = [self._plan(e) for e in elements]
        contexts = [context for _, context in plans if context is not None]
        answered = iter(self.decider.decide_batch(contexts))
        return [
            base if context is None else self._finish(base, context, next(answered))
            for base, context in plans
        ]

    def decide_stream(self, elements: list[Element], on_decision, max_workers: int = 8) -> None:
        """Decide a whole model, calling ``on_decision(Decision)`` as each element resolves — in
        completion order, so a live UI can populate its rows one at a time instead of waiting for the
        whole batch. Distinct decisions are deduped (identical elements share one decider call) and
        run concurrently behind a thread pool; elements decidable without a model call (skips,
        already-specified) resolve immediately. ``on_decision`` may be called from a worker thread."""
        from concurrent.futures import ThreadPoolExecutor, as_completed

        plans = [self._plan(e) for e in elements]
        pending: list[tuple[Decision, DecisionContext]] = []
        for base, context in plans:
            if context is None:
                on_decision(base)
            else:
                pending.append((base, context))
        if not pending:
            return

        # Dedup the model calls the way decide_batch does: one decider call per distinct context,
        # fanned back out to every element that shares it.
        key = getattr(self.decider, "_key", None)
        groups: dict = {}
        order: list = []
        for base, context in pending:
            k = key(context) if key else id(context)
            if k not in groups:
                groups[k] = (context, [])
                order.append(k)
            groups[k][1].append(base)

        with ThreadPoolExecutor(max_workers=min(max_workers, len(groups))) as pool:
            futures = {pool.submit(self.decider.decide, groups[k][0]): k for k in order}
            for future in as_completed(futures):
                context, bases = groups[futures[future]]
                raw = future.result()
                for base in bases:
                    on_decision(self._finish(base, context, raw))

    def decide_element(self, element: Element) -> Decision:
        """Single-element path: plan, and if a judgment is needed, one decider call then finish."""
        base, context = self._plan(element)
        if context is None:
            return base
        return self._finish(base, context, self.decider.decide(context))

    def _plan(self, element: Element) -> tuple[Decision, DecisionContext | None]:
        """Everything decidable without the model: coverage, an already-specified type, or the
        grounded candidate set. Returns ``(decision, None)`` when no model call is needed, or
        ``(base, context)`` when the decider must judge. Kept pure so a whole model can be planned
        up front and only the distinct contexts crossed over the (batch) transport."""
        base = Decision(
            element_id=element.element_id,
            category=element.category,
            type=element.type,
            room=element.room,
            action="skip",
        )

        enforceable = [s for s in self.standards if is_enforceable(s)]
        if not enforceable:
            base.note = "no enforceable firm standard is available"
            return base, None

        # The one deterministic, data-safe signal: the Revit-category -> firm-category bridge.
        # It narrows the decider's search to same-category standards — a ceiling is judged only
        # against ceiling standards and their ceiling products, never against the whole catalog
        # across every category. This decides *scope*, not *choice*: which standard actually
        # governs the element (its context and room) is still judged by the decider. It is the
        # only hardcoded signal, and it keeps the prompt from carrying every material there is.
        firm_category = map_category(element)
        in_scope = [s for s in enforceable if s.requirements.category == firm_category]
        if not in_scope:
            base.action = "abstain"
            base.note = "element category is outside the firm library's coverage"
            return base, None

        # A type already named after a catalog product is an explicit human choice —
        # confirm and stamp it rather than re-deciding it to a different product.
        existing = self.catalog.find_by_name(element.type)
        if existing is not None:
            return self._confirm_existing(base, existing, in_scope), None

        candidates, qualified_total = self._candidates(element, in_scope)
        base.candidates_considered = qualified_total
        return base, DecisionContext(element=element, candidates=candidates)

    def _finish(self, base: Decision, context: DecisionContext, raw) -> Decision:
        """Validate, ground and score one decider response against its own grounded context."""
        element, candidates = context.element, context.candidates

        if raw.abstain:
            base.action = "abstain"
            base.note = raw.abstain_reason or "decider abstained"
            base.why = raw.rationale or None
            return base

        chosen = next((c for c in candidates if c.id == raw.standard_id), None)
        if chosen is None:
            base.action = "abstain"
            base.note = "decider named a standard outside the candidate set"
            return base
        product_choice = next((p for p in chosen.qualified if p.product_id == raw.product_id), None)
        if product_choice is None:
            base.action = "abstain"
            base.note = "decider chose a product outside the grounded candidate set"
            return base

        conf = composite(
            raw.confidence, raw.room_fit, product_choice.is_preferred, bool(raw.violates_lessons)
        )

        base.matched_standard = chosen.intent
        base.how = f"decider matched '{chosen.intent}' for {element.category}; room fit '{raw.room_fit}'"
        base.why = raw.rationale or None
        base.confidence = conf
        base.honors_lessons = raw.honors_lessons
        base.violates_lessons = raw.violates_lessons

        if conf.band == "abstain":
            base.action = "abstain"
            base.note = f"composite confidence {conf.score} below write/review threshold"
            return base

        product = self.catalog.get(product_choice.product_id)
        base.action = "map"
        base.needs_review = conf.band == "review"
        base.chosen_product = ChosenProduct(
            product_id=product.product_id,
            name=product.name,
            manufacturer=product.manufacturer,
            url=product.url,
        )
        base.revit_write = RevitWrite(
            target_level="type",
            parameters=self._write_parameters(product),
        )
        base.alternatives = self._alternatives(chosen, product_choice, conf, raw)
        return base

    _MAX_OPTIONS = 60  # a safety cap so a huge catalog can't produce an unusable dropdown

    def _alternatives(
        self, chosen: CandidateStandard, primary: CandidateProduct, primary_conf: Confidence, raw
    ) -> list[Alternative]:
        """Every product that qualifies for the matched standard, offered as a selectable option so
        a human can pick any grounded material — not just the engine's top pick. Order: the engine's
        own primary first (it equals `chosen_product` and stays pre-selected), then the rest of the
        qualified set (firm-approved first). Each option carries its own re-derived write and its own
        composite confidence, so applying any of them needs no re-decision."""
        standard = next((s for s in self.standards if s.intent == chosen.intent), None)
        preferred = set(standard.preferred_products) if standard else set()
        qualified = qualified_products(standard, self.catalog.all()) if standard else []

        reasons: dict[str, str] = {primary.product_id: raw.rationale or ""}
        for alt in raw.alternatives:  # keep the decider's short reasons where it gave one
            reasons.setdefault(alt.product_id, alt.reason or "")

        # primary first (pre-selected), then the rest of the qualified set (already approved-first)
        order = [primary.product_id]
        for product in qualified:
            if product.product_id not in order:
                order.append(product.product_id)

        options: list[Alternative] = []
        for pid in order[: self._MAX_OPTIONS]:
            product = self.catalog.get(pid)
            if product is None:
                continue
            is_pref = pid in preferred
            conf = (
                primary_conf
                if pid == primary.product_id
                else composite(raw.confidence, raw.room_fit, is_pref, has_violations=False)
            )
            options.append(
                Alternative(
                    product_id=product.product_id,
                    name=product.name,
                    manufacturer=product.manufacturer,
                    url=product.url,
                    is_preferred=is_pref,
                    reason=reasons.get(pid) or None,
                    nrc=product.nrc,
                    fire_class=product.fire_class,
                    humidity=product.humidity,
                    nfpa_285=product.nfpa_285,
                    wear_mil=product.wear_mil,
                    dcof=product.dcof,
                    confidence=conf,
                    revit_write=RevitWrite(target_level="type", parameters=self._write_parameters(product)),
                )
            )
        return options

    def _confirm_existing(self, base: Decision, product, enforceable: list[Standard]) -> Decision:
        standard = next((s for s in enforceable if qualifies(product, s)), None)
        base.action = "map"
        base.already_specified = True
        base.chosen_product = ChosenProduct(
            product_id=product.product_id,
            name=product.name,
            manufacturer=product.manufacturer,
            url=product.url,
        )
        base.matched_standard = standard.intent if standard else None
        base.how = f"type already names catalog product {product.product_id}; confirmed without re-deciding"
        base.confidence = Confidence(score=0.99, band="write", components={"already_specified": 1.0})
        base.revit_write = RevitWrite(target_level="type", parameters=self._write_parameters(product))
        # A confirmed pick has no runners-up — the type already names it. Keep a single-entry
        # alternatives list so every mapped decision has the same shape.
        base.alternatives = [
            Alternative(
                product_id=product.product_id,
                name=product.name,
                manufacturer=product.manufacturer,
                url=product.url,
                is_preferred=True,
                reason="already specified on the type",
                nrc=product.nrc,
                fire_class=product.fire_class,
                humidity=product.humidity,
                nfpa_285=product.nfpa_285,
                wear_mil=product.wear_mil,
                dcof=product.dcof,
                confidence=base.confidence,
                revit_write=base.revit_write,
            )
        ]
        if standard is None:
            base.needs_review = True
            base.note = "already specified on the type, but it meets no applicable firm standard — flagged"
        else:
            base.note = "already specified on the type; confirmed"
        return base

    def _write_parameters(self, product) -> dict[str, str]:
        """Parameters the adapter writes onto the Revit type. Values are re-derived from
        the normalized product, never from the LLM.

        Two destinations, deliberately scoped:
          - The four ``Acelab_*`` shared parameters (defined in Acelab-SharedParameters.txt),
            which the adapter binds and populates.
          - ``Fire Rating`` — a genuine Revit *built-in* type parameter on Ceilings / Floors /
            Walls, so it is written directly with no custom parameter needed.

        Product performance/spec attributes that have no Revit built-in — acoustic NRC/CAC,
        light reflectance, NFPA 285, wear layer, slip/DCOF, humidity, emissions, etc. — would
        each need a dedicated *custom* shared parameter to store. That is intentionally out of
        scope: those attributes are documented (see docs/REVIT-ADAPTER-HANDOFF.md §5) rather
        than written, keeping the write to identity + the one native rating field.
        """
        params = {
            "Acelab_Product_ID": product.product_id,
            "Acelab_Manufacturer": product.manufacturer or "",
            "Acelab_Product_URL": product.url or "",
            "Acelab_Last_Synced": self.sync_date,
        }
        # Fire Rating is a Revit built-in type parameter (Ceilings/Floors/Walls) — safe to set.
        if product.fire_class is not None:
            params["Fire Rating"] = f"Class {product.fire_class.value}"
        return params
