from pathlib import Path

from shipper.state import Inflight, ShipperState, StateStore


def test_missing_file_gives_fresh_state(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "state.json")
    assert store.load() == ShipperState()
    assert store.load_error is None


def test_roundtrip_with_inflight(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "state.json")
    state = ShipperState("1:2", 3, 400, 9, Inflight(10, 400, 900, "1:2", 3, 5))
    store.save(state)
    assert StateStore(tmp_path / "state.json").load() == state


def test_save_leaves_no_temp_file_and_creates_the_directory(tmp_path: Path) -> None:
    path = tmp_path / "deep" / "dir" / "state.json"
    store = StateStore(path)
    store.save(ShipperState(offset=5))
    assert sorted(p.name for p in path.parent.iterdir()) == ["state.json"]


def test_corrupt_file_is_reported_and_gives_fresh_state(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    path.write_bytes(b"{not json")
    store = StateStore(path)
    assert store.load() == ShipperState()
    assert store.load_error is not None


def test_wrong_shape_and_negative_numbers_are_rejected(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    for bad in (b"[]", b'{"gen": 0}', b'{"file_id": "x", "gen": 0, "offset": -1, "seq": 0}'):
        path.write_bytes(bad)
        store = StateStore(path)
        assert store.load() == ShipperState()
        assert store.load_error is not None


def test_a_good_load_clears_an_earlier_error(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    path.write_bytes(b"garbage")
    store = StateStore(path)
    store.load()
    store.save(ShipperState(offset=1))
    store.load()
    assert store.load_error is None
