from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from eval.cases import CaseError, check_fixtures, load_cases


def answerable(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": "hdfs-block-missing",
        "source": "loghub-hdfs",
        "file": "loghub/hdfs/sample.log",
        "query": "blk_0000000001 could not be replicated",
        "expected_root_cause": "datanode unreachable during replication",
        "evidence_patterns": ["blk_0000000001"],
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


def test_loads_answerable_and_inconclusive(tmp_path: Path) -> None:
    cases = load_cases(write_cases(tmp_path, answerable(), inconclusive()))
    assert [c.id for c in cases] == ["hdfs-block-missing", "no-evidence-1"]
    assert cases[0].expect_inconclusive is False
    assert cases[1].expect_inconclusive is True
    assert cases[1].file is None


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
        (answerable(expected_root_cause=None), "answerable cases need expected_root_cause"),
        (answerable(expected_root_cause="  "), "answerable cases need expected_root_cause"),
        (answerable(evidence_patterns=[]), "at least one evidence pattern"),
        (answerable(evidence_patterns=["ERROR"]), "evidence pattern too short"),
        (answerable(evidence_patterns=["      "]), "evidence pattern too short"),
        (answerable(source="inconclusive"), "must go together"),
        (answerable(expect_inconclusive=True), "must go together"),
        (inconclusive(expected_root_cause="x"), "must not set expected_root_cause"),
        (inconclusive(evidence_patterns=["blk_0000000001"]), "must not set evidence_patterns"),
        (inconclusive(source="replayed"), "must go together"),
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


def test_check_fixtures_passes_when_file_and_patterns_exist(tmp_path: Path) -> None:
    (tmp_path / "loghub" / "hdfs").mkdir(parents=True)
    (tmp_path / "loghub" / "hdfs" / "sample.log").write_text(
        "INFO x\nWARN blk_0000000001 failed\n", encoding="utf-8"
    )
    cases = load_cases(write_cases(tmp_path, answerable(), inconclusive()))
    assert check_fixtures(cases, tmp_path) == []


def test_check_fixtures_reports_missing_file(tmp_path: Path) -> None:
    cases = load_cases(write_cases(tmp_path, answerable()))
    problems = check_fixtures(cases, tmp_path)
    assert problems == ["hdfs-block-missing: file not found: loghub/hdfs/sample.log"]


def test_check_fixtures_reports_missing_pattern(tmp_path: Path) -> None:
    (tmp_path / "loghub" / "hdfs").mkdir(parents=True)
    (tmp_path / "loghub" / "hdfs" / "sample.log").write_text("INFO nothing here\n", "utf-8")
    cases = load_cases(write_cases(tmp_path, answerable()))
    problems = check_fixtures(cases, tmp_path)
    assert len(problems) == 1
    assert "pattern not in loghub/hdfs/sample.log" in problems[0]
