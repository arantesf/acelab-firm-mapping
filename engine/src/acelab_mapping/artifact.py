"""Assemble the machine-readable result artifact from per-element decisions."""

from __future__ import annotations

from typing import Any

from .models import Decision, RunResult, RunSummary


def build_result(
    decisions: list[Decision], run_meta: dict[str, Any], products_considered: int
) -> RunResult:
    summary = RunSummary(
        elements_total=len(decisions),
        mapped=sum(d.action == "map" for d in decisions),
        abstained=sum(d.action == "abstain" for d in decisions),
        skipped=sum(d.action == "skip" for d in decisions),
        needs_review=sum(d.needs_review for d in decisions),
        products_considered=products_considered,
    )
    return RunResult(run=run_meta, summary=summary, decisions=decisions)
