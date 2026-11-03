import os
from pathlib import Path

import pytest

from shipper.reader import Batch, LogReader, make_key
from shipper.state import StateStore


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def make(tmp_path: Path, clock: Clock | None = None, **kwargs: object) -> LogReader:
    kwargs.setdefault("max_wait_s", 1.0)
    return LogReader(
        tmp_path / "app.log",
        "src",
        StateStore(tmp_path / "state" / "state.json", fsync=False),
        clock=clock or Clock(),
        **kwargs,  # type: ignore[arg-type]
    )


def append(path: Path, data: bytes) -> None:
    with open(path, "ab") as fh:
        fh.write(data)


def drain(reader: LogReader) -> list[Batch]:
    out: list[Batch] = []
    while True:
        batch = reader.next_batch(force=True)
        if batch is None:
            return out
        reader.ack(batch)
        out.append(batch)


def lines(n: int, prefix: str = "line") -> bytes:
    return "".join(f"{prefix} {i}\n" for i in range(n)).encode()


def test_batch_waits_for_max_wait(tmp_path: Path) -> None:
    clock = Clock()
    r = make(tmp_path, clock)
    (tmp_path / "app.log").write_bytes(lines(3))
    assert r.next_batch() is None
    clock.now = 1.0
    batch = r.next_batch()
    assert batch is not None
    assert (batch.seq, batch.lines, batch.start, batch.end) == (1, 3, 0, len(lines(3)))
    assert batch.data == lines(3)
    r.ack(batch)
    assert r.next_batch(force=True) is None


def test_batches_are_cut_at_max_lines_and_cover_the_file_exactly(tmp_path: Path) -> None:
    r = make(tmp_path, max_lines=3)
    (tmp_path / "app.log").write_bytes(lines(7))
    batches = drain(r)
    assert [b.lines for b in batches] == [3, 3, 1]
    assert [b.seq for b in batches] == [1, 2, 3]
    assert batches[0].start == 0
    assert all(a.end == b.start for a, b in zip(batches, batches[1:], strict=False))
    assert b"".join(b.data for b in batches) == lines(7)
    assert len({b.key for b in batches}) == 3


def test_batches_are_cut_at_max_bytes(tmp_path: Path) -> None:
    r = make(tmp_path, max_batch_bytes=25)
    (tmp_path / "app.log").write_bytes(b"aaaaaaaaa\n" * 7)
    batches = drain(r)
    assert [b.lines for b in batches] == [3, 3, 1]


def test_unterminated_last_line_waits_for_its_newline(tmp_path: Path) -> None:
    path = tmp_path / "app.log"
    path.write_bytes(b"abc\npart")
    r = make(tmp_path)
    first = drain(r)
    assert [b.data for b in first] == [b"abc\n"]
    append(path, b"ial\n")
    second = drain(r)
    assert [b.data for b in second] == [b"partial\n"]
    assert second[0].start == 4


def test_an_unacknowledged_batch_is_returned_again(tmp_path: Path) -> None:
    r = make(tmp_path)
    (tmp_path / "app.log").write_bytes(lines(2))
    first = r.next_batch(force=True)
    append(tmp_path / "app.log", lines(2, "more"))
    assert r.next_batch(force=True) == first


def test_ack_of_the_wrong_batch_raises(tmp_path: Path) -> None:
    r = make(tmp_path)
    (tmp_path / "app.log").write_bytes(lines(2))
    batch = r.next_batch(force=True)
    assert batch is not None
    with pytest.raises(ValueError):
        r.ack(Batch(99, "x", b"x\n", "f", 0, 0, 1, 1))
    r.ack(batch)
    with pytest.raises(ValueError):
        r.ack(batch)


def test_state_file_follows_the_cut_and_the_ack(tmp_path: Path) -> None:
    r = make(tmp_path)
    (tmp_path / "app.log").write_bytes(lines(2))
    batch = r.next_batch(force=True)
    assert batch is not None
    store = StateStore(tmp_path / "state" / "state.json")
    saved = store.load()
    assert saved.offset == 0
    assert saved.inflight is not None
    assert (saved.inflight.start, saved.inflight.end) == (batch.start, batch.end)
    r.ack(batch)
    saved = store.load()
    assert saved.offset == batch.end
    assert saved.seq == 1
    assert saved.inflight is None


def test_restart_resumes_after_the_acknowledged_offset(tmp_path: Path) -> None:
    (tmp_path / "app.log").write_bytes(lines(6))
    r1 = make(tmp_path, max_lines=3)
    first = r1.next_batch(force=True)
    assert first is not None
    r1.ack(first)
    r1.close()

    r2 = make(tmp_path, max_lines=3)
    rest = drain(r2)
    assert [b.seq for b in rest] == [2]
    assert rest[0].start == first.end
    assert first.data + rest[0].data == lines(6)


def test_restart_resends_the_unacknowledged_batch_identically(tmp_path: Path) -> None:
    path = tmp_path / "app.log"
    path.write_bytes(lines(3))
    r1 = make(tmp_path)
    sent = r1.next_batch(force=True)
    assert sent is not None
    r1.close()  # crash before ack
    append(path, lines(2, "later"))

    r2 = make(tmp_path)
    again = r2.next_batch()
    assert again == sent
    r2.ack(again)
    nxt = drain(r2)
    assert [b.seq for b in nxt] == [2]
    assert nxt[0].start == sent.end
    assert nxt[0].data == lines(2, "later")


