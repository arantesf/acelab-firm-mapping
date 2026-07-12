# Solution

Inheriting a firm's material **standards** onto Revit model elements — deciding, per element,
which standard applies, which approved product satisfies it, and how confident to be, with
honest abstention.

- **Screen recording (in Revit):** _<link — Loom / unlisted YouTube>_
- **Result artifact from a real run:** [`artifacts/mapping-result.json`](artifacts/mapping-result.json)
  (`gpt-4.1-mini` via OpenRouter; reproduce with the §2 command)
- **Model:** `openai/gpt-4.1-mini` via OpenRouter (config `ACELAB_MODEL`; chosen by a bake-off — see below)
- **Run & test:** see [`README.md`](README.md); the engine runs against `sample-model.json` with no Revit, and the full test suite (104 tests) needs no network.

---

## 1. What it decides

For each element the engine produces one of three outcomes, and records **why**:

- **map** — a firm standard applies and a catalog product satisfies it → the product's identity
  and parameters are written (as a dry-run plan the Revit adapter applies).
- **abstain** — the choice is too uncertain, nothing qualifies, or the firm library doesn't cover
  the element's category (doors, windows, interior partitions) → flagged for human review rather
  than guessed.
- **skip** — a firm standard carries a requirement the engine can't yet enforce, so its elements
  are set aside honestly rather than mapped on a partial filter (`qualify.is_enforceable`). This
  does not fire on the sample, where every standard is enforceable.

Covers all three catalog categories: acoustic ceilings (NRC + Class A / humidity), exterior
cladding (NFPA 285), resilient flooring (wear layer + slip). On the 38-element sample: 29 map,
9 abstain (uncovered categories), 0 skip, 0 hallucinated products. Those specs drive
*qualification*; the write itself is scoped
to the four `Acelab_*` shared params + the built-in `Fire Rating`. Specs without a Revit
built-in (NRC, DCOF, NFPA 285, …) are documented, not written — see REVIT-ADAPTER-HANDOFF §5.1.

## 2. How to run

```bash
cd engine && py -3 -m venv .venv && ./.venv/Scripts/python -m pip install -e .

# graded path — deterministic, no LLM, no key
./.venv/Scripts/python -m acelab_mapping map --decider fake --out ../artifacts/mapping-result.json

# the real agent
OPENROUTER_API_KEY=... ./.venv/Scripts/python -m acelab_mapping map --decider openrouter \
   --out ../artifacts/mapping-result.json
```

---

## 3. How I designed the agent (the core of this submission)

**The bet: treat the LLM as an unreliable component and engineer around it.** Every number and
every value written to Revit is produced by deterministic code; the model is used only for the
judgment that is genuinely linguistic — reading room semantics and the firm's *lessons-learned*
prose — and it is fenced in on every side.

### Grounding — how I keep it honest

The invariant, enforced by a test: **no decision references a product outside the catalog, and
every written value is re-derived from the normalized product — never from the model's text.**

- **Code parses, not the LLM.** The catalog is deliberately messy — NRC as `.90` / `0.80 (NRC)`
  / `0.8`; fire rating in six forms; wear layer in mil *and* mm; slip as `DCOF 0.42` or the
  unverifiable ramp rating `R10`. Total, unit-tested parsers turn these into typed values,
  encoding domain equivalence (ASTM E84 **Class A ≡ Class 1 ≡ Class I**). Asking the LLM to
  parse these would be untestable and wrong under pressure ("is 0.78 ≥ 0.80?").
- **A hard filter is the gate.** Given a standard's hard requirements, code computes the set of
  products that *provably* satisfy them. The decider only ever sees this set, so it cannot
  invent a product or pick a disqualified one.
- **Written values are re-derived** from the chosen catalog product. The model's text becomes
  the human-readable rationale — never a parameter value.

### The agent: constrained tools + structured output

The engine assembles a grounded `DecisionContext` and hands it to a `Decider` behind a port.
The LLM implementation:

- receives the element and, per candidate standard, the **hard-filtered** products (capped to
  the firm's approved items + a few alternatives — a filter can leave 150+ qualifying, and
  sending them all only adds noise and cost);
- returns a **strict JSON-schema** decision (`temperature=0`): standard, product (validated
  against the candidate set upstream), confidence, rationale, and which lessons it honored or
  violated — or an abstention;
