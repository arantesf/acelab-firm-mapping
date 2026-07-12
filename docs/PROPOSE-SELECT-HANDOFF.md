# Propose → Select → Apply — Handoff

Everything a fresh session needs to add a **human-in-the-loop selection** step to the Revit
add-in: instead of writing the engine's single pick straight into the model, **show a table
with the top-3 candidate products per element, change nothing, and only apply after the user
picks**. This is the natural home for the "suggest vs auto-apply" gating we discussed — a tool
that mutates an architect's model should propose when it isn't certain.

---

## 1. The change in one picture

```
TODAY:   collect → engine decides (1 product) → apply all mapped in a Transaction → report
DESIRED: collect → engine proposes (top-3 per element) → REVIEW UI (no writes) →
         user selects → apply only the selected picks in a Transaction → report
```

The engine and the grounding stay the same. What's new is (a) the engine returning **ranked
alternatives**, (b) a **selection UI**, and (c) splitting "apply" into *propose* and
*apply-selected*.

---

## 2. Current state (so you don't re-derive it)

- **Engine** (`acelab-firm-mapping/engine/`): agent-driven, nothing hardcoded. It grounds every
  enforceable standard, and the LLM (`gpt-4.1-mini`, `decider=openrouter`) judges which standard
  applies (category + exterior + room), picks **one** grounded product, and returns a `room_fit`
  (`match`/`weak`/`none`). Confidence = `0.40·room_fit + 0.25·approved + 0.35·llm` (×0.6 on a
  lesson violation); band = write ≥0.75 / review ≥0.55 / abstain. See `docs/ARCHITECTURE.md`.
- **Contract**: the artifact's per-element `chosen_product` is a **single** product today, plus a
  grounded candidate set the decider saw internally (not emitted). `revit_write.parameters` are the
  values for that one product.
- **Add-in** (`acelab-revit-challenge/AcelabRevitAddin/Mapping/`):
  - `MapFirmLibraryCommand` — ribbon entry; asks Dry-run vs Apply, runs, opens the HTML report.
  - `EngineClient` — subprocess to the engine CLI; fails loudly if the engine can't run.
  - `Planner` (Revit-free) — groups mapped elements by their Revit type; one product per type →
    reuse, several products on one type → **duplicate per product**. Already keyed on
    `(type, product)`, so it handles *per-element* product choices correctly.
  - `MappingApplier` — binds `Acelab_*` type params, executes the plan (duplicate + `ChangeTypeId`
    + write), or reports it in dry-run. All in one `Transaction`.
  - `DecisionReport` + `data/report-template.html` — renders the artifact into the Material-Hub-
    styled HTML report (light, sortable, per-row rationale + confidence breakdown + Applied column).
- **Key insight for this feature**: because `Planner` already groups by `(type, product)`, letting
  the user change *which product* an element maps to needs **no new apply logic** — a different
  selection just changes the per-element product before planning, and duplication falls out.

---

## 3. What to build

### 3.1 Engine — emit ranked alternatives (top-3)

The decider already sees a grounded shortlist (`_shortlist` in `decide.py`: approved first, then a
few alternatives). Two ways to expose the top-3; pick one:

- **(a) LLM ranks** — add `alternatives: [{product_id, reason}]` (2–3, best-first) to the decider's
  JSON schema (`decider/openrouter.py`) and to `RawDecision` (`models.py`); the primary stays
  `product_id`. Richest (each option gets a reason), slightly bigger output. The `FakeDecider` fills
  it deterministically (approved-first order).
- **(b) Engine ranks** — keep one LLM pick, and in `decide.py` attach the grounded shortlist
  (already ordered preferred-first) as the alternatives. No schema change; reasons are generic.

Recommended: **(a)**. Then emit on each mapped `Decision` an `alternatives` list, each item with
`product_id, name, manufacturer, url, is_preferred`, the key spec attributes (NRC, fire, humidity,
wear, dcof — already normalized), a one-line `reason`, and the **re-derived** `revit_write.parameters`
for THAT product (so the add-in can apply whichever the user picks without re-deriving). Cap at 3.

Update `artifact.py` / the artifact schema in `REVIT-ADAPTER-HANDOFF.md §3` accordingly. Keep
`chosen_product` = alternatives[0] for backward compatibility.

