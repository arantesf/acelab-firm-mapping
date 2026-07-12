from acelab_mapping.confidence import REVIEW_THRESHOLD, WRITE_THRESHOLD, composite


def test_strong_match_writes():
    c = composite(llm_confidence=0.9, room_fit="match", is_preferred=True, has_violations=False)
    assert c.band == "write"
    assert c.score >= WRITE_THRESHOLD


def test_weak_signal_drops_to_review():
    c = composite(llm_confidence=0.6, room_fit="weak", is_preferred=True, has_violations=False)
    assert c.band == "review"
    assert REVIEW_THRESHOLD <= c.score < WRITE_THRESHOLD


def test_ambiguous_room_and_low_confidence_abstains():
    c = composite(llm_confidence=0.2, room_fit="none", is_preferred=False, has_violations=False)
    assert c.band == "abstain"
    assert c.score < REVIEW_THRESHOLD


def test_a_lesson_violation_penalizes_the_score():
    kwargs = dict(llm_confidence=0.9, room_fit="match", is_preferred=True)
    clean = composite(**kwargs, has_violations=False)
    violated = composite(**kwargs, has_violations=True)
    assert violated.score < clean.score
    assert violated.components["lessons_penalty"] == 0.6


def test_components_are_reported_for_audit():
    c = composite(llm_confidence=0.5, room_fit="weak", is_preferred=False, has_violations=False)
    assert set(c.components) == {"room", "approved", "llm", "lessons_penalty"}
