from pathlib import Path

from shipper.reader import Batch
from shipper.spool import Spool


def batch(seq: int) -> Batch:
    data = f"line {seq}\n".encode()
    return Batch(seq, f"key{seq}", data, "1:2", 0, seq * 10, seq * 10 + len(data), 1)


def make(tmp_path: Path) -> Spool:
    return Spool(tmp_path / "spool", fsync=False)


def test_pending_lists_seq_and_key_oldest_first(tmp_path: Path) -> None:
    s = make(tmp_path)
    for seq in (3, 1, 2):
        s.put(batch(seq))
    assert s.pending() == [(1, "key1"), (2, "key2"), (3, "key3")]


def test_keys_survive_a_restart_without_reading_the_batch_data(tmp_path: Path) -> None:
    s1 = make(tmp_path)
    s1.put(batch(1))
    s1.put(batch(2))
    assert make(tmp_path).pending() == [(1, "key1"), (2, "key2")]


def test_pop_and_prune_forget_the_key(tmp_path: Path) -> None:
    s = make(tmp_path)
    for seq in (1, 2, 3):
        s.put(batch(seq))
    s.pop(1)
    s.prune_through(2)
    assert s.pending() == [(3, "key3")]


def test_a_batch_quarantined_on_read_disappears_from_pending(tmp_path: Path) -> None:
    s = make(tmp_path)
    s.put(batch(1))
    s.put(batch(2))
    path = tmp_path / "spool" / f"{1:020d}.batch"
    path.write_bytes(path.read_bytes()[:-3])  # header intact, data truncated
    assert s.get(1) is None
    assert s.pending() == [(2, "key2")]


def test_a_file_with_an_unreadable_header_is_quarantined_at_startup(tmp_path: Path) -> None:
    (tmp_path / "spool").mkdir()
    (tmp_path / "spool" / f"{1:020d}.batch").write_bytes(b'{"seq": 1}\nno key in this header')
    s = make(tmp_path)
    assert s.pending() == []
    assert len(s.corrupt) == 1
