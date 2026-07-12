from acelab_mapping.decider.caching import CachingDecider
from acelab_mapping.models import (
    CandidateProduct,
    CandidateStandard,
    DecisionContext,
    Element,
    RawDecision,
)

DECISION = RawDecision(abstain=False, standard_id="s", product_id="cl-1004", confidence=0.9)


def _context(element_id: int, room: str = "Open Office", level: str = "Level 1") -> DecisionContext:
    return DecisionContext(
        element=Element(element_id=element_id, category="Ceilings", type="Generic - Lay-in",
                        level=level, room=room),
        candidates=[
            CandidateStandard(id="s", intent="s", requirements_summary="",
                              qualified=[CandidateProduct(product_id="cl-1004", name="X", is_preferred=True)])
        ],
    )


class Counting:
    def __init__(self):
        self.calls = 0

    def decide(self, context):
        self.calls += 1
        return DECISION


class Boom:
    def decide(self, context):
        raise AssertionError("inner decider must not be called on a cache hit")


def test_identical_elements_hit_one_call():
    inner = Counting()
    decider = CachingDecider(inner)
    # same category/room/candidates, different element_id + level + type-irrelevant fields
    decider.decide(_context(1, level="Level 1"))
    decider.decide(_context(2, level="Level 7"))
    assert inner.calls == 1  # deduped to a single inner call


def test_different_rooms_do_not_collide():
    inner = Counting()
    decider = CachingDecider(inner)
    decider.decide(_context(1, room="Open Office"))
    decider.decide(_context(2, room="Restroom 201"))
    assert inner.calls == 2


def test_replay_from_disk_needs_no_inner_call(tmp_path):
    path = tmp_path / "cache.json"
    CachingDecider(Counting(), path=path).decide(_context(1))  # records to disk
    assert path.exists()

    replay = CachingDecider(Boom(), path=path)  # Boom raises if called
    out = replay.decide(_context(99))  # same key -> served from disk
    assert out.product_id == "cl-1004"


def test_decide_batch_dedups_and_preserves_order():
    class Echo:
        def __init__(self):
            self.calls = 0

        def decide(self, context):
            self.calls += 1
            return RawDecision(abstain=True, abstain_reason=context.element.room)

    inner = Echo()
    decider = CachingDecider(inner)
    contexts = [
        _context(1, room="Open Office"),
        _context(2, room="Restroom 201"),
        _context(3, room="Open Office"),  # same key as #1 -> must not call inner again
    ]
    out = decider.decide_batch(contexts)
    assert inner.calls == 2  # two distinct keys; the duplicate is deduped before dispatch
    assert [d.abstain_reason for d in out] == ["Open Office", "Restroom 201", "Open Office"]


def test_namespace_isolates_models(tmp_path):
    path = tmp_path / "cache.json"
    a = CachingDecider(Counting(), path=path, namespace="model-a")
    b = CachingDecider(Counting(), path=path, namespace="model-b")
    a.decide(_context(1))
    b.inner = Counting()
    b.decide(_context(1))  # different namespace -> not a hit
    assert b.inner.calls == 1
