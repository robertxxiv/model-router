"""Measure the files a task touches, so Jev can tell wide from hard.

Every measurement is best-effort: a missing path is reported as missing rather
than raising, because a wrong path should still produce a routing decision.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

MAX_BYTES = 2_000_000       # files larger than this are counted, not read
MAX_DIRECTORY_FILES = 200
TEST_HINTS = ("test_", "_test", "spec_", "_spec")


def _loc(p: Path) -> int | None:
    try:
        if p.stat().st_size > MAX_BYTES:
            return None
        with p.open("r", encoding="utf-8", errors="replace") as fh:
            return sum(1 for _ in fh)
    except OSError:
        return None


def _looks_like_test(p: Path) -> bool:
    name = p.name.lower()
    return any(h in name for h in TEST_HINTS) or "tests" in {q.lower() for q in p.parts}


def _has_nearby_tests(p: Path) -> bool:
    """A sibling test file or a tests/ directory beside or above the target."""
    try:
        parent = p.parent
        if any(_looks_like_test(q) for q in parent.iterdir() if q.is_file()):
            return True
        for d in (parent, *parent.parents):
            if (d / "tests").is_dir() or (d / "test").is_dir():
                return True
            if (d / ".git").exists():
                break
    except OSError:
        pass
    return False


def _git_diff_stat(cwd: Path) -> str | None:
    try:
        r = subprocess.run(["git", "diff", "--stat"], cwd=cwd, capture_output=True,
                           text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    out = r.stdout.strip()
    return out[-2000:] if r.returncode == 0 and out else None


def measure(paths: list[str] | None, *, include_diff: bool = False,
            cwd: Path | None = None) -> dict:
    """Facts about the named paths. Returns {} when nothing was named."""
    cwd = cwd or Path.cwd()
    facts: dict = {}

    if paths:
        files, missing, dirs = [], [], []
        files_skipped = 0
        for raw in paths:
            p = (cwd / raw).resolve() if not Path(raw).is_absolute() else Path(raw)
            if p.is_dir():
                kids = sorted(
                    (q for q in p.rglob("*") if q.is_file()),
                    key=lambda q: q.relative_to(p).as_posix(),
                )
                dirs.append({"path": raw, "files_within": len(kids)})
                selected = kids[:MAX_DIRECTORY_FILES]
                files_skipped += len(kids) - len(selected)
                files += [(raw + "/" + q.relative_to(p).as_posix(), q) for q in selected]
                continue
            if not p.exists():
                missing.append(raw)
                continue
            files.append((raw, p))

        measured = []
        total = 0
        for shown, p in files:
            loc = _loc(p)
            if loc:
                total += loc
            measured.append({"path": shown, "lines": loc, "suffix": p.suffix or None})

        facts["files_named"] = len(measured)
        facts["files"] = measured[:40]
        facts["total_lines"] = total or None
        facts["file_types"] = sorted({m["suffix"] for m in measured if m["suffix"]})
        if dirs:
            facts["directories_named"] = dirs
        if files_skipped:
            facts["files_truncated"] = True
            facts["files_skipped"] = files_skipped
        if missing:
            facts["paths_that_do_not_exist"] = missing
        if measured:
            targets = [p for _, p in files if not _looks_like_test(p)]
            facts["tests_already_exist_nearby"] = any(_has_nearby_tests(p) for p in targets) \
                if targets else True
            facts["some_named_files_are_tests"] = any(_looks_like_test(p) for _, p in files)

    if include_diff:
        stat = _git_diff_stat(cwd)
        if stat:
            facts["uncommitted_diff_stat"] = stat

    return facts
