"""Optional external root for the models Music Suite downloads.

By default models live beside the rest of the data (``<data_dir>/models``). Set
``MUSIC_SUITE_MODELS_DIR``, or save a root with ``python -m audioqi.models_root
move /Volumes/Drive/MusicSuiteModels``, and all downloaded weights live there
instead. The choice survives restarts in ``<data_dir>/models-root.json``.

Usage from a terminal::

    python -m audioqi.models_root status
    python -m audioqi.models_root move /Volumes/Drive/MusicSuiteModels
    python -m audioqi.models_root copy /Volumes/Drive/MusicSuiteModels
    python -m audioqi.models_root restore
    python -m audioqi.models_root shell-env
"""

from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import json
import os
import shlex
import shutil
import sys
from pathlib import Path
from typing import Any

from audioqi.editor import projects

ENV_ROOT = "MUSIC_SUITE_MODELS_DIR"
ENV_CONFIG = "MUSIC_SUITE_MODELS_CONFIG"
CONFIG_NAME = "models-root.json"
SPLIT_DIR = "audio-separator"
ENGINE_DIR = "acestep.cpp"
HF_DIR = "huggingface"
COMFY_DIR = "comfyui"
MIN_FREE_BYTES = 1024**3


def config_path() -> Path:
    """The small JSON file that remembers a chosen root (outside it, so it survives moves)."""
    override = os.environ.get(ENV_CONFIG, "").strip()
    if override:
        return Path(override).expanduser().resolve()
    return projects.SETTINGS.data_dir.resolve() / CONFIG_NAME


def default_root() -> Path:
    return projects.SETTINGS.data_dir.resolve() / "models"


def configured_root() -> Path | None:
    """An explicit root from the environment or the saved config, if either is set."""
    raw = os.environ.get(ENV_ROOT, "").strip()
    if not raw:
        stored = projects.read_json_safe(config_path()) or {}
        raw = str(stored.get("root") or "").strip()
    if not raw:
        return None
    path = Path(raw).expanduser()
    return path.resolve() if path.is_absolute() else None


def root_source() -> str:
    if os.environ.get(ENV_ROOT, "").strip():
        return "environment"
    return "config" if (projects.read_json_safe(config_path()) or {}).get("root") else "default"


def models_root() -> Path:
    return configured_root() or default_root()


def separator_dir() -> Path:
    return models_root() / SPLIT_DIR


def engine_models_dir() -> Path:
    return models_root() / ENGINE_DIR / "models"


def hf_cache_dir() -> Path:
    return models_root() / HF_DIR


def comfyui_checkpoints_dir() -> Path:
    return models_root() / COMFY_DIR / "checkpoints"


def _dir_size(path: Path) -> int:
    if not path.is_dir():
        return 0
    return sum(item.stat().st_size for item in path.rglob("*") if item.is_file())


def _disk_free(path: Path) -> int:
    candidate = path if path.is_dir() else path.parent
    while not candidate.is_dir() and candidate != candidate.parent:
        candidate = candidate.parent
    return shutil.disk_usage(candidate).free


def _local_pairs() -> list[tuple[str, Path]]:
    """Model folders in their current, on-disk locations."""
    from audioqi.generation import settings as generation_settings

    return [
        (SPLIT_DIR, separator_dir()),
        (ENGINE_DIR, generation_settings.models_dir()),
    ]


def status() -> dict[str, Any]:
    """A compact report for ``models_root status`` and for support requests."""
    split, engine = (path for _, path in _local_pairs())
    external = configured_root()
    return {
        "root": str(models_root()),
        "source": root_source(),
        "external": external is not None,
        "default_root": str(default_root()),
        "config": str(config_path()),
        "separator_dir": str(separator_dir()),
        "engine_models_dir": str(engine_models_dir()),
        "hf_cache_dir": str(hf_cache_dir()),
        "comfyui_checkpoints_dir": str(comfyui_checkpoints_dir()),
        "separator_bytes": _dir_size(split),
        "engine_models_bytes": _dir_size(engine),
        "total_bytes": _dir_size(split) + _dir_size(engine),
        "free_bytes": _disk_free(models_root()),
        "api_environment": _env_values(),
    }


def _env_values() -> dict[str, str]:
    """Cache variables that should follow an external root; explicit values win."""
    if configured_root() is None:
        return {}
    wanted = {
        "HF_HOME": str(hf_cache_dir()),
        "HUGGINGFACE_HUB_CACHE": str(hf_cache_dir() / "hub"),
        "COMFYUI_CHECKPOINTS_DIR": str(comfyui_checkpoints_dir()),
    }
    return {key: value for key, value in wanted.items() if not os.environ.get(key, "").strip()}


def apply_environment() -> None:
    """Point Hugging Face and ComfyUI downloads at the external root as well."""
    for key, value in _env_values().items():
        os.environ.setdefault(key, value)


def shell_env() -> str:
    """Shell fragment for ``start.command``; quoted for ``eval``."""
    return "".join(f"export {key}={shlex.quote(value)}\n" for key, value in _env_values().items())


def _write_config(root: Path, original: dict[str, str]) -> None:
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    projects.write_json_atomic(
        path,
        {
            "root": str(root),
            "moved_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            "original": original,
        },
    )
    with contextlib.suppress(OSError):
        path.chmod(0o600)


def clear_config() -> None:
    config_path().unlink(missing_ok=True)


