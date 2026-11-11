"""Eval cases: one JSON object per line (eval/fixtures/cases.jsonl).

Chunk ids change on every ingest, so ground truth is expressed as `evidence_patterns`:
substrings that must appear in the text of a retrieved chunk for it to count as a hit.
Patterns are matched against redacted chunk text, so they must not contain anything the
redactor rewrites (IP addresses, emails, tokens).

Scoring:
- "full": retrieval, root cause, evidence validity (needs expected_root_cause).
- "retrieval": hit-rate only. Used where no trustworthy root-cause label exists (Loghub).
- inconclusive cases are scored on abstention and always use "full".

Run `python eval/cases.py eval/fixtures/cases.jsonl` to validate a case file.
"""

from __future__ import annotations

import json
import re
import sys
from collections.abc import Sequence
from pathlib import Path, PurePosixPath
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

Source = Literal["loghub-hdfs", "loghub-openstack", "replayed", "inconclusive"]
Scoring = Literal["retrieval", "full"]

LABELLESS_SOURCES = frozenset({"loghub-hdfs", "loghub-openstack"})

_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
MIN_PATTERN_CHARS = 6  # shorter patterns match almost any chunk and inflate hit-rate


class CaseError(ValueError):
    """The case file is invalid. The message lists every problem found."""


class EvalCase(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    source: Source
    file: str | None = None  # path relative to eval/fixtures
    query: str = Field(min_length=1, max_length=4000)
    scoring: Scoring = "full"
    expected_root_cause: str | None = None
    evidence_patterns: list[str] = Field(default_factory=list)
    expect_inconclusive: bool = False
    notes: str = ""

    @field_validator("id")
    @classmethod
    def _check_id(cls, value: str) -> str:
        if not _ID.fullmatch(value):
            raise ValueError("id must be lowercase letters, digits, '-' or '_' (max 64)")
        return value

    @field_validator("query")
    @classmethod
    def _check_query(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("query must not be blank")
        return value

    @field_validator("file")
    @classmethod
    def _check_file(cls, value: str | None) -> str | None:
        if value is None:
            return None
        path = PurePosixPath(value)
        if path.is_absolute() or ".." in path.parts or "\\" in value or ":" in value:
            raise ValueError("file must be a relative forward-slash path without '..'")
        return value

    @field_validator("evidence_patterns")
    @classmethod
    def _check_patterns(cls, value: list[str]) -> list[str]:
        for pattern in value:
            if len(pattern.strip()) < MIN_PATTERN_CHARS:
                raise ValueError(f"evidence pattern too short (min {MIN_PATTERN_CHARS} chars)")
        return value

    @model_validator(mode="after")
    def _check_shape(self) -> Self:
        if (self.source == "inconclusive") != self.expect_inconclusive:
            raise ValueError("source 'inconclusive' and expect_inconclusive must go together")
        if self.expect_inconclusive:
            if self.expected_root_cause is not None:
                raise ValueError("inconclusive cases must not set expected_root_cause")
            if self.evidence_patterns:
                raise ValueError("inconclusive cases must not set evidence_patterns")
            if self.scoring != "full":
                raise ValueError(
                    "inconclusive cases are scored on abstention: scoring must be 'full'"
                )
            return self
        if self.source in LABELLESS_SOURCES and self.scoring != "retrieval":
            raise ValueError(f"{self.source} has no root-cause labels: scoring must be 'retrieval'")
        if not self.evidence_patterns:
            raise ValueError("answerable cases need at least one evidence pattern")
        if self.file is None:
            raise ValueError("answerable cases need a file")
        if self.scoring == "retrieval":
            if self.expected_root_cause is not None:
                raise ValueError("retrieval cases must not set expected_root_cause")
        elif not (self.expected_root_cause or "").strip():
            raise ValueError("full cases need expected_root_cause")
        return self


def load_cases(path: Path) -> list[EvalCase]:
    """Parse a JSONL case file. Blank lines are skipped; all problems are reported together."""
    cases: list[EvalCase] = []
    problems: list[str] = []
    seen: dict[str, int] = {}
    text = path.read_text(encoding="utf-8-sig")
    for lineno, raw in enumerate(text.splitlines(), start=1):
        if not raw.strip():
            continue
        try:
            case = EvalCase.model_validate(json.loads(raw))
        except json.JSONDecodeError as exc:
            problems.append(f"line {lineno}: invalid JSON ({exc.msg})")
            continue
        except ValidationError as exc:
            for err in exc.errors():
                where = ".".join(str(p) for p in err["loc"]) or "case"
                problems.append(f"line {lineno}: {where}: {err['msg']}")
            continue
        if case.id in seen:
            first = seen[case.id]
            problems.append(f"line {lineno}: duplicate id {case.id!r} (first on line {first})")
            continue
        seen[case.id] = lineno
        cases.append(case)
    if problems:
        raise CaseError("\n".join(problems))
    return cases


def check_fixtures(cases: Sequence[EvalCase], root: Path) -> list[str]:
    """Each case file must exist and contain every evidence pattern in its raw text.

    Necessary, not sufficient: redaction may still rewrite a pattern at ingest time.
    """
    problems: list[str] = []
    contents: dict[str, str | None] = {}
    for case in cases:
        if case.file is None:
            continue
        if case.file not in contents:
            target = root / case.file
            contents[case.file] = (
                target.read_text(encoding="utf-8", errors="replace") if target.is_file() else None
            )
        content = contents[case.file]
        if content is None:
            problems.append(f"{case.id}: file not found: {case.file}")
            continue
        for pattern in case.evidence_patterns:
            if pattern not in content:
                problems.append(f"{case.id}: pattern not in {case.file}: {pattern!r}")
    return problems


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: python eval/cases.py <cases.jsonl>")
        return 2
    path = Path(argv[1])
    try:
        cases = load_cases(path)
    except CaseError as exc:
        print(exc)
        return 1
    problems = check_fixtures(cases, path.parent)
    for problem in problems:
        print(problem)
    inconclusive = sum(1 for c in cases if c.expect_inconclusive)
    retrieval = sum(1 for c in cases if c.scoring == "retrieval")
    full = len(cases) - inconclusive - retrieval
    print(
        f"{len(cases)} cases ({full} full, {retrieval} retrieval-only, {inconclusive} inconclusive)"
    )
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
