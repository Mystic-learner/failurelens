from app.services.diagnosis import EvidenceChunk
from app.services.evidence_context import collapse_repeats, merge_context, normalize


def test_normalize_drops_timestamp() -> None:
    assert normalize("2026-11-13 10:32:50.054572 ERROR boom") == "ERROR boom"


def test_normalize_keeps_line_without_timestamp() -> None:
    assert normalize("  plain line  ") == "plain line"


def test_collapse_keeps_first_of_repeats() -> None:
    a = EvidenceChunk("1", "2026-11-13 10:00:00.1 ERROR x failed")
    b = EvidenceChunk("2", "2026-11-13 10:00:05.9 ERROR x failed")
    c = EvidenceChunk("3", "2026-11-13 10:00:06.0 ERROR y failed")
    assert [x.chunk_id for x in collapse_repeats([a, b, c])] == ["1", "3"]


def test_collapse_empty() -> None:
    assert collapse_repeats([]) == []


def test_merge_adds_new_and_skips_duplicates() -> None:
    base = [EvidenceChunk("1", "2026-11-13 10:00:00.1 ERROR x failed")]
    extra = [
        EvidenceChunk("1", "2026-11-13 09:00:00.0 INFO other"),
        EvidenceChunk("2", "2026-11-13 09:59:00.0 ERROR x failed"),
        EvidenceChunk("3", "2026-11-13 09:58:00.0 INFO config reload"),
    ]
    merged = merge_context(base, extra)
    assert [c.chunk_id for c in merged] == ["1", "3"]


def test_merge_does_not_change_inputs() -> None:
    base = [EvidenceChunk("1", "a")]
    merge_context(base, [EvidenceChunk("2", "b")])
    assert len(base) == 1