def _pair_target(root: Path, name: str) -> Path:
    if name == SPLIT_DIR:
        return root / SPLIT_DIR
    if name in (ENGINE_DIR, "engine_models"):
        return root / ENGINE_DIR / "models"
    if name in (HF_DIR, "huggingface-cache"):
        return root / HF_DIR
    return root / name


def _check_destination(destination: Path, pairs: list[tuple[str, Path]]) -> None:
    if destination.exists() and not destination.is_dir():
        raise ValueError(f"{destination} exists and is not a folder.")
    try:
        destination.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise ValueError(f"Cannot create {destination}: {exc}") from exc
    probe = destination / f".music-suite-write-{os.getpid()}"
    try:
        probe.write_bytes(b"ok")
        probe.unlink()
    except OSError as exc:
        raise ValueError(f"{destination} is not writable: {exc}") from exc
    for name, source in pairs:
        target = _pair_target(destination, name)
        if source.is_dir() and (target == source or target.is_relative_to(source)):
            raise ValueError("The destination must be outside the current model folders.")
        if target.exists() and any(target.iterdir()):
            raise ValueError(f"{target} is not empty. Move it aside before trying again.")
    needed = sum(_dir_size(source) for _, source in pairs) + MIN_FREE_BYTES
    if _disk_free(destination) < needed:
        raise ValueError(
            f"Not enough free space on {destination}: need about {needed / 1024**3:.1f} GB."
        )


def migrate(destination: Path, *, move: bool, hf_cache: bool = False) -> dict[str, Any]:
    """Copy (and optionally remove) the local model folders into ``destination``."""
    destination = destination.expanduser().resolve()
    if not destination.is_absolute():
        raise ValueError("The destination must be an absolute path.")
    pairs = list(_local_pairs())
    if hf_cache:
        source = Path(os.environ.get("HF_HOME") or Path.home() / ".cache" / "huggingface")
        pairs.append(("huggingface-cache", source))
    _check_destination(destination, pairs)
    copied: dict[str, int] = {}
    for name, source in pairs:
        target = _pair_target(destination, name)
        size = _dir_size(source)
        if not source.is_dir() or size == 0:
            continue
        shutil.copytree(source, target, dirs_exist_ok=True)
        copied[name] = size
        if move:
            shutil.rmtree(source)
    stored = projects.read_json_safe(config_path()) or {}
    previous = stored.get("original")
    original = (
        {
            name: path
            for name, path in previous.items()
            if isinstance(name, str)
            and isinstance(path, str)
            and Path(path).is_absolute()
        }
        if isinstance(previous, dict) and previous
        else {name: str(source) for name, source in pairs}
    )
    if move:
        _write_config(destination, original)
    return {
        "root": str(destination),
        "mode": "moved" if move else "copied",
        "bytes": sum(copied.values()),
        "folders": sorted(copied),
    }


def restore(*, move: bool = True) -> dict[str, Any]:
    """Send models back to their original folders and forget the external root."""
    if os.environ.get(ENV_ROOT, "").strip():
        raise ValueError(f"Unset {ENV_ROOT} before restoring the saved root.")
    stored = projects.read_json_safe(config_path()) or {}
    root_raw = str(stored.get("root") or "").strip()
    if not root_raw:
        raise ValueError("No external models root has been saved.")
    root = Path(root_raw).expanduser().resolve()
    if not root.is_dir():
        raise ValueError(f"The external root is not available: {root}")
    pairs = [
        (name, Path(raw))
        for name, raw in (stored.get("original") or {}).items()
        if isinstance(raw, str) and Path(raw).is_absolute()
    ]
    if not pairs:
        pairs = _local_pairs()
    for _, source in pairs:
        try:
            source.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise ValueError(f"Cannot create {source.parent}: {exc}") from exc
        if _disk_free(source.parent) < _dir_size(source) + MIN_FREE_BYTES:
            raise ValueError(
                f"Not enough free space next to {source.parent} to restore the models."
            )
    freed = 0
    for name, source in pairs:
        target = _pair_target(root, name)
        if not target.is_dir():
            continue
        copied = _dir_size(target)
        shutil.copytree(target, source, dirs_exist_ok=True)
        if _dir_size(source) < copied:
            raise ValueError(f"The restore into {source} is incomplete; the external copy is kept.")
        freed += copied
        if move:
            shutil.rmtree(target)
    if move:
        clear_config()
    return {
        "root": str(root),
        "mode": "restored" if move else "copied back",
        "bytes": freed,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m audioqi.models_root", description=__doc__.split("\n")[0]
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("status", help="show the active root, sizes and free space")
    sub.add_parser("shell-env", help="print shell exports used by start.command")
    for command in ("move", "copy"):
        item = sub.add_parser(command, help=f"{command} models to another disk")
        item.add_argument("destination", help="absolute path to the new models folder")
        item.add_argument(
            "--hf-cache", action="store_true", help=f"also {command} the Hugging Face cache"
        )
    sub.add_parser("restore", help="move models back to their original folders")
    args = parser.parse_args(argv)
    try:
        if args.command == "status":
            print(json.dumps(status(), indent=2))
        elif args.command == "shell-env":
            sys.stdout.write(shell_env())
        elif args.command in ("move", "copy"):
            result = migrate(
                Path(args.destination), move=args.command == "move", hf_cache=args.hf_cache
            )
            print(json.dumps(result, indent=2))
        elif args.command == "restore":
            print(json.dumps(restore(), indent=2))
    except (ValueError, OSError, shutil.Error) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
