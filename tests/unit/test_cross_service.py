from app.services.evidence_context import (
    CROSS_LIMIT,
    mentions_service,
    pick_cross_evidence,
    related_services,
)

CAUSE = "2026-11-14 10:00:01.100000 INFO redis-2 evicted all keys, rebuilding from database"


def test_error_naming_a_dependency_selects_that_service() -> None:
    err = "inventory lookup failed: 503 from upstream"
    assert mentions_service(err, "inventory-api-6ed59")
    assert not mentions_service(err, "report-job-6ed59")


def test_generic_name_parts_never_match() -> None:
    assert not mentions_service("api worker service failed", "api-worker")


def test_same_service_instances_are_not_other_services() -> None:
    names = ["inventory-api-b2", "cart-api-c3", "report-job-d4"]
    got = related_services("inventory lookup failed", "cart-api-a1", names)
    assert got == ["inventory-api-b2"]


def test_pick_keeps_rare_cause_and_drops_routine_repeats() -> None:
    routine = "INFO GET /stock 200 in 9ms"
    rows = [
        (5, "inventory-api-x1", "INFO", f"2026-11-14 10:00:05.100000 {routine}"),
        (4, "inventory-api-x1", "INFO", f"2026-11-14 10:00:04.100000 {routine}"),
        (1, "inventory-api-x1", "INFO", CAUSE),
    ]
    got = pick_cross_evidence(rows)
    assert [c.chunk_id for c in got] == ["1"]
    assert got[0].text == f"SERVICE: inventory-api-x1\n{CAUSE}"


def test_pick_keeps_repeated_errors_once() -> None:
    line = "2026-11-14 10:00:0{n}.100000 ERROR redis connection refused"
    rows = [(n, "inventory-api-x1", "ERROR", line.format(n=n)) for n in (3, 2, 1)]
    got = pick_cross_evidence(rows)
    assert [c.chunk_id for c in got] == ["3"]


def test_pick_collapses_same_cause_from_two_instances_and_caps() -> None:
    rows = [(9, "inventory-api-b2", "INFO", CAUSE), (2, "inventory-api-a1", "INFO", CAUSE)]
    got = pick_cross_evidence(rows)
    assert [c.chunk_id for c in got] == ["9"]
    assert got[0].text.startswith("SERVICE: inventory-api-b2\n")
    many = [
        (i, "inventory-api-x1", "ERROR", f"2026-11-14 10:00:00.1 ERROR failure mode {i}")
        for i in range(10)
    ]
    assert len(pick_cross_evidence(many)) == CROSS_LIMIT
