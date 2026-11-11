from __future__ import annotations

from typing import Any

import pytest

from eval.retrieval_eval import MODES, first_hit_rank, random_baseline, summarize


def test_modes_are_vector_lexical_hybrid() -> None:
    assert MODES == {"vector": (True, False), "lexical": (False, True), "hybrid": (True, True)}


def test_first_hit_rank_is_one_based_and_first_match() -> None:
    texts = ["nothing", "has needle here", "needle again"]
    assert first_hit_rank(texts, ["needle"]) == 2


def test_first_hit_rank_accepts_any_pattern() -> None:
    assert first_hit_rank(["alpha", "beta"], ["zzz", "beta"]) == 2


def test_first_hit_rank_none_without_match_or_texts() -> None:
    assert first_hit_rank(["alpha", "beta"], ["gamma"]) is None
    assert first_hit_rank([], ["gamma"]) is None


def test_random_baseline_small_exact_case() -> None:
    assert random_baseline(4, 1, 2) == pytest.approx(0.5)


def test_random_baseline_matches_observed_corpus_counts() -> None:
    assert random_baseline(143, 13, 5) == pytest.approx(0.383, abs=0.005)
    assert random_baseline(318, 23, 5) == pytest.approx(0.315, abs=0.005)
    assert random_baseline(318, 1, 5) == pytest.approx(5 / 318)


def test_random_baseline_edges() -> None:
    assert random_baseline(100, 0, 5) == 0.0
    assert random_baseline(0, 0, 5) == 0.0
    assert random_baseline(10, 10, 5) == 1.0
    assert random_baseline(10, 6, 5) == 1.0  # fewer than k non-matching chunks
    assert random_baseline(3, 1, 5) == 1.0  # k larger than the corpus


def _row(ranks: dict[str, int | None], baseline: float) -> dict[str, Any]:
    return {
        "id": "x",
        "source": "loghub-hdfs",
        "matching_chunks": 1,
        "ranks": ranks,
        "baseline": baseline,
    }


def test_summarize_counts_hits_per_mode() -> None:
    rows = [
        _row({"vector": 1, "lexical": None, "hybrid": 1}, 0.2),
        _row({"vector": None, "lexical": None, "hybrid": 3}, 0.4),
    ]
    summary = summarize(rows)
    assert summary["cases"] == 2
    assert summary["modes"]["vector"]["hits"] == 1
    assert summary["modes"]["lexical"]["hits"] == 0
    assert summary["modes"]["hybrid"]["hits"] == 2
    assert summary["modes"]["hybrid"]["rate"] == 1.0
    assert summary["random_baseline"] == pytest.approx(0.3)


def test_summarize_empty_has_zero_rates() -> None:
    summary = summarize([])
    assert summary["cases"] == 0
    assert summary["random_baseline"] == 0.0
    assert all(m["rate"] == 0.0 for m in summary["modes"].values())
