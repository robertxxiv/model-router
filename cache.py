"""Judgment cache: the same task gets the same judgments, forever.

Jev's characterization of a task is the only non-deterministic step in a route.
Cache it and the whole router becomes deterministic for repeated input: the
first run calls Jev, every later run replays the stored answers. It also takes
the API call off the hot path, which is what the pre-spawn hook cares about.

The cache key covers everything that could legitimately change an answer:

    the task text, the measured repository facts, the exact wording of every
    question, and the Jev model

so rewording a question or editing the questions file invalidates the cache
automatically - a stale judgment can never outlive the question that produced it.

The raw task text is never stored, only its hash. Assignments routinely name
internal systems, so there is no reason to keep them on disk.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

class CacheError(Exception):
    """The judgment cache cannot be used safely."""


SCHEMA = """
CREATE TABLE IF NOT EXISTS judgments (
    key           TEXT PRIMARY KEY,
    task_sha      TEXT NOT NULL,
    facts_sha     TEXT NOT NULL,
    questions_sha TEXT NOT NULL,
    jev_model     TEXT,
    judgments     TEXT NOT NULL,
    created_at    INTEGER NOT NULL,
    last_used_at  INTEGER NOT NULL,
    hits          INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS judgments_created ON judgments(created_at);
"""

STATE_DIR = ".model-router"
DB_NAME = "state.sqlite"


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def canonical(obj) -> str:
    """Stable JSON, so an equal dict always hashes the same."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)


def questions_fingerprint(questions: dict) -> str:
    """Hash the questions as they are sent, so a reworded question misses."""
    shape = {}
    for qid, q in sorted(questions.items()):
        if hasattr(q, "model_dump"):
            shape[qid] = q.model_dump(exclude_none=True)
        else:
            shape[qid] = q
    return _sha(canonical(shape))


def normalize_task(task: str) -> str:
    """Collapse whitespace so trivial reformatting still hits the cache."""
    return " ".join(task.split())


@dataclass
class Store:
    path: Path
    conn: sqlite3.Connection

    # ---------------------------------------------------------------- open --
    @classmethod
    def open(cls, project_root: Path | str) -> "Store":
        d = Path(project_root) / STATE_DIR
        # Never follow a symlink into somewhere else: a checked-out repository
        # could ship `.model-router` or `state.sqlite` as a link and have us
        # write - and chmod - outside the project.
        if d.is_symlink():
            raise CacheError(f"{d} is a symlink; refusing to use it for state")
        d.mkdir(parents=True, exist_ok=True, mode=0o700)
        _harden(d, 0o700)
        path = d / DB_NAME
        if path.is_symlink():
            raise CacheError(f"{path} is a symlink; refusing to write state through it")
        # Create it ourselves, privately, so there is no window where the file
        # exists with default permissions.
        if not path.exists():
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
            os.close(fd)
        conn = sqlite3.connect(path, timeout=5.0)
        conn.row_factory = sqlite3.Row
        conn.executescript(SCHEMA)
        conn.commit()
        for p in (path, path.with_name(path.name + "-wal"),
                  path.with_name(path.name + "-shm")):
            if p.exists() and not p.is_symlink():
                _harden(p, 0o600)
        return cls(path=path, conn=conn)

    # --------------------------------------------------------------- keys --
    @staticmethod
    def key(task: str, facts: dict | None, questions_sha: str,
            jev_model: str | None) -> tuple[str, str, str]:
        task_sha = _sha(normalize_task(task))
        facts_sha = _sha(canonical(facts or {}))
        key = _sha("|".join([task_sha, facts_sha, questions_sha, jev_model or ""]))
        return key, task_sha, facts_sha

    # --------------------------------------------------------------- read --
    def get(self, key: str) -> dict | None:
        row = self.conn.execute(
            "SELECT judgments FROM judgments WHERE key = ?", (key,)).fetchone()
        if not row:
            return None
        self.conn.execute(
            "UPDATE judgments SET hits = hits + 1, last_used_at = ? WHERE key = ?",
            (int(time.time()), key))
        self.conn.commit()
        try:
            return json.loads(row["judgments"])
        except json.JSONDecodeError:
            self.conn.execute("DELETE FROM judgments WHERE key = ?", (key,))
            self.conn.commit()
            return None

    # -------------------------------------------------------------- write --
    def put(self, key: str, task_sha: str, facts_sha: str, questions_sha: str,
            jev_model: str | None, judgments: dict) -> None:
        now = int(time.time())
        stored = {k: v for k, v in judgments.items() if k != "_usage"}
        self.conn.execute(
            "INSERT INTO judgments (key, task_sha, facts_sha, questions_sha, "
            "jev_model, judgments, created_at, last_used_at, hits) "
            "VALUES (?,?,?,?,?,?,?,?,0) ON CONFLICT(key) DO UPDATE SET "
            "judgments = excluded.judgments, last_used_at = excluded.last_used_at",
            (key, task_sha, facts_sha, questions_sha, jev_model or "",
             canonical(stored), now, now))
        self.conn.commit()

    # --------------------------------------------------------------- misc --
    def stats(self) -> dict:
        row = self.conn.execute(
            "SELECT COUNT(*) n, COALESCE(SUM(hits),0) hits, MIN(created_at) oldest, "
            "MAX(last_used_at) newest FROM judgments").fetchone()
        shas = self.conn.execute(
            "SELECT questions_sha, COUNT(*) n FROM judgments GROUP BY questions_sha"
        ).fetchall()
        return {"path": str(self.path), "entries": row["n"], "replays": row["hits"],
                "oldest": row["oldest"], "newest": row["newest"],
                "question_versions": {r["questions_sha"][:12]: r["n"] for r in shas}}

    def prune(self, questions_sha: str | None = None, older_than: int | None = None) -> int:
        """Drop entries from other question versions, or older than N seconds."""
        n = 0
        if questions_sha:
            cur = self.conn.execute(
                "DELETE FROM judgments WHERE questions_sha != ?", (questions_sha,))
            n += cur.rowcount or 0
        if older_than:
            cur = self.conn.execute(
                "DELETE FROM judgments WHERE created_at < ?",
                (int(time.time()) - older_than,))
            n += cur.rowcount or 0
        self.conn.commit()
        return n

    def clear(self) -> int:
        cur = self.conn.execute("DELETE FROM judgments")
        self.conn.commit()
        return cur.rowcount or 0

    def close(self) -> None:
        self.conn.close()


def _harden(path: Path, mode: int) -> None:
    """Keep state private. Judgments describe internal work; so can a task hash."""
    try:
        if (path.stat().st_mode & 0o777) != mode:
            os.chmod(path, mode)
    except OSError:
        pass
