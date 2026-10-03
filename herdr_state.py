"""Read-only herdr probe, plus the spawn commands the router prints.

The router never mutates a herdr session. The single command it runs is
`herdr agent list`, which is read-only; everything else is printed for the
operator to run.

Known limitation: no herdr command reports the model an agent was launched
with - `agent list`, `agent get` and `agent explain --json` were all checked
and none carries it. So a reuse candidate is matched on harness kind, idle
status, and whether the agent's name or terminal title mentions the chosen
identifier. Treat a suggested reuse as a prompt to confirm, not a guarantee.
"""
from __future__ import annotations

import json
import re
import shlex
import subprocess
from pathlib import Path

import model as model_mod

IDLE = {"idle", "done"}


def agents(timeout: float = 5.0) -> tuple[list[dict], str | None]:
    """Every agent herdr knows about. Returns ([], reason) when unavailable."""
    try:
        r = subprocess.run(["herdr", "agent", "list"], capture_output=True,
                           text=True, timeout=timeout)
    except FileNotFoundError:
        return [], "herdr is not on PATH"
    except (OSError, subprocess.SubprocessError) as exc:
        return [], f"herdr agent list failed: {exc}"
    if r.returncode != 0:
        return [], (r.stderr or "herdr agent list exited non-zero").strip()[:200]
    try:
        payload = json.loads(r.stdout)
    except json.JSONDecodeError:
        return [], "herdr agent list did not return JSON"
    return payload.get("result", {}).get("agents", []) or [], None


def _slugs(identifier: str) -> set[str]:
    """Fragments of an identifier worth matching in a name or a title."""
    low = identifier.lower()
    parts = {p for p in re.split(r"[-_.]", low) if len(p) > 2 and not p.isdigit()}
    return parts | {low}


def find_reuse(agent_list: list[dict], model: model_mod.Model,
               cwd: Path | None = None) -> dict | None:
    """An idle agent that plausibly already runs this model. Best match first."""
    cwd = str(cwd or Path.cwd())
    slugs = _slugs(model.identifier)
    scored = []
    for a in agent_list:
        if a.get("agent") != model.kind or a.get("agent_status") not in IDLE:
            continue
        name = (a.get("name") or "").lower()
        title = (a.get("terminal_title_stripped") or "").lower()
        score = 0
        if any(s in name for s in slugs):
            score += 3                      # a named worker is the strong signal
        elif any(s in title for s in slugs):
            score += 1
        if a.get("cwd") == cwd:
            score += 2
        if a.get("interactive_ready"):
            score += 1
        if score:
            scored.append((score, a))
    if not scored:
        return None
    scored.sort(key=lambda s: -s[0])
    best_score, best = scored[0]
    return {
        "name": best.get("name") or best.get("pane_id"),
        "pane_id": best.get("pane_id"),
        "cwd": best.get("cwd"),
        "status": best.get("agent_status"),
        "title": best.get("terminal_title_stripped"),
        "model_confirmed": False,
        "match_strength": "likely" if best_score >= 3 else "possible",
    }


# Harness kinds herdr accepts. A catalog is data, and these strings end up in a
# command line a human is told to run, so an unknown one is refused rather than
# printed.
KNOWN_KINDS = frozenset({
    "pi", "claude", "codex", "gemini", "cursor", "devin", "agy", "cline", "omp",
    "mastracode", "opencode", "copilot", "kimi", "kiro", "droid", "amp", "grok",
    "hermes", "kilo", "qodercli", "qwen", "letta", "maki", "muse",
})
SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


def safe_kind(kind: str) -> str:
    if kind not in KNOWN_KINDS:
        raise ValueError(
            f"unknown harness kind {kind!r}; the catalog must name one of: "
            f"{', '.join(sorted(KNOWN_KINDS))}")
    return kind


def safe_name(name: str) -> str:
    """A worker name that cannot be read as an option or need quoting."""
    if not SAFE_NAME.match(name or ""):
        raise ValueError(
            f"unusable worker name {name!r}; use letters, digits, dot, dash or "
            f"underscore, not starting with a dash")
    return name


def spawn_commands(model: model_mod.Model, name: str, cwd: Path | str) -> list[str]:
    """The exact herdr lines to bring this model up in a fresh pane.

    Every interpolated value is either allowlisted or shell-quoted: these lines
    are printed for a human to run, so an unquoted catalog string would be a
    command-injection vector.
    """
    args = " ".join(shlex.quote(a) for a in model.extra_args)
    return [
        "herdr agent list",
        f"herdr pane split --current --direction right --cwd {shlex.quote(str(cwd))} --no-focus",
        f"herdr agent start {shlex.quote(safe_name(name))} "
        f"--kind {shlex.quote(safe_kind(model.kind))} --pane <pane-id> -- {args}",
    ]


def prompt_command(name: str, timeout_ms: int = 600000) -> str:
    return (f"herdr agent prompt {shlex.quote(safe_name(name))} "
            f"\"<assignment>\" --wait --timeout {timeout_ms}")
