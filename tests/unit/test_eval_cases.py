from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from eval.cases import CaseError, check_fixtures, load_cases


def answerable(**over: Any) -> dict[str, Any]:
    """A fully scored case from your own services."""
    base: dict[str, Any] = {
        "id": "api-db-refused",
        "source": "replayed",
        "file": "replayed/sample.log",
        "query": "api returns 500 and logs say connection refused to the database",
        "expected_root_cause": "database container was stopped",
        "evidence_patterns": ["connection refused"],
    }
    base.update(over)
    return base


def retrieval_case(**over: Any) -> dict[str, Any]:
    """A retrieval-only Loghub case: no root-cause label."""
    base: dict[str, Any] = {
        "id": "hdfs-serve-exception",
        "source": "loghub-hdfs",
        "file": "loghub/hdfs/sample.log",
        "query": "datanode throws exceptions when serving block reads to clients",
        "scoring": "retrieval",
        "evidence_patterns": ["Got exception while serving"],
    }
    base.update(over)
    return base


def inconclusive(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": "no-evidence-1",
        "source": "inconclusive",
        "query": "the payment service feels slow today",
        "expect_inconclusive": True,
    }
    base.update(over)
    return base


def write_cases(tmp_path: Path, *rows: dict[str, Any] | str) -> Path:
    path = tmp_path / "cases.jsonl"
    lines = [r if isinstance(r, str) else json.dumps(r) for r in rows]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def test_loads_all_three_kinds(tmp_path: Path) -> None:
    cases = load_cases(write_cases(tmp_path, answerable(), retrieval_case(), inconclusive()))
    assert [c.id for c in cases] == ["api-db-refused", "hdfs-serve-exception", "no-evidence-1"]
    assert cases[0].scoring == "full"
    assert cases[0].expected_root_cause is not None
    assert cases[1].scoring == "retrieval"
    assert cases[1].expected_root_cause is None
    assert cases[2].expect_inconclusive is True
    assert cases[2].scoring == "full"
    assert cases[2].file is None


def test_replayed_case_may_be_retrieval_only(tmp_path: Path) -> None:
    row = answerable(scoring="retrieval", expected_root_cause=None)
    assert load_cases(write_cases(tmp_path, row))[0].scoring == "retrieval"


def test_blank_lines_and_bom_are_tolerated(tmp_path: Path) -> None:
    path = tmp_path / "cases.jsonl"
    body = "\n" + json.dumps(answerable()) + "\n\n" + json.dumps(inconclusive()) + "\n"
    path.write_bytes(b"\xef\xbb\xbf" + body.encode("utf-8"))
    assert len(load_cases(path)) == 2


@pytest.mark.parametrize(
    ("row", "fragment"),
    [
        (answerable(extra_field=1), "extra_field"),
        (answerable(id="Bad Id"), "id must be lowercase"),
        (answerable(query="   "), "query must not be blank"),
        (answerable(file="/etc/passwd"), "relative forward-slash path"),
        (answerable(file="../secret.log"), "relative forward-slash path"),
        (answerable(file="a\\b.log"), "relative forward-slash path"),
        (answerable(file="C:/x.log"), "relative forward-slash path"),
        (answerable(file=None), "answerable cases need a file"),
        (answerable(expected_root_cause=None), "full cases need expected_root_cause"),
        (answerable(expected_root_cause="  "), "full cases need expected_root_cause"),
        (answerable(evidence_patterns=[]), "at least one evidence pattern"),
        (answerable(evidence_patterns=["ERROR"]), "evidence pattern too short"),
        (answerable(evidence_patterns=["      "]), "evidence pattern too short"),
        (answerable(source="inconclusive"), "must go together"),
        (answerable(expect_inconclusive=True), "must go together"),
        (answerable(scoring="bogus"), "scoring: Input should be"),
        (inconclusive(expected_root_cause="x"), "must not set expected_root_cause"),
        (inconclusive(evidence_patterns=["connection refused"]), "must not set evidence_patterns"),
        (inconclusive(source="replayed"), "must go together"),
        (inconclusive(scoring="retrieval"), "scoring must be 'full'"),
        (retrieval_case(expected_root_cause="made up"), "must not set expected_root_cause"),
        (retrieval_case(expected_root_cause=""), "must not set expected_root_cause"),
        (retrieval_case(evidence_patterns=[]), "at least one evidence pattern"),
        (retrieval_case(file=None), "answerable cases need a file"),
        (
            retrieval_case(scoring="full", expected_root_cause="made up"),
            "has no root-cause labels",
        ),
        (retrieval_case(scoring="full"), "has no root-cause labels"),
        (
            retrieval_case(source="loghub-openstack", scoring="full"),
            "loghub-openstack has no root-cause labels",
        ),
    ],
)
def test_invalid_case_is_rejected(tmp_path: Path, row: dict[str, Any], fragment: str) -> None:
    with pytest.raises(CaseError) as exc:
        load_cases(write_cases(tmp_path, row))
    assert fragment in str(exc.value)
    assert "line 1" in str(exc.value)


def test_duplicate_id_reports_both_lines(tmp_path: Path) -> None:
    path = write_cases(tmp_path, answerable(), answerable())
    with pytest.raises(CaseError) as exc:
        load_cases(path)
    assert "line 2" in str(exc.value)
    assert "first on line 1" in str(exc.value)


def test_invalid_json_reports_line_number(tmp_path: Path) -> None:
    with pytest.raises(CaseError) as exc:
        load_cases(write_cases(tmp_path, answerable(), "{not json"))
    assert "line 2: invalid JSON" in str(exc.value)


def test_non_object_line_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(CaseError) as exc:
        load_cases(write_cases(tmp_path, "[1, 2]"))
    assert "line 1" in str(exc.value)


def test_all_problems_are_reported_together(tmp_path: Path) -> None:
    path = write_cases(
        tmp_path,
        answerable(id="a-1", evidence_patterns=[]),
        "{broken",
        answerable(id="a-3", file=None),
    )
    with pytest.raises(CaseError) as exc:
        load_cases(path)
    message = str(exc.value)
    assert "line 1" in message
    assert "line 2" in message
    assert "line 3" in message


def _write_fixture(root: Path, rel: str, content: str) -> None:
    target = root / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")


def test_check_fixtures_passes_when_files_and_patterns_exist(tmp_path: Path) -> None:
    _write_fixture(tmp_path, "replayed/sample.log", "ERROR connection refused to db:5432\n")
    _write_fixture(
        tmp_path, "loghub/hdfs/sample.log", "WARN x: Got exception while serving blk_1 to /h\n"
    )
    cases = load_cases(write_cases(tmp_path, answerable(), retrieval_case(), inconclusive()))
    assert check_fixtures(cases, tmp_path) == []


def test_check_fixtures_reports_missing_file(tmp_path: Path) -> None:
    cases = load_cases(write_cases(tmp_path, answerable()))
    assert check_fixtures(cases, tmp_path) == [
        "api-db-refused: file not found: replayed/sample.log"
    ]


def test_check_fixtures_reports_missing_pattern(tmp_path: Path) -> None:
    _write_fixture(tmp_path, "replayed/sample.log", "INFO nothing here\n")
    cases = load_cases(write_cases(tmp_path, answerable()))
    problems = check_fixtures(cases, tmp_path)
    assert len(problems) == 1
    assert "pattern not in replayed/sample.log" in problems[0]
