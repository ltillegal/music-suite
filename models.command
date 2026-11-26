#!/usr/bin/env bash
set -euo pipefail

# Move/copy Music Suite's downloaded models to another disk, without opening the app.
# Examples: ./models.command status
#           ./models.command move /Volumes/SSD/MusicSuiteModels
#           ./models.command restore

ROOT="$(cd "$(dirname "$0")" && pwd)"
if [[ -n "${MUSIC_SUITE_STATE_DIR:-}" ]]; then
  STATE_DIR="$MUSIC_SUITE_STATE_DIR"
elif [[ "$ROOT" == *".app/Contents/Resources/suite" ]]; then
  STATE_DIR="$HOME/Library/Application Support/Music Suite"
else
  STATE_DIR="$ROOT"
fi
if [[ -z "${MUSIC_SUITE_MODELS_CONFIG:-}" ]]; then
  export MUSIC_SUITE_MODELS_CONFIG="$STATE_DIR/models-root.json"
fi
mkdir -p "$STATE_DIR"
cd "$ROOT"

if [[ ! -x .venv/bin/python ]]; then
  echo "Python environment is missing. Run ./install.command first." >&2
  exit 1
fi

exec .venv/bin/python -m audioqi.models_root "$@"
