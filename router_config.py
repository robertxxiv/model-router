"""Per-project on/off switch for the router.

The flag lives in the project, not in the router, so a checkout carries its own
answer: `.herdr/router.json` with `{"enabled": false}`. An absent file means
enabled - routing is the default, and disabling it is the deliberate act.

Project root is the nearest ancestor holding `.herdr/`, `.git/` or `AGENTS.md`,
so the flag is found from any subdirectory of the project.
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

FLAG_DIR = ".herdr"
FLAG_NAME = "router.json"
MARKERS = (".herdr", ".git", "AGENTS.md", "CLAUDE.md")


def project_root(start: Path | str | None = None) -> Path:
    """Nearest ancestor that looks like a project; the start dir if none does."""
    here = Path(start or Path.cwd()).resolve()
    if here.is_file():
        here = here.parent
    for d in (here, *here.parents):
        if any((d / m).exists() for m in MARKERS):
            return d
    return here


def flag_path(start: Path | str | None = None) -> Path:
    return project_root(start) / FLAG_DIR / FLAG_NAME


def read(start: Path | str | None = None) -> dict:
    """{'enabled': bool, 'source': 'default'|'file', 'path': Path}"""
    p = flag_path(start)
    if not p.exists():
        return {"enabled": True, "source": "default", "path": p, "note": None}
    try:
        data = json.loads(p.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        # An unreadable flag must not silently disable routing, and must not
        # crash a spawn either. Default on, and say why.
        return {"enabled": True, "source": "unreadable", "path": p,
                "note": f"could not read {p}: {exc}"}
    return {"enabled": bool(data.get("enabled", True)), "source": "file", "path": p,
            "note": data.get("note")}


def enabled(start: Path | str | None = None) -> bool:
    return read(start)["enabled"]


def write(on: bool, start: Path | str | None = None, note: str | None = None) -> Path:
    p = flag_path(start)
    p.parent.mkdir(parents=True, exist_ok=True)
    body = {"enabled": on}
    if note:
        body["note"] = note
    # Write to a temp file in the same directory and swap it into place, so
    # an interrupted or concurrent write can never leave a truncated flag.
    tmp = tempfile.NamedTemporaryFile(dir=p.parent, prefix=p.name + ".",
                                      suffix=".tmp", delete=False)
    try:
        tmp.write(json.dumps(body, indent=2).encode() + b"\n")
        tmp.flush()
        os.fsync(tmp.fileno())
        tmp.close()
        os.chmod(tmp.name, 0o600)
        os.replace(tmp.name, p)
    except BaseException:
        tmp.close()
        try:
            os.unlink(tmp.name)
        except OSError:
            pass
        raise
    return p
