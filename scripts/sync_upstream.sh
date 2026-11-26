#!/usr/bin/env bash
set -euo pipefail

# Pull the author's changes into this fork.
#
#   ./scripts/sync_upstream.sh            # merge into a local branch and test
#   ./scripts/sync_upstream.sh --push     # also push and open a pull request
#
# The merge lands on a throwaway branch, never directly on main, so a conflict
# can be resolved (or the attempt dropped) without touching a working branch.

UPSTREAM_URL="${MUSIC_SUITE_UPSTREAM_URL:-https://github.com/GeekatplayStudio/music-suite.git}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

PUSH=0
[[ "${1:-}" == "--push" ]] && PUSH=1

if [[ -n "$(git status --porcelain)" ]]; then
  echo "Working tree is not clean. Commit or stash first." >&2
  exit 1
fi

if ! git remote get-url upstream >/dev/null 2>&1; then
  git remote add upstream "$UPSTREAM_URL"
fi

echo "Fetching upstream..."
git fetch --quiet upstream main

if git merge-base --is-ancestor upstream/main HEAD; then
  echo "Already up to date with upstream/main."
  exit 0
fi

BRANCH="sync/upstream-$(date +%Y%m%d)"
echo "Upstream is ahead; merging into $BRANCH"
git checkout -q -B "$BRANCH"

if ! git -c user.name="$(git config user.name)" -c user.email="$(git config user.email)" \
       merge --no-edit upstream/main; then
  echo
  echo "Merge conflicts:" >&2
  git diff --name-only --diff-filter=U >&2
  cat >&2 <<'MSG'

Resolve them by hand, then:
  git add -A && git commit
  git checkout main

Pay attention to audioqi/generation/settings.py: both sides have edited
models_dir() before, and a clean auto-merge once left two definitions.
MSG
  exit 2
fi

echo "Running the structural audit..."
if ! python3 scripts/qa_audit.py --base main; then
  echo "The audit found blocking problems; the branch is left for inspection." >&2
  exit 4
fi

if command -v python3 >/dev/null 2>&1 && python3 -c "import pytest" 2>/dev/null; then
  echo "Running the test suite..."
  python3 -m pytest tests -q || {
    echo "Tests failed on the merged tree; the branch is left for inspection." >&2
    exit 3
  }
else
  echo "pytest is not installed in this python3; skipping the test run."
fi

if [[ "$PUSH" == "1" ]]; then
  git push -u origin "$BRANCH"
  if command -v gh >/dev/null 2>&1; then
    gh pr create --fill --title "Sync upstream/main" \
      --body "Automatic merge of the author's changes. Review the test run before merging."
  else
    echo "Push done; open the pull request in the browser."
  fi
fi

echo "Done. The merged branch is $BRANCH."
