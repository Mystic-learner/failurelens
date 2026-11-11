from __future__ import annotations

from pathlib import Path

import pytest

from app.api.health import embedding_configured
from app.core.config import Settings
from app.services import embeddings
from app.services.embeddings import local_provider_available, make_provider

MODEL_DIR = Path(__file__).resolve().parents[2] / "models" / "bge-small-en-v1.5"
has_model = (MODEL_DIR / "model.onnx").is_file() and (MODEL_DIR / "tokenizer.json").is_file()


def local_settings(model_dir: Path) -> Settings:
    return Settings(_env_file=None, embedding_provider="local", local_model_dir=str(model_dir))


def test_local_is_unavailable_without_model_files(tmp_path: Path) -> None:
    s = local_settings(tmp_path)
    assert local_provider_available(s) is False
    assert make_provider(s) is None
    assert embedding_configured(s) is False


def test_local_is_unavailable_without_packages(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "model.onnx").write_bytes(b"x")
    (tmp_path / "tokenizer.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(embeddings, "find_spec", lambda name: None)
    s = local_settings(tmp_path)
    assert local_provider_available(s) is False
    assert make_provider(s) is None
    assert embedding_configured(s) is False


@pytest.mark.skipif(not has_model, reason="model files not downloaded")
def test_local_provider_is_built_when_everything_is_present() -> None:
    pytest.importorskip("app.services.embeddings_local")
    s = local_settings(MODEL_DIR)
    assert local_provider_available(s) is True
    assert embedding_configured(s) is True
    provider = make_provider(s)
    assert provider is not None
    assert provider.name == "local"
    assert provider.table == "chunk_embeddings_local"


def test_default_model_dir_is_the_repo_models_folder() -> None:
    assert Path(Settings(_env_file=None).local_model_dir) == MODEL_DIR