def test_key_is_deterministic_and_depends_on_every_part() -> None:
    base = make_key("s", "f", 0, 0, 10)
    assert base == make_key("s", "f", 0, 0, 10)
    for other in (
        make_key("t", "f", 0, 0, 10),
        make_key("s", "g", 0, 0, 10),
        make_key("s", "f", 1, 0, 10),
        make_key("s", "f", 0, 1, 10),
        make_key("s", "f", 0, 0, 11),
    ):
        assert other != base


def test_rotation_switches_to_the_new_file(tmp_path: Path) -> None:
    path = tmp_path / "app.log"
    path.write_bytes(lines(2, "old"))
    r = make(tmp_path)
    old = drain(r)
    os.replace(path, tmp_path / "app.log.1")
    path.write_bytes(lines(2, "new"))

    new = drain(r)
    assert len(new) == 1
    assert new[0].data == lines(2, "new")
    assert new[0].start == 0
    assert new[0].file_id != old[0].file_id
    assert new[0].seq == 2
    assert new[0].key != old[0].key


@pytest.mark.skipif(
    os.name == "nt",
    reason="Windows cannot rename a file the reader holds open, so the reader does not hold it "
    "there and the unread tail of a rotated file cannot be drained (documented limit)",
)
def test_rotation_drains_the_unread_tail_of_the_old_file(tmp_path: Path) -> None:
    path = tmp_path / "app.log"
    path.write_bytes(lines(2, "old"))
    r = make(tmp_path, hold_open=True)
    assert len(drain(r)) == 1
    append(path, b"tail line\nunterminated")
    os.replace(path, tmp_path / "app.log.1")
    path.write_bytes(b"fresh\n")

    batches = drain(r)
    assert [b.data for b in batches] == [b"tail line\nunterminated\n", b"fresh\n"]
    assert batches[1].start == 0
    assert [b.seq for b in batches] == [2, 3]


def test_truncation_restarts_at_zero_with_a_new_generation(tmp_path: Path) -> None:
    path = tmp_path / "app.log"
    path.write_bytes(b"aaaaaaaaaa\n" * 3)
    r = make(tmp_path, max_lines=1)
    before = drain(r)
    assert [(b.start, b.end) for b in before] == [(0, 11), (11, 22), (22, 33)]

    path.write_bytes(b"bbbbbbbbbb\n")  # smaller than our position: truncated in place
    after = drain(r)
    assert len(after) == 1
    assert (after[0].start, after[0].end, after[0].gen) == (0, 11, 1)
    assert after[0].data == b"bbbbbbbbbb\n"
    assert after[0].key != before[0].key  # same range, different generation


def test_missing_file_is_picked_up_when_it_appears(tmp_path: Path) -> None:
    r = make(tmp_path)
    assert r.next_batch(force=True) is None
    (tmp_path / "app.log").write_bytes(lines(1))
    assert [b.data for b in drain(r)] == [lines(1)]


def test_from_beginning_false_skips_existing_content(tmp_path: Path) -> None:
    path = tmp_path / "app.log"
    path.write_bytes(b"old1\nold2\n")
    r = make(tmp_path, from_beginning=False)
    assert drain(r) == []
    append(path, b"new\n")
    batches = drain(r)
    assert [b.data for b in batches] == [b"new\n"]
    assert batches[0].start == 10


def test_crlf_unicode_invalid_utf8_and_null_bytes_pass_through_untouched(tmp_path: Path) -> None:
    raw = b"a\r\nb \xff\xfe \xf0\x9f\x94\xa5\x00\r\nlast\n"
    (tmp_path / "app.log").write_bytes(raw)
    batches = drain(make(tmp_path))
    assert b"".join(b.data for b in batches) == raw


def test_one_megabyte_line_is_one_batch(tmp_path: Path) -> None:
    raw = b"2024-01-01 10:00:00 INFO " + b"a" * 1_000_000 + b"\n"
    (tmp_path / "app.log").write_bytes(raw)
    batches = drain(make(tmp_path))
    assert len(batches) == 1
    assert batches[0].data == raw
    assert batches[0].lines == 1


def test_line_longer_than_the_line_limit_is_split_without_loss(tmp_path: Path) -> None:
    raw = b"x" * 25 + b"\nnext\n"
    (tmp_path / "app.log").write_bytes(raw)
    batches = drain(make(tmp_path, max_line_bytes=10))
    assert b"".join(b.data for b in batches) == raw


def test_corrupt_state_is_reported_and_the_file_is_shipped_from_the_start(
    tmp_path: Path,
) -> None:
    (tmp_path / "state").mkdir()
    (tmp_path / "state" / "state.json").write_bytes(b"garbage")
    (tmp_path / "app.log").write_bytes(lines(2))
    r = make(tmp_path)
    assert r.load_error is not None
    assert b"".join(b.data for b in drain(r)) == lines(2)


def test_unacknowledged_batch_of_a_vanished_file_is_dropped_and_its_seq_not_reused(
    tmp_path: Path,
) -> None:
    path = tmp_path / "app.log"
    path.write_bytes(lines(2, "old"))
    r1 = make(tmp_path)
    assert r1.next_batch(force=True) is not None  # cut, never acknowledged
    r1.close()
    os.replace(path, tmp_path / "app.log.1")  # rotated away while we were down
    path.write_bytes(lines(2, "new"))

    r2 = make(tmp_path)
    assert r2.lost_inflight is True
    batches = drain(r2)
    assert [b.data for b in batches] == [lines(2, "new")]
    assert batches[0].seq == 2


def test_invalid_limits_are_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        make(tmp_path, max_lines=0)