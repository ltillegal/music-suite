# Reviewing an upstream sync

The author ships quickly and parts of the code are generated. A merge can pass
the test suite and still be wrong, so every sync gets two passes: the automated
one, and a short manual one against the list below.

## 1. Automated pass

```bash
./scripts/sync_upstream.sh        # merges into sync/upstream-YYYYMMDD
python3 scripts/qa_audit.py --base main
ruff check .
python3 -m pytest tests -q
```

`qa_audit.py` looks for the failures that tests do not catch:

- **merge conflict markers** left in any file;
- **duplicate top-level definitions** — this already happened once: both sides
  added `models_dir()` to `settings.py`, the auto-merge kept both, and Python
  silently used the last one;
- risky calls (`shell=True`, `os.system`, `eval`, `exec`, `pickle`, bare `yaml.load`);
- guardrail breaches (CORS wildcard, binding to `0.0.0.0`);
- **translation keys** present in `en` but missing in `ru` (and the reverse);
- count of skipped tests and new `TODO`/`FIXME` markers compared to `main`.

Anything printed as `BLOCK` must be fixed before merging. `WARN` needs a look,
`INFO` is context.

The test count matters as a number: compare it with `main` and with a clean
`upstream/main` run. Five environmental failures are normal on this machine
(memory guard, WSL-only tests); a *sixth* failure is a new break.

## 2. Manual pass

Read the diff by area, not line by line. The areas that have broken before:

1. **`audioqi/generation/settings.py`, `engine.py`, `setup.py`** — where the
   external models root and the author's managed runtimes meet. Check that
   `models_dir()` has exactly one definition and that the fork's branch is still
   inside it.
2. **API and uploads** (`apps/api/main.py`, `audioqi/editor/`): new endpoints,
   changed limits, anything that weakens `GUARDRAILS.md`. The guardrails file is
   the specification, not the code.
3. **Frontend** (`apps/web-next`): new `window.open`, `dangerouslySetInnerHTML`,
   `localStorage`/cookies holding user data, and client/server rendering that
   disagree so the first frame flashes.
4. **Tests**: a test rewritten to pass (literal assertions loosened, `skip`
   added, `assert True`) is worse than no test. Compare the assertions, not
   just the green result.
5. **Docs against code**: the author's README and USER_MANUAL describe behaviour
   that may not exist yet. Verify claims by reading the implementation — the
   same way the VU ballistics claim was caught in LudensScope.
6. **Dependencies**: new packages or version bumps in `pyproject.toml` and
   `package.json`, and whether their licences are compatible with MIT.

## 3. Definition of done

A sync is finished when:

- `qa_audit.py` reports 0 blocking findings;
- the test suite fails no more than on `upstream/main` itself in the same
  environment, and the failure reason is understood;
- `ruff check` counts are not worse than before the merge;
- the PR description lists every finding that was fixed and every one left;
- `./scripts/build_macos_app.sh` still produces a DMG (the CI job
  `macOS app` is the cheapest way to confirm it) and the app opens.

## 4. After the merge

Bump the release only when the DMG builds: the release asset is the thing users
download, and a tag without a working bundle is worse than no tag.
