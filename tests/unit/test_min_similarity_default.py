from __future__ import annotations

from app.core.config import Settings


def test_min_similarity_default_is_tuned_for_local_model() -> None:
    assert Settings(_env_file=None).min_similarity == 0.65


def test_min_similarity_can_be_set_from_environment(monkeypatch) -> None:
    monkeypatch.setenv("MIN_SIMILARITY", "0.7")
    assert Settings(_env_file=None).min_similarity == 0.7
