"""Sustained-load test for the running FailureLens stack.

Sends --rate lines/s for --duration seconds to POST /ingest/stream, then reports:
  - loss: batches answered 202 but not committed (must be 0), plus a line-count check
  - ingest-to-searchable latency p50/p95 (dispatch until the batch's marker chunk is stored)
  - peak memory of the app container (docker stats polling; n/a if docker is unavailable)

Needs the stack up (docker compose up -d --wait db app) and direct DB access for the checks.
Usage: python eval/load_test.py [--rate 1000] [--duration 60] [--lines-per-batch 100]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import re
import sys
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import asyncpg
import httpx

DEFAULT_DSN = "postgresql://failurelens:failurelens@localhost:55432/failurelens"
MARKER_SQL = (
    "SELECT m[1] FROM chunks, regexp_matches(text, 'mk=([A-Za-z0-9]+)', 'g') AS m "
    "WHERE service = $1 AND created_at >= $2"
)
COMMITTED_SQL = (
    "SELECT count(*) FILTER (WHERE b.idempotency_key LIKE $2), "
    "COALESCE(sum(b.line_count) FILTER (WHERE b.idempotency_key LIKE $2), 0) "
    "FROM ingest_batches b JOIN streams s ON s.id = b.stream_id WHERE s.service = $1"
)
_UNITS = {"B": 1, "KB": 1e3, "MB": 1e6, "GB": 1e9, "KIB": 1024, "MIB": 1024**2, "GIB": 1024**3}


def percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(1, math.ceil(q * len(ordered))) - 1]


def parse_mem(text: str) -> float | None:
    m = re.match(r"\s*([\d.]+)\s*([A-Za-z]+)", text.split("/")[0])
    if not m or m.group(2).upper() not in _UNITS:
        return None
    return float(m.group(1)) * _UNITS[m.group(2).upper()]


def make_body(run: str, index: int, lines: int) -> bytes:
    out: list[str] = []
    for j in range(lines):
        ts = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S.%f")
        mark = f" mk={run}b{index}" if j == 0 else ""
        out.append(f"{ts} INFO loadtest worker={j % 8} handled item {index * lines + j}{mark}")
    return ("\n".join(out) + "\n").encode()


async def poll_memory(container: str, stop: asyncio.Event, peaks: list[float]) -> None:
    while not stop.is_set():
        try:
            proc = await asyncio.create_subprocess_exec(
                "docker", "stats", "--no-stream", "--format", "{{.MemUsage}}", container,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
            )  # fmt: skip
            out, _ = await proc.communicate()
            value = parse_mem(out.decode(errors="replace"))
            if value is not None:
                peaks.append(value)
        except OSError:
            return
        try:
            await asyncio.wait_for(stop.wait(), timeout=2.0)
        except TimeoutError:
            pass


async def poll_markers(
    conn: asyncpg.Connection, service: str, since: datetime, stop: asyncio.Event,
    seen: dict[str, float],
) -> None:  # fmt: skip
    while not stop.is_set():
        for row in await conn.fetch(MARKER_SQL, service, since):
            seen.setdefault(str(row[0]), time.monotonic())
        try:
            await asyncio.wait_for(stop.wait(), timeout=0.5)
        except TimeoutError:
            pass


async def run(args: argparse.Namespace) -> int:
    run_id = uuid.uuid4().hex[:8]
    service, source = f"loadtest-{run_id}", f"src-{run_id}"
    per_s = args.rate / args.lines_per_batch
    total = int(args.duration * per_s)
    sent_at: dict[str, float] = {}
    accepted: set[int] = set()
    seen: dict[str, float] = {}
    counters = {"r429": 0, "r503": 0, "failed": 0}
    peaks: list[float] = []
    sem = asyncio.Semaphore(args.concurrency)

    poll_conn = await asyncpg.connect(args.dsn)
    since = await poll_conn.fetchval("SELECT now()")
    stop = asyncio.Event()
    tasks = [asyncio.create_task(poll_markers(poll_conn, service, since, stop, seen))]
    tasks.append(asyncio.create_task(poll_memory(args.container, stop, peaks)))

    async with httpx.AsyncClient(base_url=args.url, timeout=30.0) as http:

        async def send(i: int, t_due: float) -> None:
            await asyncio.sleep(max(0.0, t_due - time.monotonic()))
            body = make_body(run_id, i, args.lines_per_batch)
            headers = {
                "X-Service": service,
                "X-Source-Id": source,
                "Idempotency-Key": f"lt-{run_id}-{i}",
                "X-Sequence": str(i + 1),
            }
            sent_at[f"{run_id}b{i}"] = time.monotonic()
            async with sem:
                deadline = time.monotonic() + 60
                while time.monotonic() < deadline:
                    try:
                        resp = await http.post("/ingest/stream", content=body, headers=headers)
                    except httpx.HTTPError:
                        counters["failed"] += 1
                        await asyncio.sleep(1.0)
                        continue
                    if resp.status_code == 202:
                        accepted.add(i)
                        return
                    if resp.status_code in (429, 503):
                        counters["r429" if resp.status_code == 429 else "r503"] += 1
                        wait = float(resp.headers.get("Retry-After", "1") or 1)
                        await asyncio.sleep(min(wait, 5.0))
                        continue
                    counters["failed"] += 1
                    return

        t0 = time.monotonic() + 0.5
        print(f"run {run_id}: {total} batches x {args.lines_per_batch} lines, {args.duration}s")
        await asyncio.gather(*(send(i, t0 + i / per_s) for i in range(total)))
        send_done = time.monotonic()

    # Drain: wait until every accepted batch is committed and every marker is visible.
    committed_batches, committed_lines = 0, 0
    drain_deadline = time.monotonic() + args.drain_timeout
    while time.monotonic() < drain_deadline:
        row = await poll_conn.fetchrow(COMMITTED_SQL, service, f"lt-{run_id}-%")
        committed_batches, committed_lines = int(row[0]), int(row[1])
        markers_ok = all(f"{run_id}b{i}" in seen for i in accepted)
        if committed_batches >= len(accepted) and markers_ok:
            break
        await asyncio.sleep(1.0)
    stop.set()
    await asyncio.gather(*tasks)
    await poll_conn.close()

    lat = [seen[m] - sent_at[m] for m in seen if m in sent_at]
    lost_batches = max(0, len(accepted) - committed_batches)
    sent_lines = len(accepted) * args.lines_per_batch
    not_searchable = sum(1 for i in accepted if f"{run_id}b{i}" not in seen)
    achieved = sent_lines / max(1e-9, send_done - t0)
    result: dict[str, Any] = {
        "run": run_id,
        "target_lines_per_s": args.rate,
        "achieved_lines_per_s": round(achieved, 1),
        "batches_planned": total,
        "batches_accepted": len(accepted),
        "batches_committed": committed_batches,
        "lost_batches": lost_batches,
        "lines_accepted": sent_lines,
        "lines_committed": committed_lines,
        "lines_missing": max(0, sent_lines - committed_lines),
        "accepted_but_not_searchable": not_searchable,
        "responses_429": counters["r429"],
        "responses_503": counters["r503"],
        "client_failures": counters["failed"],
        "searchable_p50_s": percentile(lat, 0.5),
        "searchable_p95_s": percentile(lat, 0.95),
        "peak_app_memory_mb": round(max(peaks) / 1e6, 1) if peaks else None,
    }
    out_dir = Path(__file__).parent / "results"
    out_dir.mkdir(exist_ok=True)
    (out_dir / "load_test.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    for key, value in result.items():
        print(f"{key:28} {value if value is not None else 'n/a'}")
    ok = lost_batches == 0 and result["lines_missing"] == 0 and not_searchable == 0
    print("RESULT:", "PASS (0 accepted-then-lost)" if ok else "FAIL")
    return 0 if ok else 1


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("--url", default="http://localhost:8000")
    p.add_argument("--dsn", default=DEFAULT_DSN)
    p.add_argument("--container", default="failurelens-app-1")
    p.add_argument("--rate", type=float, default=1000.0, help="lines per second")
    p.add_argument("--duration", type=float, default=60.0, help="seconds")
    p.add_argument("--lines-per-batch", type=int, default=100)
    p.add_argument("--concurrency", type=int, default=50)
    p.add_argument("--drain-timeout", type=float, default=90.0)
    args = p.parse_args()
    if args.rate <= 0 or args.duration <= 0 or args.lines_per_batch <= 0:
        p.error("rate, duration and lines-per-batch must be positive")
    return asyncio.run(run(args))


if __name__ == "__main__":
    sys.exit(main())