### 3.2 Add-in — split apply into *propose* and *apply-selected*

- `MapFirmLibraryCommand`: replace the Dry-run/Apply prompt with **Propose** (default). Propose runs
  collect → engine → `DecisionReport` in a new **review mode**, opens the UI, and **opens no
  Transaction**.
- Add an **apply-selected** path that takes a selection (element_id → chosen product_id), rebuilds
  the `Planner.MapItem` list using the selected product per element, and runs `MappingApplier` in
  one Transaction. This can reuse `MappingApplier` almost verbatim — feed it the selected products.
- Default the selection to the engine's `alternatives[0]`; pre-select write-band rows, and consider
  leaving review-band rows unselected so the user must choose (this is the gating).

### 3.3 The UI + the transport (the crux)

The report is HTML opened in a browser — **outside** Revit — so a selection made there has to get
back into the add-in to apply. Options, with the trade-off:

| Option | How | Pros | Cons |
|---|---|---|---|
| **A. Selection file** | Report has "Export selections" → saves `selections.json`; a second Revit command "Apply selections" reads it | Minimal; keeps the HTML report | Two manual steps; browser download path is uncontrolled |
| **B. WPF DataGrid in Revit** | Build the review table as a modeless WPF window (DataGrid + per-row ComboBox of the 3 options); "Apply" writes directly | Best UX; no transport problem; select→apply in one process/Transaction | More C# UI work; re-implements the table Revit-side |
| **C. Local HTTP handshake** | Add-in starts a `localhost` listener; the HTML POSTs selections back | Keeps the rich HTML UI, closes the loop | Local server + Revit's single-threaded API (marshal via `ExternalEvent`); more moving parts |
| **D. WebView2 panel** | Host the HTML report in a WebView2 control in a Revit dockable panel; JS↔C# bridge returns selections | Rich HTML *and* in-process | Adds WebView2 dependency + bridge plumbing |

**Recommendation:** **B (WPF DataGrid)** for the real interactive flow — it removes the transport
problem entirely and keeps "select → apply in one Transaction" clean; keep the HTML report as the
read-only audit artifact. If you want an MVP fast, **A** ships in an afternoon. Avoid C/D unless a
web UI inside Revit is a hard requirement.

### 3.4 Report template (if you keep HTML in the loop, Option A/C/D)

`data/report-template.html` already renders rows with an expandable detail. Add, per row, a control
(radio group or `<select>`) listing the 3 `alternatives`, defaulted to #1, and an "Export
selections" / "Apply" button that serializes `{element_id: product_id}`. The table, filters, and
confidence breakdown stay as-is.

---

## 4. Decisions to make before coding

1. **Transport** (§3.3) — WPF (B) vs selection-file (A). Everything else follows from this.
2. **Selection granularity** — per **element** or per **type**? Per-element is most flexible and the
   `Planner` already supports it (mixed products on a type → duplicate). Per-type is simpler UX but
   can't express "these two office ceilings, different product." Recommend per-element with a
   "apply to all of this type" convenience.
3. **What is pre-selected** — default all to engine #1, or leave `review`-band rows blank so the
   human must choose (the gating). Recommend the latter: write-band pre-checked, review-band blank.
4. **Top-N** — 3 is the ask; confirm the engine can always surface 3 grounded options (some
   standards have fewer qualifying approved products — fall back to filling from non-approved
   qualified, and label them clearly).

---

## 5. Pointers

- Engine decision + artifact: `engine/src/acelab_mapping/decide.py`, `models.py`, `artifact.py`,
  `decider/openrouter.py` (schema + prompt).
- Add-in flow: `AcelabRevitAddin/Mapping/` — `MapFirmLibraryCommand.cs`, `MappingApplier.cs`,
  `Planner.cs`, `DecisionReport.cs`, `data/report-template.html`.
- Contract reference: `docs/REVIT-ADAPTER-HANDOFF.md §3`. Design: `docs/ARCHITECTURE.md`.
- Run the engine to see the current artifact shape:
  `PYTHONPATH=src ./.venv/Scripts/python.exe -m acelab_mapping map --out ../artifacts/mapping-result.openrouter.json --decider openrouter` (needs `OPENROUTER_API_KEY`).
- Tests: `./.venv/Scripts/python.exe -m pytest -q` (104, no network).
