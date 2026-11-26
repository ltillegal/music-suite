"""Structural checks for code coming from upstream.

The upstream project is written quickly and generated in parts, so a merge can
introduce things the test suite does not notice: a duplicated function that
silently shadows the fork's version, a conflict marker left in a file, a test
that got skipped, a CORS wildcard, or a translation file with missing keys.

Run before merging a sync:

    python3 scripts/qa_audit.py              # just look at the tree
    python3 scripts/qa_audit.py --base main  # compare against a branch

Exit code 1 means at least one blocking finding.
"""

from __future__ import annotations

import argparse
import ast
import collections
import pathlib
import re
import subprocess
import sys

BLOCK, WARN, INFO = "BLOCK", "WARN", "INFO"
SKIP_DIRS = {".git", "node_modules", ".venv", ".next", "dist", "build", "__pycache__"}
TEXT_SUFFIXES = {".py", ".ts", ".tsx", ".js", ".mjs", ".json", ".yml", ".yaml", ".sh", ".md"}

findings: list[tuple[str, str, str]] = []


def add(level: str, where: str, message: str) -> None:
    findings.append((level, where, message))


def files(suffixes: set[str]) -> list[pathlib.Path]:
    out = []
    for path in pathlib.Path(".").rglob("*"):
        if not path.is_file() or path.suffix not in suffixes:
            continue
        if SKIP_DIRS.intersection(path.parts):
            continue
        out.append(path)
    return sorted(out)


def check_conflict_markers() -> None:
    for path in files(TEXT_SUFFIXES):
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for number, line in enumerate(text.splitlines(), 1):
            if line.startswith(("<<<<<<< ", ">>>>>>> ")) or line.rstrip() == "=======":
                add(BLOCK, f"{path}:{number}", "merge conflict marker left in the file")


def check_duplicate_defs() -> None:
    for path in files({".py"}):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        names = [
            node.name
            for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        ]
        for name, count in collections.Counter(names).items():
            if count > 1:
                add(
                    BLOCK,
                    str(path),
                    f"{name!r} is defined {count} times; the last one silently wins",
                )


RISKY = [
    (re.compile(r"subprocess\.\w+\([^)]*shell\s*=\s*True"), "shell=True in subprocess"),
    (re.compile(r"\bos\.system\("), "os.system()"),
    (re.compile(r"(?<![.\w])eval\("), "eval()"),
    (re.compile(r"(?<![.\w])exec\("), "exec()"),
    (re.compile(r"pickle\.loads?\("), "pickle deserialisation"),
    (re.compile(r"yaml\.load\((?!.*Loader)"), "yaml.load without a Loader"),
]


def check_risky_calls() -> None:
    for path in files({".py"}):
        if "tests" in path.parts or path.name == "qa_audit.py":
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for number, line in enumerate(text.splitlines(), 1):
            for pattern, label in RISKY:
                if pattern.search(line):
                    add(WARN, f"{path}:{number}", label)


GUARDRAILS = [
    (re.compile(r"allow_origins\s*=\s*\[\s*[\"']\*"), "CORS wildcard"),
    (re.compile(r"host\s*=\s*[\"']0\.0\.0\.0[\"']"), "binds to 0.0.0.0"),
]


def check_guardrails() -> None:
    for path in files({".py"}):
        if "tests" in path.parts:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for number, line in enumerate(text.splitlines(), 1):
            for pattern, label in GUARDRAILS:
                if pattern.search(line):
                    add(BLOCK, f"{path}:{number}", f"guardrail: {label}")


def check_prints() -> None:
    for path in files({".py"}):
        if "tests" in path.parts or path.name == "models_root.py":
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        count = len(re.findall(r"^\s*print\(", text, re.MULTILINE))
        if count:
            add(INFO, str(path), f"{count} print() call(s) in library code")


KEY_RE = re.compile(r"^\s{2,}([a-zA-Z_][\w]*)\s*:", re.MULTILINE)


def check_i18n() -> None:
    roots = [path for path in pathlib.Path(".").rglob("i18n") if path.is_dir()]
    for root in roots:
        langs = {path.name: path for path in root.iterdir() if path.is_dir()}
        if "en" not in langs or "ru" not in langs:
            continue
        for en_file in sorted(langs["en"].glob("*")):
            ru_file = langs["ru"] / en_file.name
            if not ru_file.is_file():
                add(WARN, str(en_file), f"no {langs['ru'].name} counterpart for this file")
                continue
            en_keys = set(KEY_RE.findall(en_file.read_text(encoding="utf-8")))
            ru_keys = set(KEY_RE.findall(ru_file.read_text(encoding="utf-8")))
            missing = sorted(en_keys - ru_keys)
            extra = sorted(ru_keys - en_keys)
            if missing:
                add(WARN, str(ru_file), f"missing keys: {', '.join(missing[:8])}")
            if extra:
                add(INFO, str(ru_file), f"keys not in en: {', '.join(extra[:8])}")


def diff_against(base: str) -> None:
    def count(pattern: str, rev: str | None) -> int:
        command = ["git", "grep", "-c", "-E", pattern]
        if rev:
            command.append(rev)
        result = subprocess.run(command, capture_output=True, text=True)
        return sum(int(line.rsplit(":", 1)[1]) for line in result.stdout.splitlines() if ":" in line)

    for pattern, label in (
        (r"pytest\.mark\.skip|pytest\.skip\(", "pytest skips"),
        (r"@unittest\.skip", "unittest skips"),
        (r"TODO|FIXME", "TODO/FIXME markers"),
    ):
        now = count(pattern, None)
        before = count(pattern, base)
        if now > before:
            add(WARN, base, f"{label}: {before} before, {now} now (+{now - before})")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", help="git revision to compare marker counts against")
    args = parser.parse_args()

    check_conflict_markers()
    check_duplicate_defs()
    check_risky_calls()
    check_guardrails()
    check_i18n()
    check_prints()
    if args.base:
        diff_against(args.base)

    order = {BLOCK: 0, WARN: 1, INFO: 2}
    findings.sort(key=lambda item: (order[item[0]], item[1]))
    for level, where, message in findings:
        print(f"{level:5} {where}: {message}")

    blocking = sum(1 for level, _, _ in findings if level == BLOCK)
    warnings = sum(1 for level, _, _ in findings if level == WARN)
    print(f"\n{blocking} blocking, {warnings} warnings, {len(findings) - blocking - warnings} notes")
    return 1 if blocking else 0


if __name__ == "__main__":
    sys.exit(main())
