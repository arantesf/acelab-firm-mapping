"""Command-line entry point: map a serialized model and emit the result artifact.

This is the graded, Revit-free path — it runs against sample-model.json exactly as
the Revit adapter would run against a live model, because both speak the same
element shape.

    python -m acelab_mapping map --model sample-model.json --out result.json
"""

from __future__ import annotations

import argparse
import datetime
import json
import sys
import time
from pathlib import Path

from .artifact import build_result
from .catalog import Catalog
from .decide import Engine
from .decider import FakeDecider
from .firm import load_standards
from .models import Element

_DEFAULT_DATA = Path(__file__).resolve().parents[3] / "data"


def _build_decider(name: str, cache_path: Path, max_workers: int):
    if name == "fake":
        return FakeDecider(), "fake-decider (deterministic baseline)"
    if name == "openrouter":
        from .decider.caching import CachingDecider
        from .decider.openrouter import OpenRouterDecider

        inner = OpenRouterDecider()
        decider = CachingDecider(inner, path=cache_path, namespace=inner.model, max_workers=max_workers)
        return decider, inner.model
    raise SystemExit(f"unknown decider: {name}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="acelab-mapping")
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("map", help="map a model and emit the result artifact")
    run.add_argument("--model", type=Path, default=_DEFAULT_DATA / "sample-model.json")
    run.add_argument("--data-dir", type=Path, default=_DEFAULT_DATA)
    run.add_argument("--out", type=Path, default=Path("artifacts/mapping-result.json"))
    run.add_argument("--decider", default="fake", choices=["fake", "openrouter"])
    run.add_argument("--cache", type=Path, default=Path("artifacts/decider-cache.json"))
    run.add_argument("--date", default=datetime.date.today().isoformat())
    run.add_argument(
        "--workers", type=int, default=8,
        help="concurrency for the distinct decider calls in a batch (the transport's fan-out)",
    )
    run.add_argument(
        "--stream", action="store_true",
        help="emit one decision per line to stdout ('@@DECISION@@ <json>') as it resolves, so a "
             "live UI can populate rows one at a time; the full artifact is still written to --out",
    )

    args = parser.parse_args(argv)

    catalog = Catalog.load(args.data_dir / "products.json")
    standards = load_standards(args.data_dir / "firm-library.json")
    firm_doc = json.loads((args.data_dir / "firm-library.json").read_text(encoding="utf-8"))
    model = json.loads(args.model.read_text(encoding="utf-8"))

    decider, decider_label = _build_decider(args.decider, args.cache, args.workers)
    engine = Engine(catalog, standards, decider, sync_date=args.date)

    # One batch call for the whole model — the same shape a hosted service would answer over HTTP.
    # Dedup and concurrency for the distinct decisions live behind the decider seam, not here.
    elements = [Element(**e) for e in model["elements"]]
    started = time.perf_counter()
    if args.stream:
        # Emit each decision as it resolves so the Revit UI can fill rows one at a time; still
        # collect them all for the final artifact below.
        import threading

        decisions = []
        lock = threading.Lock()

        def _emit(decision):
            line = "@@DECISION@@ " + decision.model_dump_json(exclude_none=True)
            with lock:
                decisions.append(decision)
                sys.stdout.write(line + "\n")
                sys.stdout.flush()

        engine.decide_stream(elements, _emit, max_workers=args.workers)
    else:
        decisions = engine.decide_all(elements)
    elapsed = time.perf_counter() - started
    print(
        f"decided {len(decisions)} elements in {elapsed:.1f}s "
        f"(workers={args.workers}, decider={decider_label})",
        file=sys.stderr,
    )

    result = build_result(
        decisions,
        run_meta={
            "model": model.get("model_name"),
            "firm_library_version": firm_doc.get("library_version"),
            "mode": "dry-run",
            "engine_decider": decider_label,
            "generated_at": args.date,
        },
        products_considered=len(catalog),
    )

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(result.model_dump_json(indent=2, exclude_none=True), encoding="utf-8")

    s = result.summary
    print(f"model: {result.run['model']}  decider: {decider_label}")
    print(
        f"elements={s.elements_total}  mapped={s.mapped}  abstained={s.abstained}  "
        f"skipped={s.skipped}  needs_review={s.needs_review}"
    )
    print(f"artifact -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
