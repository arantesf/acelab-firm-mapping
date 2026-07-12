# Revit Adapter — Handoff

Everything a fresh session needs to build the **C# Revit add-in** that applies the
engine's decisions to a live model. The Python decision engine is already built and
tested; the add-in is a **thin, mechanical executor** — it does not decide anything.

---

## 1. Where the add-in sits

```
[Revit + C# add-in]                [Python engine]                 [C# add-in]
 1. collect ceiling elements  ──►   2. decide (grounded + LLM)  ──►  3. apply writes
    → element snapshot JSON          → result artifact JSON          in a Transaction
                                                                      + dry-run
```

- The **engine owns 100% of the decisions** (which standard, which product, confidence,
  what parameters to write). It runs offline against `sample-model.json` (the graded path)
  and, for the video, against a snapshot exported from the live model.
- The **add-in owns 100% of the Revit I/O**: collect elements, bind shared parameters,
  resolve/duplicate types, write parameters, dry-run, re-emit the artifact. It re-decides
  nothing.
- The two sides speak **one JSON contract** (Section 3). Same shape offline or live.

The engine lives at `acelab-firm-mapping/engine/`. Read `docs/ARCHITECTURE.md` for the
full design once it's written; this file is enough to start the add-in.

---

## 2. Locked decisions that constrain the add-in

- **Stack:** C# Revit add-in (`IExternalCommand`). Revit version **TBD — confirm which you
  have** (target 2024; 2023–2025 fine). Version decides the target framework (see §7).
- **Parameters go on the TYPE, not the instance.** A product *is* a type-level property.
- **Type collision is the add-in's job** (this was decided explicitly). The engine decides
  per element; the add-in detects when one Revit type must carry two products and
  **duplicates the type**.
- **Dry-run is required.** It reports what *would* change without opening a Transaction.
- **The engine never invents values.** Everything in `revit_write.parameters` is already
  grounded (re-derived from the catalog). The add-in writes those strings verbatim.
- **LLM key stays server-side in production** (the engine, not the add-in, calls the LLM).
  For dev you can run the engine locally; see §8.

---

## 3. The contract

### 3.1 Input — element snapshot (add-in → engine)

Exactly the shape of `data/sample-model.json`. The add-in produces this
from a `FilteredElementCollector`:

```json
{
  "model_name": "rac_advanced_sample_project",
  "elements": [
    { "element_id": 312121, "category": "Ceilings", "family": "Compound Ceiling",
      "type": "Generic - Lay-in", "level": "Level 2", "room": "Open Office" },
    { "element_id": 312306, "category": "Walls", "family": "Basic Wall",
      "type": "Generic - Exterior 300mm", "level": "Level 1", "exterior": true }
  ]
}
```