- is told, in the system prompt, to choose only from the candidates, **prefer approved
  products, use `lessons_learned` to reject a technically-qualifying product the firm warns
  against, and abstain when unsure.**

This is where the LLM earns its seat. Example from a real run: for wet-room ceilings it chose a
**fiberglass** tile over a qualifying mineral-fiber one, quoting the lesson *"mineral fiber sags
in humid rooms; prefer fiberglass"* — a judgment no rule in the data expresses. For cladding it
chose fiber cement, citing *"fiber cement has been our reliable choice"* + NFPA 285.

### Confidence & abstention

The model's self-confidence is **one input, not the answer.** A composite tempers it with
signals it can't fake — how well the room matched, whether the product is firm-approved, and
whether it violates a lesson — and every component is reported in the artifact:

```
score = 0.40·room + 0.25·approved + 0.35·llm_confidence   (×0.6 if a lesson is violated)
band  = write (≥0.75) · review (≥0.55) · abstain (<0.55)
```

Abstention is **two-sided**: the model may abstain, and code may *override to abstain* even when
the model was confident — an ungrounded pick, an unknown standard, or a below-threshold score
all collapse to an honest abstention. (Unit-tested with synthetic inputs; the sample ceilings
sit in unambiguous rooms.)

### Respecting explicit choices

A type already named after a catalog product (`Acme - Northwind Quietude 300` → `cl-1004`) is an
explicit human decision: the engine **confirms and stamps it** without calling the LLM and
without re-speccing it to a different approved tile — while the generic ceilings around it are
mapped by the model. It flags for review if the named product no longer meets a standard.

### Choosing the model by measurement

A bake-off (`scripts/bakeoff.py`) ran `gpt-4o-mini`, `gpt-4.1-mini`, and `gemini-2.5-flash` over
the ceilings. On the sample model's clean room names (`Open Office`, `Pool Deck`, `Corridor`) all
three score **100%** on schema validity, standard accuracy, grounding, and honoring the fiberglass
lesson — grounding makes the *product* choice a near-commodity. The **room/applicability judgment
is not**, though: on messy real-world names (`Men` / `Women` as restrooms, `Conference` / `Admin`
as office-adjacent-but-not-open-plan) `gpt-4o-mini` mis-routes and over-scores, while
`gpt-4.1-mini` routes correctly and calibrates `room_fit`. So `gpt-4.1-mini` is the default, behind
a one-line config swap (`ACELAB_MODEL`); a full run costs about **$0.01**, and identical elements
share one cached call.

---

## 4. The Revit adapter (thin, by design)

The decision engine is separate from Revit and graded on `sample-model.json`; a C# add-in is a
**mechanical executor** that re-decides nothing. It consumes the artifact and, in a Transaction:

- **binds the `Acelab_*` shared parameters** from the `.txt` to Ceilings as **type** parameters
  (product identity is a type-level property), and writes the product parameters;
- **duplicates a type where one generic type must carry two products** — in the sample,
  `Generic - Lay-in` spans Open Office (`cl-1004`) and Pool Deck (a humidity tile); one Revit
  type cannot hold both, so the add-in duplicates it and reassigns instances (the deliberate
  type-vs-instance call);
- supports **dry-run** (report intended changes, open no Transaction).

### Type vs. instance — the rationale

I put the product on the **type**, deliberately — and it's also the simplest, most common choice
in practice. In Revit a *type* is what a specifiable product maps onto (one type ≈ one catalog
SKU), and a type edit propagates to every instance, which is exactly what "this ceiling *is* this
product" wants. It also fits the pattern the four `Acelab_*` parameters imply: they are a **thin
link** (ID, URL, manufacturer, sync date), not a spec dump — the lightweight identity lives on the
element while the rich data stays in the Acelab hub (the modern, lean-model approach). Instance
parameters are for what genuinely varies per placement (Mark, offsets, phasing); using them for
product identity only adds per-copy bookkeeping and loses propagation.

Where this stops being 1:1 is a single element that carries **two products** — a wall with
cladding on the exterior face and paint on the interior. A type (or element) parameter holds one
value, so you don't stamp two product IDs on the wall. The idiomatic resolution is to put each
product identity on the **material of its layer**: a Revit `Material` has its own identity fields
(manufacturer, model, URL), and a wall's compound structure already references one material per
layer — so the exterior cladding and the interior finish each carry their own Acelab id, composed
by the assembly. The alternatives are to model the rainscreen as a **separate element** (restoring
one element ↔ one product) or to code the interior side as a **room-finish** resolved in a schedule.

