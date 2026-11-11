from hypothesis import given, settings
from hypothesis import strategies as st

from app.services.parsing import BinaryInputError
from app.services.stream_processor import ProcessedChunk, StreamProcessor

# Letters a-h only: no level words, no secrets, nothing the redactor or parser reacts to.
WORD = st.text(alphabet="abcdefgh", min_size=3, max_size=10)
LEVELS = st.sampled_from(["INFO", "WARN", "ERROR", "DEBUG"])
RECORD_SPEC = st.tuples(LEVELS, WORD, st.integers(min_value=0, max_value=5))
SPECS = st.lists(RECORD_SPEC, min_size=1, max_size=30)
CUTS = st.lists(st.integers(min_value=0, max_value=400), max_size=8)


def build_lines(specs: list[tuple[str, str, int]]) -> tuple[list[str], list[list[str]]]:
    """All input lines, plus the lines of every record that carries a stack trace."""
    lines: list[str] = []
    traces: list[list[str]] = []
    for i, (level, word, n_trace) in enumerate(specs):
        record = [f"2024-01-01 10:00:{i:02d} {level} rec{i} {word}"]
        record += [f"    at com.acme.C{i}.m{j}(C.java:{j})" for j in range(n_trace)]
        lines += record
        if n_trace:
            traces.append(record)
    return lines, traces


def split_batches(lines: list[str], cuts: list[int]) -> list[str]:
    points = sorted({c % (len(lines) + 1) for c in cuts} | {0, len(lines)})
    return ["\n".join(lines[a:b]) + "\n" for a, b in zip(points, points[1:], strict=False) if b > a]


def process(batches: list[str]) -> list[ProcessedChunk]:
    p = StreamProcessor("s")
    out: list[ProcessedChunk] = []
    for batch in batches:
        out += p.feed_text(batch)
    out += p.flush()
    return out


@settings(max_examples=200, deadline=None)
@given(SPECS, CUTS)
def test_no_line_lost_or_duplicated(specs: list[tuple[str, str, int]], cuts: list[int]) -> None:
    lines, _ = build_lines(specs)
    out = process(split_batches(lines, cuts))
    rebuilt = "\n".join(c.chunk.text for c in out).split("\n")
    assert rebuilt == lines
    assert sum(c.chunk.line_count for c in out) == len(lines)


@settings(max_examples=200, deadline=None)
@given(SPECS, CUTS)
def test_stack_traces_stay_whole(specs: list[tuple[str, str, int]], cuts: list[int]) -> None:
    lines, traces = build_lines(specs)
    out = process(split_batches(lines, cuts))
    by_text = {c.chunk.text: c for c in out}
    for trace in traces:
        found = by_text.get("\n".join(trace))
        assert found is not None
        assert found.chunk.record_count == 1
        assert "stack_trace" in found.chunk.flags


@settings(max_examples=200, deadline=None)
@given(SPECS, CUTS)
def test_error_and_trace_hashes_do_not_depend_on_batching(
    specs: list[tuple[str, str, int]], cuts: list[int]
) -> None:
    lines, _ = build_lines(specs)

    def isolated_hashes(chunks: list[ProcessedChunk]) -> list[str]:
        return sorted(c.chunk.chunk_hash for c in chunks if c.fingerprint is not None)

    whole = process(["\n".join(lines) + "\n"])
    split = process(split_batches(lines, cuts))
    assert isolated_hashes(whole) == isolated_hashes(split)


@settings(max_examples=200, deadline=None)
@given(
    st.lists(
        st.one_of(st.binary(max_size=600), st.text(max_size=300).map(lambda s: s.encode())),
        max_size=6,
    )
)
def test_arbitrary_bytes_never_crash(batches: list[bytes]) -> None:
    p = StreamProcessor("s")
    out: list[ProcessedChunk] = []
    for data in batches:
        try:
            out += p.feed_bytes(data)
        except BinaryInputError:
            pass
    out += p.flush()
    for c in out:
        assert len(c.chunk.chunk_hash) == 64
        assert c.fingerprint is None or len(c.fingerprint) == 64
        assert "\x00" not in c.chunk.text
