"""Replay 5 scripted failures through the real ingest and diagnosis path, then score them.

Run:  python -m eval.scenario_run
Each run uses fresh service names, so it adds about 10 incidents and uses LLM tokens.
"""

from __future__ import annotations

import argparse
import statistics
import time
import uuid
from datetime import UTC, datetime

import httpx

SCENARIOS = [
    {
        "svc": "payments-api",
        "keywords": ["pool", "connection"],
        "info": ["POST /checkout 200 in 84ms", "GET /cart 200 in 31ms", "GET /prices 200 in 12ms"],
        "err": [
            "db.pool timeout waiting for a free connection, pool exhausted (50 of 50 in use)",
            "checkout failed to charge the card: no database connection available",
        ],
    },
    {
        "svc": "auth-service",
        "keywords": ["key"],
        "info": ["GET /health 200", "POST /login 200 in 120ms", "POST /refresh 200 in 45ms"],
        "err": [
            "token verification failed: signing key not found in key store",
            "login rejected: upstream key service unreachable",
        ],
    },
    {
        "svc": "orders-worker",
        "keywords": ["memory", "oom"],
        "info": ["picked up order batch", "order batch saved", "heartbeat ok"],
        "err": [
            "worker crashed while processing order batch: out of memory",
            "consumer lag keeps growing, queue is not draining",
        ],
    },
    {
        "svc": "search-index",
        "keywords": ["disk", "space", "storage"],
        "info": ["segment merged", "query served in 22ms", "snapshot started"],
        "err": [
            "write to index segment failed: no space left on device",
            "index node switched to read only mode",
        ],
    },
    {
        "svc": "notify-service",
        "keywords": ["smtp", "provider", "email"],
        "info": ["queued welcome email", "queued receipt email", "template cache refreshed"],
        "err": [
            "smtp provider request timed out after retries",
            "notification dropped: provider returned service unavailable",
        ],
    },
]


def stamp(offset_ms: int = 0) -> str:
    t = datetime.now(UTC).timestamp() + offset_ms / 1000
    return datetime.fromtimestamp(t, UTC).strftime("%Y-%m-%d %H:%M:%S.%f")


def post(client: httpx.Client, svc: str, src: str, seq: int, text: str) -> None:
    headers = {
        "X-Service": svc,
        "X-Source-Id": src,
        "Idempotency-Key": f"{src}-{seq}",
        "X-Sequence": str(seq),
    }
    for _ in range(10):
        r = client.post("/ingest/stream", headers=headers, content=text.encode())
        if r.status_code == 429:
            time.sleep(1)
            continue
        r.raise_for_status()
        return
    raise RuntimeError("server busy (429) too many times")


def replay(client: httpx.Client, svc: str, src: str, sc: dict) -> None:
    for seq in range(1, 7):
        lines = [f"{stamp(k * 40)} INFO {sc['info'][(seq + k) % 3]}" for k in range(3)]
        lines += [f"{stamp(150 + k * 40)} ERROR {m}" for k, m in enumerate(sc["err"])]
        post(client, svc, src, seq, "\n".join(lines) + "\n")
        time.sleep(0.5)


def verdict(item: dict, keywords: list[str]) -> tuple[str, str]:
    dg = item.get("diagnosis") or {}
    if dg.get("status") == "failed" or dg.get("error"):
        return "ERROR", ""
    res = dg.get("result") or {}
    root = str(res.get("root_cause") or "")
    if dg.get("status") == "inconclusive" or res.get("confidence") == "inconclusive":
        return "INCONCLUSIVE", root
    low = root.lower()
    return ("RIGHT" if any(k in low for k in keywords) else "WRONG"), root


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://localhost:8000")
    ap.add_argument("--timeout", type=int, default=180)
    args = ap.parse_args()
    tag = uuid.uuid4().hex[:5]
    expected = {f"{s['svc']}-{tag}": s for s in SCENARIOS}
    t0 = time.time()
    found: dict[int, tuple[dict, float]] = {}
    with httpx.Client(base_url=args.base, timeout=30) as client:
        for i, (svc, sc) in enumerate(expected.items()):
            replay(client, svc, f"eval-{tag}-{i}", sc)
        sent_s = time.time() - t0
        deadline = time.time() + args.timeout
        while time.time() < deadline:
            items = client.get("/incidents").json().get("items", [])
            seen = [it for it in items if it.get("service") in expected]
            for it in seen:
                if it.get("status") == "diagnosed" and it["id"] not in found:
                    found[it["id"]] = (it, time.time() - t0)
            if len(seen) >= 2 * len(expected) and len(found) >= len(seen):
                break
            time.sleep(2)
    rows = []
    for iid in sorted(found):
        it, secs = found[iid]
        v, root = verdict(it, expected[it["service"]]["keywords"])
        rows.append((iid, it["service"], v, secs, root))
        print(f"#{iid:<4} {it['service']:<26} {v:<12} {secs:5.0f}s  {root[:70]}")
    total = 2 * len(expected)
    n = {k: sum(1 for r in rows if r[2] == k) for k in ("RIGHT", "WRONG", "INCONCLUSIVE", "ERROR")}
    print()
    print(
        f"SCENARIOS: RIGHT {n['RIGHT']} of {total}, WRONG {n['WRONG']}, "
        f"INCONCLUSIVE {n['INCONCLUSIVE']}, ERROR {n['ERROR']}, "
        f"NOT DIAGNOSED IN TIME {total - len(rows)}"
    )
    if rows:
        times = [r[3] for r in rows]
        print(
            f"Seconds from replay start to diagnosis: median {statistics.median(times):.0f}, "
            f"max {max(times):.0f} (the replay itself takes {sent_s:.0f}s)"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