The governing rule: product identity sits on the **type** when the element is 1:1 with a product,
and drops to the **material** (per layer/face) when one element composes several. The three
categories here — ceiling tiles, cladding panels, resilient flooring — are all clean 1:1
type-level products (Acelab evidently chose them that way), so type is correct and sufficient;
the multi-material case stays a design consideration, not something the slice builds.

Build guide and JSON contract: [`docs/REVIT-ADAPTER-HANDOFF.md`](docs/REVIT-ADAPTER-HANDOFF.md).

---

## 5. How I'd evolve this into a production feature

*This is where the interesting problems are.*

### Where the LLM is unreliable — and the guardrails

| Failure mode | Guardrail (built or planned) |
|---|---|
| Parsing messy values wrong | Done in code, unit-tested — the LLM never parses |
| Hallucinating a product / value | Chooses only from hard-filtered candidates; written values re-derived; validated at the boundary |
| Overconfidence / poor calibration | Composite confidence + two-sided abstention. **Not yet calibrated statistically** (~20 elements) — in production I'd collect architect-reviewed decisions and fit a reliability curve, reporting *precision@confidence* and tuning the thresholds |
| **Relevance ≠ compliance** | The real Acelab search returns *plausible* products with a similarity score, **not verified specs**. Trusting search ranking would reintroduce guessing — retrieval must always be followed by the hard filter |

### Where Revit constrains you

- **Room for a ceiling isn't a property** — ceilings aren't room-bounded, so the adapter must
  derive it spatially (`GetRoomAtPoint` below the ceiling, with a phase). Null rooms become
  ambiguous cases the engine reviews.
- **A product selection is a type**, so mapping a shared generic type to divergent products
  forces type duplication (above) — a real modeling decision, not a metadata write.
- **The write side is transactional, single-threaded, version-locked.** Re-runs must be
  idempotent (don't re-duplicate types). Interop tooling (e.g. Everse) can ease *extraction*,
  but the write path is still the Revit API's.

### Where mapping-by-standard breaks down

- The library is **intent, not a rule table** — most elements are uncovered on purpose, and
  standards overlap (the open-office standard itself carries a humidity caveat "near rooftop
  units" that no element flags). Honest abstention is the correct default, not a fallback.
- **Region / availability isn't in the standard.** Products carry a `region`, but the firm
  library never states the project's region, so specifying an out-of-region product is a real
  error the current data can't prevent. In production the project region is a first-class input.
- **Near-duplicates and catalog drift** — the catalog has near-identical products (Acelab even
  exposes a `/deduplication/` endpoint I'd reuse), and products change; `Acelab_Last_Synced`
  records staleness so a re-inherit can be scheduled.

### Scale

The engine loads a 366-product JSON and filters in memory; at 50k+ this shifts, without touching
the decision logic, because the catalog sits behind a boundary:

- push the hard filter into an **indexed store or the Acelab vector-search API** (retrieve
  top-K semantically, *then* hard-filter on specs — never trust the ranking as the answer);
- **normalize at ingest**, not per run;
- the LLM cost stays flat — it only ever sees a bounded shortlist.

> **Note on the Acelab search API.** Its documentation was unreachable during the assessment
> (`https://acelab.mintlify.app/search-api` returned **404**), so every reference to Acelab's
> vector-search / `/deduplication/` endpoints above is inferred from the local example data and
> each product's `masterformat` / attributes — not from the real API contract. I built and
> graded entirely against the provided local files.

### Interactive resolution (the natural next step)

Today an ambiguous element abstains with *what* was ambiguous. That structured note is exactly a
clarifying question: instead of queuing it for review, the agent could **ask** ("this ceiling
has no room — open office, wet room, or other?") with the candidate standards as options — the
Claude-Code plan-mode pattern. The decision logic stays in the engine; only a UI (a Revit panel
or a web chat + 3D viewer) is added. Deliberately **out of scope** for this batch deliverable,
where abstention is the right behavior.

---

## 6. What I deliberately did not build

To keep a focused, working slice: no hosted service (the engine is a library + CLI; production
would put it behind a service with the key server-side), no region filter or price normalization
(they don't fire on the graded input — better discussed here than shipped as dead code), and no
interactive UI. The scarce signal is a grounded, honest, testable decision engine — that is what
I optimized for.
