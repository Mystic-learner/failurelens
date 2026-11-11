from pathlib import Path

import pytest

from shipper.reader import Batch
from shipper.spool import Spool


def batch(seq: int, data: bytes = b"line\n") -> Batch:
    return Batch(
        seq, f"key{seq}", data, "1:2", 0, seq * 10, seq * 10 + len(data), data.count(b"\n")
    )


def make(tmp_path: Path, **kwargs: int) -> Spool:
    return Spool(tmp_path / "spool", fsync=False, **kwargs)


def test_empty_spool(tmp_path: Path) -> None:
    s = make(tmp_path)
    assert len(s) == 0
    assert s.peek() is None
    assert s.bytes_used == 0


def test_roundtrip_preserves_every_field_and_binary_data(tmp_path: Path) -> None:
    s = make(tmp_path)
    data = b"a\r\nb \xff\xfe \x00 \xf0\x9f\x94\xa5\n\n\nlast\n"
    original = batch(7, data)
    assert s.put(original)
    assert s.peek() == original


def test_delivery_is_in_sequence_order(tmp_path: Path) -> None:
    s = make(tmp_path)
    for seq in (3, 1, 2):
        assert s.put(batch(seq))
    assert s.pending_seqs() == [1, 2, 3]
    first = s.peek()
    assert first is not None and first.seq == 1
    s.pop(1)
    second = s.peek()
    assert second is not None and second.seq == 2


def test_putting_the_same_seq_twice_is_a_noop(tmp_path: Path) -> None:
    s = make(tmp_path)
    assert s.put(batch(1))
    assert s.put(batch(1, b"different\n"))
    assert len(s) == 1
    assert s.peek() == batch(1)


def test_spool_is_bounded_but_an_empty_spool_accepts_one_oversized_batch(tmp_path: Path) -> None:
    s = make(tmp_path, max_bytes=300)
    assert s.put(batch(1, b"x" * 1000 + b"\n"))
    assert s.put(batch(2)) is False
    s.pop(1)
    assert s.put(batch(2))


def test_prune_through_removes_committed_batches(tmp_path: Path) -> None:
    s = make(tmp_path)
    for seq in range(1, 6):
        s.put(batch(seq))
    assert s.prune_through(3) == 3
    assert s.pending_seqs() == [4, 5]
    assert s.prune_through(3) == 0
    assert sorted(p.name for p in (tmp_path / "spool").iterdir()) == [
        f"{4:020d}.batch",
        f"{5:020d}.batch",
    ]


def test_survives_a_restart(tmp_path: Path) -> None:
    s1 = make(tmp_path)
    s1.put(batch(1))
    s1.put(batch(2))
    s2 = make(tmp_path)
    assert s2.pending_seqs() == [1, 2]
    assert s2.peek() == batch(1)


def test_truncated_file_is_quarantined_and_skipped(tmp_path: Path) -> None:
    s = make(tmp_path)
    s.put(batch(1, b"hello world\n"))
    s.put(batch(2))
    path = tmp_path / "spool" / f"{1:020d}.batch"
    path.write_bytes(path.read_bytes()[:-5])  # cut off the end, e.g. a disk problem
    got = s.peek()
    assert got is not None and got.seq == 2
    assert s.corrupt == [path.name]
    assert (tmp_path / "spool" / (path.name + ".corrupt")).exists()
    assert s.pending_seqs() == [2]


def test_garbage_file_is_quarantined(tmp_path: Path) -> None:
    make(tmp_path)
    (tmp_path / "spool" / f"{1:020d}.batch").write_bytes(b"not a header at all")
    s2 = make(tmp_path)
    assert s2.peek() is None
    assert len(s2.corrupt) == 1


def test_leftover_temp_files_from_a_crash_are_removed(tmp_path: Path) -> None:
    (tmp_path / "spool").mkdir()
    (tmp_path / "spool" / f"{9:020d}.batch.tmp").write_bytes(b"half")
    s = make(tmp_path)
    assert len(s) == 0
    assert list((tmp_path / "spool").iterdir()) == []


def test_invalid_size_limit_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        make(tmp_path, max_bytes=0)
