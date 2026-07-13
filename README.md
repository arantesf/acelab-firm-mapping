# Acelab Firm-Library Mapping

An agent that inherits an architecture firm's material **standards** into a Revit model —
deciding, per element, which standard applies, which approved product satisfies it, and how
confident it is. It **abstains rather than guesses** when a standard doesn't cover an element
or the data is too ambiguous.

The **decision engine** runs against a serialized model (`sample-model.json`) with **no Revit
installed** and emits a **decision plan**: per element, the chosen product and the exact type
parameters — including the `Acelab_*` **shared parameters** — that should be written.

This repo is the **decision engine** (the graded, Revit-free deliverable). The thin **C# add-in**
that applies the decisions to a live model — a human-in-the-loop review UI that binds shared
parameters, writes type parameters, and duplicates a type per product — is a **separate repo**:
<https://github.com/arantesf/acelab-revit-challenge>.

- **Screen recording (in Revit):** <https://www.loom.com/share/46c95588acbe48d79d23c7b6bbea572e>
- **Revit add-in (separate repo):** <https://github.com/arantesf/acelab-revit-challenge>
- **Design:** [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)
- **Write-up** (design decisions + path to production): [`SOLUTION.md`](SOLUTION.md)
- **Result artifact from a real run:** [`artifacts/mapping-result.json`](artifacts/mapping-result.json)

## Scope

Covers all three catalog categories and all four firm standards:

- **Ceilings** — open-plan acoustic (NRC + Class A) and high-humidity rooms.
- **Exterior walls** — rainscreen cladding (NFPA 285).
- **Floors** — resilient flooring (wear layer + slip/DCOF).

Each category has its own messy-attribute normalizers and hard filter; the pipeline,
grounding, confidence, and abstention are shared. Elements whose category the firm library
does not cover (doors, windows, furniture, interior partitions) **abstain** — flagged for
review, never guessed. See [ARCHITECTURE](docs/ARCHITECTURE.md).

## Run the engine

```bash
cd engine
py -3 -m venv .venv
./.venv/Scripts/python -m pip install -e .

# deterministic baseline — no LLM, no key, fully reproducible
./.venv/Scripts/python -m acelab_mapping map --decider fake \
   --out ../artifacts/mapping-result.json

# the real agent (needs OPENROUTER_API_KEY; model via ACELAB_MODEL, default openai/gpt-4.1-mini)
OPENROUTER_API_KEY=... ./.venv/Scripts/python -m acelab_mapping map \
   --decider openrouter --out ../artifacts/mapping-result.json
```

Both write a result artifact (see `artifacts/`) reporting, per element, what was decided,
why, its confidence, and what would be written — or why it abstained.

## Test

```bash
cd engine && ./.venv/Scripts/python -m pytest -q     # 104 tests, no network
```

The suite runs entirely without an LLM: the pure core (normalization for all three
categories, hard-filter, confidence, guardrails) plus a golden expectation over all 38
sample elements.

## Layout

```
engine/                     Python decision engine (the graded deliverable)
  src/acelab_mapping/        normalize · catalog · firm · qualify · decide · confidence
                             decider/ (port + fake + openrouter + caching) · artifact · cli
  tests/                     104 tests, no network
  scripts/bakeoff.py         model comparison
docs/                       ARCHITECTURE.md  (the Revit add-in is a separate repo)
data/                       canonical inputs: catalog, firm library, sample model, shared params
artifacts/mapping-result.json  result artifact from a real openrouter run (committed)
```

Canonical inputs live in `data/` (product catalog, firm library, sample model,
shared-parameter file), so the repo runs on a fresh clone. Other run output is reproducible,
not source — the engine's LLM cache and bake-off outputs (`engine/artifacts/`) are git-ignored.
