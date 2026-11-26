"""Models can live on an external disk without changing the engine's own folder."""

from __future__ import annotations

import json
import os
from dataclasses import replace
from pathlib import Path

import pytest

from audioqi import models_root
from audioqi.editor import projects
from audioqi.generation import engine, settings


@pytest.fixture(autouse=True)
def isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setattr(projects, "SETTINGS", replace(projects.SETTINGS, data_dir=data))
    for name in (models_root.ENV_ROOT, models_root.ENV_CONFIG, "HF_HOME",
                 "HUGGINGFACE_HUB_CACHE", "COMFYUI_CHECKPOINTS_DIR", "MUSIC_SUITE_ACESTEP_DIR"):
        monkeypatch.delenv(name, raising=False)
    return data


def _seed(data: Path) -> tuple[Path, Path]:
    split = data / "models" / "audio-separator"
    weights = data / "engines" / "acestep.cpp" / "models"
    split.mkdir(parents=True)
    weights.mkdir(parents=True)
    (split / "htdemucs_ft.yaml").write_bytes(b"s" * 4096)
    (weights / "vae-BF16.gguf").write_bytes(b"g" * 8192)
    return split, weights


def test_default_paths_and_status(isolated: Path) -> None:
    split, weights = _seed(isolated)
    assert models_root.models_root() == isolated / "models"
    assert models_root.separator_dir() == split
    assert settings.models_dir() == weights
    report = models_root.status()
    assert report["source"] == "default" and report["external"] is False
    assert report["separator_dir"] == str(split)
    assert report["engine_models_dir"] == str(weights)
    assert report["separator_bytes"] == 4096 and report["engine_models_bytes"] == 8192
    assert engine.list_models()["vae"] == ["vae-BF16.gguf"]


def test_environment_override_wins_over_saved_config(isolated: Path, tmp_path: Path) -> None:
    external = tmp_path / "external"
    external.mkdir()
    models_root._write_config(external, {})
    assert models_root.configured_root() == external
    os.environ[models_root.ENV_ROOT] = str(tmp_path / "one-off")
    assert models_root.models_root() == tmp_path / "one-off"
    assert models_root.root_source() == "environment"
    del os.environ[models_root.ENV_ROOT]


def test_environment_and_cache_exports(isolated: Path, tmp_path: Path) -> None:
    external = tmp_path / "external"
    external.mkdir()
    os.environ[models_root.ENV_ROOT] = str(external)
    fragment = models_root.shell_env()
    assert "export HF_HOME=" in fragment
    assert "export COMFYUI_CHECKPOINTS_DIR=" in fragment
    models_root.apply_environment()
    assert os.environ["HF_HOME"] == str(external / "huggingface")
    assert os.environ["HUGGINGFACE_HUB_CACHE"] == str(external / "huggingface" / "hub")
    assert os.environ["COMFYUI_CHECKPOINTS_DIR"] == str(
        external / "comfyui" / "checkpoints"
    )
    assert models_root.shell_env() == ""  # already exported; nothing left to do


def test_explicit_cache_location_is_kept(isolated: Path, tmp_path: Path) -> None:
    external = tmp_path / "external"
    external.mkdir()
    os.environ[models_root.ENV_ROOT] = str(external)
    os.environ["HF_HOME"] = "/custom/cache"
    models_root.apply_environment()
    assert os.environ["HF_HOME"] == "/custom/cache"


def test_move_updates_every_reader_and_restore_brings_models_back(
    isolated: Path, tmp_path: Path
) -> None:
    split, weights = _seed(isolated)
    external = tmp_path / "external"
    result = models_root.migrate(external, move=True)
    assert result["folders"] == ["acestep.cpp", "audio-separator"]
    assert result["bytes"] == 4096 + 8192
    assert not split.exists() and not weights.exists()
    assert (external / "audio-separator" / "htdemucs_ft.yaml").is_file()
    assert models_root.models_root() == external
    assert models_root.separator_dir() == external / "audio-separator"
    assert settings.models_dir() == external / "acestep.cpp" / "models"
    assert engine.list_models()["vae"] == ["vae-BF16.gguf"]
    report = models_root.status()
    assert report["source"] == "config" and report["external"] is True
    assert json.loads(models_root.config_path().read_text())["root"] == str(external)

    restored = models_root.restore()
    assert restored["bytes"] == 4096 + 8192
    assert (split / "htdemucs_ft.yaml").is_file()
    assert (weights / "vae-BF16.gguf").is_file()
    assert not (external / "audio-separator").exists()
    assert models_root.models_root() == isolated / "models"
    assert not models_root.config_path().exists()


def test_copy_keeps_the_originals_and_move_refuses_a_nonempty_target(
    isolated: Path, tmp_path: Path
) -> None:
    split, weights = _seed(isolated)
    external = tmp_path / "external"
    result = models_root.migrate(external, move=False)
    assert result["mode"] == "copied"
    assert split.is_dir() and weights.is_dir()
    assert models_root.models_root() == isolated / "models"
    with pytest.raises(ValueError, match="not empty"):
        models_root.migrate(external, move=True)
    assert models_root.models_root() == isolated / "models"


def test_restore_refuses_an_environment_override(isolated: Path, tmp_path: Path) -> None:
    external = tmp_path / "external"
    models_root.migrate(external, move=True)
    os.environ[models_root.ENV_ROOT] = str(external)
    with pytest.raises(ValueError, match="Unset"):
        models_root.restore()
    assert models_root.config_path().is_file()
