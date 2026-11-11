import json

from hypothesis import given, settings

from app.services.stream_processor import ProcessedChunk, StreamProcessor
from tests.property.test_stream_processor_props import CUTS, SPECS, build_lines, split_batches


def keys(chunks: list[ProcessedChunk]) -> list[tuple[str, str | None, str]]:
    return [(c.chunk.chunk_hash, c.fingerprint, c.chunk.level) for c in chunks]


@settings(max_examples=150, deadline=None)
@given(SPECS, CUTS)
def test_saving_and_restoring_state_between_every_batch_changes_nothing(
    specs: list[tuple[str, str, int]], cuts: list[int]
) -> None:
    lines, _ = build_lines(specs)
    batches = split_batches(lines, cuts)

    continuous = StreamProcessor("s")
    expected: list[ProcessedChunk] = []
    for batch in batches:
        expected += continuous.feed_text(batch)
    expected += continuous.flush()

    state: dict[str, object] = {}
    restored: list[ProcessedChunk] = []
    for batch in batches:
        p = StreamProcessor("s")
        p.import_state(json.loads(json.dumps(state)))
        restored += p.feed_text(batch)
        state = p.export_state()
    last = StreamProcessor("s")
    last.import_state(json.loads(json.dumps(state)))
    restored += last.flush()

    assert keys(restored) == keys(expected)
