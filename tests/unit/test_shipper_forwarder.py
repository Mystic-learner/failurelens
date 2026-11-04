from pathlib import Path

import pytest

from shipper.backoff import Backoff
from shipper.forwarder import Forwarder
from shipper.reader import Batch
from shipper.sender import SendResult
from shipper.spool import Spool


def batch(seq: int) -> Batch:
    data = f"line {seq}\n".encode()
    return Batch(seq, f"key{seq}", data, "1:2", 0, seq * 10, seq * 10 + len(data), 1)


class FakeClient:
    def __init__(self) -> None:
        self.sent: list[int] = []
        self.results: list[SendResult] = []
        self.committed: set[str] | None = set()
        self.asked: list[list[str]] = []

    async def send(self, batch: Batch) -> SendResult:
        self.sent.append(batch.seq)
        return self.results.pop(0) if self.results else SendResult("accepted", 202)

    async def committed_keys(self, keys: list[str]) -> set[str] | None:
        self.asked.append(keys)
        return None if self.committed is None else self.committed & set(keys)


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def make(tmp_path: Path, count: int, **kwargs: float) -> tuple[Forwarder, FakeClient, Spool, Clock]:
    spool = Spool(tmp_path / "spool", fsync=False)
    for seq in range(1, count + 1):
        spool.put(batch(seq))
    client = FakeClient()
    clock = Clock()
    forwarder = Forwarder(
        spool,
        client,
        Backoff(base_s=1.0, cap_s=8.0, rng=lambda: 0.5),
        clock=clock,
        **kwargs,  # type: ignore[arg-type]
    )
    return forwarder, client, spool, clock


@pytest.mark.asyncio
async def test_idle_when_the_spool_is_empty(tmp_path: Path) -> None:
    forwarder, client, _, _ = make(tmp_path, 0, idle_s=0.5)
    assert await forwarder.step() == 0.5
    assert client.sent == []
    assert client.asked == []


@pytest.mark.asyncio
async def test_sends_in_order_and_keeps_batches_until_their_key_is_committed(tmp_path: Path) -> None:
    forwarder, client, spool, _ = make(tmp_path, 3, poll_s=0.2)
    assert await forwarder.step() == 0.2
    assert client.sent == [1, 2, 3]
    assert spool.pending_seqs() == [1, 2, 3]  # accepted is not committed

    client.committed = {"key1", "key2"}
    await forwarder.step()
    assert spool.pending_seqs() == [3]
    assert client.sent == [1, 2, 3]  # 3 is not resent while we wait for its commit

    client.committed = {"key1", "key2", "key3"}
    assert await forwarder.step() == 0.5
    assert len(spool) == 0
    assert forwarder.confirmed == 3


@pytest.mark.asyncio
async def test_out_of_order_commit_removes_only_that_batch(tmp_path: Path) -> None:
    forwarder, client, spool, _ = make(tmp_path, 3)
    await forwarder.step()
    client.committed = {"key3"}  # batch 3 committed, 1 and 2 not (e.g. dropped by a worker)
    await forwarder.step()
    assert spool.pending_seqs() == [1, 2]


@pytest.mark.asyncio
async def test_retry_stops_the_step_and_returns_a_backoff_delay(tmp_path: Path) -> None:
    forwarder, client, spool, _ = make(tmp_path, 3)
    client.results = [SendResult("retry", 503, detail="HTTP 503")]
    assert await forwarder.step() == 0.5
    assert client.sent == [1]
    assert spool.pending_seqs() == [1, 2, 3]

    await forwarder.step()
    assert client.sent == [1, 1, 2, 3]


@pytest.mark.asyncio
async def test_backoff_grows_and_resets_after_a_success(tmp_path: Path) -> None:
    forwarder, client, _, _ = make(tmp_path, 1)
    client.results = [SendResult("retry"), SendResult("retry"), SendResult("accepted", 202)]
    assert await forwarder.step() == 0.5
    assert await forwarder.step() == 1.0
    await forwarder.step()  # success resets the attempt counter
    client.results = [SendResult("retry")]
    client.committed = set()
    forwarder._sent.clear()  # noqa: SLF001 - force a resend to observe the delay
    assert await forwarder.step() == 0.5


@pytest.mark.asyncio
async def test_retry_after_is_a_floor_for_the_delay(tmp_path: Path) -> None:
    forwarder, client, _, _ = make(tmp_path, 1)
    client.results = [SendResult("retry", 429, retry_after=10.0)]
    assert await forwarder.step() == 10.0


@pytest.mark.asyncio
async def test_accepted_but_never_committed_batch_is_resent(tmp_path: Path) -> None:
    forwarder, client, _, clock = make(tmp_path, 1, resend_after_s=30.0)
    await forwarder.step()
    clock.now = 29.0
    await forwarder.step()
    assert client.sent == [1]
    clock.now = 31.0  # the server must have lost it (crash after the 202)
    await forwarder.step()
    assert client.sent == [1, 1]


@pytest.mark.asyncio
async def test_rejected_batch_is_dropped_and_recorded(tmp_path: Path) -> None:
    forwarder, client, spool, _ = make(tmp_path, 2)
    client.results = [SendResult("rejected", 413, detail="HTTP 413")]
    await forwarder.step()
    assert forwarder.dropped == [(1, "HTTP 413")]
    assert client.sent == [1, 2]
    assert spool.pending_seqs() == [2]


@pytest.mark.asyncio
async def test_unreachable_status_endpoint_prunes_nothing(tmp_path: Path) -> None:
    forwarder, client, spool, _ = make(tmp_path, 2)
    client.committed = None
    await forwarder.step()
    assert spool.pending_seqs() == [1, 2]


@pytest.mark.asyncio
async def test_status_query_is_capped(tmp_path: Path) -> None:
    forwarder, client, _, _ = make(tmp_path, 5, max_status_keys=2)
    await forwarder.step()
    assert client.asked == [["key1", "key2"]]


@pytest.mark.asyncio
async def test_a_step_sends_at_most_max_sends(tmp_path: Path) -> None:
    forwarder, client, _, _ = make(tmp_path, 5, max_sends_per_step=2)
    assert await forwarder.step() == 0.0
    assert client.sent == [1, 2]
    assert await forwarder.step() == 0.0
    assert client.sent == [1, 2, 3, 4]


def test_spool_get_and_key_of(tmp_path: Path) -> None:
    spool = Spool(tmp_path / "spool", fsync=False)
    spool.put(batch(4))
    assert spool.get(4) == batch(4)
    assert spool.get(5) is None
    assert spool.key_of(4) == "key4"
    assert spool.key_of(5) is None


def test_invalid_settings_are_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        make(tmp_path, 0, resend_after_s=0)