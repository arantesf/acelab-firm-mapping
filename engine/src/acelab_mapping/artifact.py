"""Assemble the machine-readable result artifact from per-element decisions."""

from __future__ import annotations

from typing import Any

from .models import Decision, DecisionGroup, RunResult, RunSummary


def _build_groups(decisions: list[Decision]) -> list[DecisionGroup]:
    """Collapse per-element decisions into one entry per distinct (category, type, room). Elements
    that share that key share the same decision (the decider is deduped on it), so the group carries
    the shared fields plus the element ids it covers. Most-common group first; ties keep input order."""
    order: list[tuple] = []
    buckets: dict[tuple, list[Decision]] = {}
    for d in decisions:
        key = (d.category, d.type, d.room)
        if key not in buckets:
            buckets[key] = []
            order.append(key)
        buckets[key].append(d)

    groups = []
    for key in order:
        members = buckets[key]
        rep = members[0]
        groups.append(
            DecisionGroup(
                category=rep.category,
                type=rep.type,
                room=rep.room,
                action=rep.action,
                count=len(members),
                element_ids=[m.element_id for m in members],
                matched_standard=rep.matched_standard,
                chosen_product=rep.chosen_product,
                confidence=rep.confidence,
                why=rep.why,
                needs_review=rep.needs_review,
                note=rep.note,
                revit_write=rep.revit_write,
            )
        )
    groups.sort(key=lambda g: -g.count)  # stable: equal counts keep first-appearance order
    return groups


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
    return RunResult(
        run=run_meta,
        summary=summary,
        groups=_build_groups(decisions),
        decisions=decisions,
    )