Fields per element: `element_id` (int, the Revit `ElementId` value — the round-trip key),
`category` (Revit category name), `family`, `type` (the **type name**), `level`, `room`
(nullable — see §6.4), `exterior` (bool, default false). For the ceilings slice you only
need to collect `OST_Ceilings`, but collecting all covered categories is harmless (elements
in a category the firm library doesn't cover come back as `abstain`, never `map`).

### 3.2 Output — result artifact (engine → add-in)

Produced by the engine; the add-in reads it and applies. Real examples from a run:

**A mapped element (this is an instruction):**
```json
{
  "element_id": 312121,
  "category": "Ceilings",
  "type": "Generic - Lay-in",
  "room": "Open Office",
  "action": "map",
  "matched_standard": "Acoustic ceilings in open-plan office areas",
  "how": "decider matched 'Acoustic ceilings in open-plan office areas' for Ceilings; room fit 'match'",
  "chosen_product": {
    "product_id": "cl-1004", "name": "Northwind Quietude 300",
    "manufacturer": "Northwind Ceilings",
    "url": "https://material-hub.acelabusa.com/products/cl-1004"
  },
  "why": "...rationale...",
  "confidence": { "score": 0.965, "band": "write",
                  "components": { "room": 1.0, "approved": 1.0, "llm": 0.9, "lessons_penalty": 1.0 } },
  "needs_review": false,
  "revit_write": {
    "target_level": "type",
    "parameters": {
      "Acelab_Product_ID": "cl-1004",
      "Acelab_Manufacturer": "Northwind Ceilings",
      "Acelab_Product_URL": "https://material-hub.acelabusa.com/products/cl-1004",
      "Acelab_Last_Synced": "2026-07-12",
      "Fire Rating": "Class A"
    }
  },
  "alternatives": [
    { "product_id": "cl-1004", "name": "Northwind Quietude 300", "manufacturer": "Northwind Ceilings",
      "url": "https://material-hub.acelabusa.com/products/cl-1004", "is_preferred": true,
      "reason": "Room fit 'match'; firm-approved acoustic tile.",
      "confidence": { "score": 0.965, "band": "write", "components": { "room": 1.0, "approved": 1.0, "llm": 0.9, "lessons_penalty": 1.0 } },
      "revit_write": { "target_level": "type", "parameters": { "Acelab_Product_ID": "cl-1004", "...": "..." } } },
    { "product_id": "cl-1022", "name": "Northwind Fissured 600", "is_preferred": true,
      "reason": "Firm-approved alternative, same fire class.",
      "confidence": { "score": 0.965, "band": "write", "components": {} },
      "revit_write": { "target_level": "type", "parameters": { "...": "..." } } }
  ]
}
```

`alternatives` is **every product that qualifies for the matched standard**, best-first (capped at
60). `alternatives[0]` mirrors `chosen_product` (same product, same `confidence`) and is the
pre-selected recommendation; the rest are the other qualifying products (firm-approved first) so the
user can override to any grounded material. **Each option is independently applicable** — its own
composite `confidence` and re-derived `revit_write` — so the add-in writes whichever the user
selects, with no engine round-trip. A confirmed already-specified type carries a single-entry list.

**An abstained element (do nothing) — a category the firm library doesn't cover:**
```json
{ "element_id": 312749, "category": "Doors", "type": "0915 x 2134mm",
  "action": "abstain", "note": "element category is outside the firm library's coverage" }
```

`action` is one of `map` | `abstain` | `skip`. Elements in an uncovered category (doors,
windows, interior partitions) come back as `abstain`; `skip` is reserved for the rarer case
where a firm standard carries a requirement the engine can't yet enforce, so it never fires
on the current sample. **Only `map` has a `revit_write`.** For `abstain`/`skip` the add-in
does nothing (optionally surfaces `note` in its report).
`band` is `write` (apply) | `review` (apply but flag `needs_review`) | `abstain` (won't
appear as `map`). Fields that are null are omitted (the engine serializes with
`exclude_none`), so guard for missing keys.

### 3.3 The collision, made concrete

In the real artifact, **both** of these are `action: "map"` with `type: "Generic - Lay-in"**
but different products:

| element | room | type | product |
|---|---|---|---|
| 312121 | Open Office | Generic - Lay-in | **cl-1004** |
| 312297 | Pool Deck | Generic - Lay-in | **cl-1038** |

One Revit `ElementType` ("Generic - Lay-in") cannot hold two products. The add-in must
duplicate it (§6.3). This is the core Revit-depth demonstration.

---

## 4. Add-in responsibilities (the checklist)

1. **Collect** ceiling elements → build the snapshot JSON (§3.1). Derive `room` (§6.4).
2. **Get decisions** from the engine (§8) → deserialize the artifact (§3.2).
3. **Bind** the four `Acelab_*` shared parameters to the Ceilings category at **type** level (§6.1).
4. **Apply** each `map` decision inside one `Transaction` (§6.2–6.3):
   - resolve the element's type; detect collisions; duplicate types as needed;
   - reassign instances to their product-specific type;
   - write `revit_write.parameters` on the type.
5. **Dry-run mode:** do steps 1–4's *analysis* but open no Transaction; produce a report of
   intended changes (types to duplicate, params to set).
6. **Re-emit** the artifact with `mode: "applied"`, and per element the actual
   `type_action` (`reuse` | `duplicate("<new name>")`) and params written.

---

## 5. Type vs instance policy

- The four `Acelab_*` params + the built-in `Fire Rating` type parameter go on the
  **type**. Rationale: selecting a product is a type-level act; instances of the same
  product share it, and schedules/tags read the type.
- When a generic type is shared across elements resolving to **different** products,
  duplicate the type per product and reassign instances (§6.3). This is the "be deliberate
  about type vs instance" answer.
- Do **not** write these on instances. (An instance-level `Acelab_*` is only justified if a
  single type legitimately hosts mixed products, which the duplication strategy avoids.)

### 5.1 Product specs the engine does **not** write (documentation-only)

Every product carries performance/spec attributes with no Revit built-in to hold them —
they would each need a dedicated **custom** shared parameter. That is deliberately out of
scope: the engine surfaces them here for reference but does not write them, so the write
stays limited to identity (`Acelab_*`) + the native `Fire Rating`.

| Category | Spec attributes (no built-in → not written) |
|---|---|
| Acoustic Ceilings | `nrc`, `cac`, `light_reflectance`, `edge_detail`, `grid_compatibility`, `humidity_resistance`, `tile_size`, `emissions_cert`, `recycled_content` |
| Resilient Flooring | `wear_layer`, `slip_resistance` (DCOF), `moisture_limit`, `finish`, `pattern`, `emissions_cert`, `recycled_content` |
| Exterior Cladding | `nfpa_285`, `astm_e84`, `r_value`, `wind_load`, `water_penetration`, `panel_size`, `weight`, `material` |

To persist any of these later, add a custom shared parameter (same binding flow as §6.1)
and extend `_write_parameters` in `decide.py`. `r_value` is the one near-built-in case —
it maps to the Revit analytical *Thermal Resistance (R)* parameter when analytical
properties are enabled.

---

## 6. Revit API specifics

### 6.1 Bind the shared parameters (type-level)

`data/Acelab-SharedParameters.txt` defines 4 TEXT params with fixed GUIDs
(`Acelab_Product_ID`, `_URL`, `_Manufacturer`, `_Last_Synced`).

```csharp
// point Revit at the shared param file, then bind each definition to Ceilings as a Type param
app.SharedParametersFilename = sharedParamTxtPath;
DefinitionFile file = app.OpenSharedParameterFile();
DefinitionGroup group = file.Groups.get_Item("Acelab");

var cats = app.Create.NewCategorySet();
cats.Insert(doc.Settings.Categories.get_Item(BuiltInCategory.OST_Ceilings));
TypeBinding binding = app.Create.NewTypeBinding(cats);   // Type, not Instance

foreach (ExternalDefinition def in group.Definitions)
    doc.ParameterBindings.Insert(def, binding, BuiltInParameterGroup.PG_IDENTITY_DATA);
// use ReInsert if already bound. Do this inside a Transaction.
```

Note: `Fire Rating` is **not** in the shared file — it is a Revit **built-in** type
parameter (present on Ceilings / Floors / Walls), so the add-in writes it directly via
`type.get_Parameter(BuiltInParameter.FIRE_RATING)` (or `LookupParameter("Fire Rating")`),
with no shared-parameter binding needed. Product specs without a built-in (NRC, DCOF,
NFPA 285, …) are documentation-only (§5.1) and are intentionally not written.

### 6.2 Write a parameter on a type

```csharp
ElementType type = ...;
// shared params are addressable by GUID (robust) or by name
Parameter p = type.get_Parameter(new Guid("a1f0e7c2-...-0a1b2c3d4e01")); // Acelab_Product_ID
p.Set(value);            // all four are TEXT → Set(string)
// name-based fallback:
type.LookupParameter("Acelab_Manufacturer")?.Set(manufacturer);
```

### 6.3 Collision detection + type duplication

```
group mapped decisions by the element's ElementType (element.GetTypeId())
for each type group:
    distinctProducts = set(decision.chosen_product.product_id)
    if count(distinctProducts) == 1 and typeIsSafeToWriteInPlace:
        write params on the existing type            // type_action = reuse
    else:
        for each product in distinctProducts:
            newType = existingType.Duplicate($"{typeName} — {productName}")   // ElementType.Duplicate
            write params on newType
            foreach element mapped to this product:
                (element as Ceiling).ChangeTypeId(newType.Id)  // reassign instance
            // type_action = duplicate("<new name>")
```

`ElementType.Duplicate(string)` returns a new type; `Element.ChangeTypeId(ElementId)`
reassigns an instance. "Safe to write in place" = the type is already product-specific (e.g.
its name already equals the product, like `Acme - Northwind Quietude 300` == cl-1004) or all
its instances map to the same product. When in doubt, duplicate.

### 6.4 Deriving a ceiling's room (the wrinkle worth points)

Ceilings are **not** room-bounded, so `room` isn't a direct property. Get it spatially:

```csharp
BoundingBoxXYZ bb = ceiling.get_BoundingBox(null);
XYZ center = (bb.Min + bb.Max) * 0.5;
XYZ probe = new XYZ(center.X, center.Y, center.Z - 0.3);  // dip just below the ceiling plane
Phase phase = doc.GetElement(ceiling.CreatedPhaseId) as Phase;
Room room = doc.GetRoomAtPoint(probe, phase);             // may be null
string roomName = room?.get_Parameter(BuiltInParameter.ROOM_NAME)?.AsString();
```

Fallbacks: iterate `Room`s and test `room.IsPointInRoom(probe)`, or pick the room whose
level matches and whose solid contains the point. Null room → send `room: null`; the engine
will treat it as an ambiguous case (low room signal → review/abstain). This is a good
"where Revit constrains you" note for the write-up.

### 6.5 Transaction + dry-run

Wrap all writes in a single `Transaction`. For dry-run, run the same analysis (collision
grouping, target type names, param sets) but **do not** start the Transaction — collect the
intended operations into the report instead. Re-emit the artifact with `mode: "applied"` (or
`"dry-run"`) and, per element, `type_action` + the params actually/would-be written.

---

## 7. C# project setup

- **Revit 2024** → `.NET Framework 4.8`. **Revit 2025/2026** → `.NET 8`. Confirm your version
  first; it sets `<TargetFramework>`.
- Reference `RevitAPI.dll` and `RevitAPIUI.dll` from `C:\Program Files\Autodesk\Revit <ver>\`.
  Set them **Copy Local = false** and `<Private>false</Private>`.
- Add a `.addin` manifest so Revit loads the command:

```xml
<?xml version="1.0" encoding="utf-8"?>
<RevitAddIns>
  <AddIn Type="Command">
    <Name>Acelab: Map Firm Library</Name>
    <Assembly>Acelab.Revit.AddIn.dll</Assembly>
    <AddInId>PUT-A-FRESH-GUID-HERE</AddInId>
    <FullClassName>Acelab.Revit.AddIn.MapFirmLibraryCommand</FullClassName>
    <VendorId>ACELAB</VendorId>
  </AddIn>
</RevitAddIns>
```

Drop the `.addin` + built DLL in `%AppData%\Autodesk\Revit\Addins\<ver>\`.

Suggested layout:
```
adapter-revit/Acelab.Revit.AddIn/
  MapFirmLibraryCommand.cs      # IExternalCommand entry point + dry-run toggle (TaskDialog)
  ElementSnapshotExporter.cs    # FilteredElementCollector -> snapshot JSON (+ room derivation)
  EngineClient.cs               # run engine, get artifact (see §8)
  ArtifactModels.cs             # DTOs mirroring §3.2 (System.Text.Json)
  SharedParameterBinder.cs      # §6.1
  MappingApplier.cs             # §6.3 collision + duplicate + write, Transaction, dry-run
  Acelab.Revit.AddIn.csproj
  Acelab.Revit.AddIn.addin
```

---

## 8. Getting decisions from the engine (transport)

For the demo you have Python + the engine on your dev machine, so the simplest path is a
**subprocess to the engine CLI**:

```csharp
// write snapshot to temp, run the engine, read the artifact back
File.WriteAllText(snapshotPath, snapshotJson);
var psi = new ProcessStartInfo {
    FileName = pythonExe,            // engine/.venv/Scripts/python.exe
    Arguments = $"-m acelab_mapping map --model \"{snapshotPath}\" " +
                $"--out \"{artifactPath}\" --decider fake",
    WorkingDirectory = engineDir,    // acelab-firm-mapping/engine
    EnvironmentVariables = { ["PYTHONPATH"] = "src" },
    UseShellExecute = false, RedirectStandardOutput = true
};
Process.Start(psi).WaitForExit();
var artifact = JsonSerializer.Deserialize<RunResult>(File.ReadAllText(artifactPath));
```

Note `--decider fake`: **you can build and verify the entire Revit write path against the
deterministic FakeDecider — no LLM, no budget, fully repeatable.** Switch to
`--decider openrouter` (needs `OPENROUTER_API_KEY` in the env) once that decider is wired on
the engine side. The CLI arguments and artifact shape stay identical.

**Production shape (write-up, not required to build):** the engine runs as a hosted HTTP
service (FastAPI on Cloud Run, mirroring Acelab's own vector-search service); the add-in
becomes a thin HTTP client (`POST /map`) pointing at a configurable base URL, and the LLM
key never leaves the server. Same JSON contract — only the transport changes.

---

## 9. Open items to confirm before coding

1. **Which Revit version** you have installed (sets the target framework).
2. `Fire Rating` is written via its Revit built-in (§6.1); product specs without a built-in
   are documentation-only (§5.1). No extra shared params to add unless a spec must persist.
3. The `.rvt` for the video: `rac_advanced_sample_project.rvt` (ships with Revit; has
   ceilings + rooms). Your run won't match `sample-model.json` — that's expected and fine.

---

## 10. Pointers

- Engine code: `acelab-firm-mapping/engine/src/acelab_mapping/` (read `decide.py`,
  `models.py` for the exact artifact fields).
- Data (canonical inputs): `data/` (`products.json`, `firm-library.json`,
  `sample-model.json`, `Acelab-SharedParameters.txt`).
- Run the engine to see a real artifact:
  ```bash
  cd acelab-firm-mapping/engine
  PYTHONPATH=src ./.venv/Scripts/python.exe -m acelab_mapping map \
     --out ../artifacts/mapping-result.fake.json --date 2026-07-12
  ```
- Tests (all green): `./.venv/Scripts/python.exe -m pytest -q`
- The command above writes the artifact to develop against at
  `acelab-firm-mapping/artifacts/mapping-result.fake.json` (git-ignored reproducible output).
```
