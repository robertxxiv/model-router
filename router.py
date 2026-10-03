#!/usr/bin/env python3
"""router - pick the cheapest capable model for a task, and show how to start it in Herdr.

    router run "<task>" --files src/http/client.py
    router models                 which models are candidates, and why
    router status                 is routing on, what catalog, what cache
    router enable / disable       turn routing on or off for this project
    router cache stats            how often routes are replayed rather than judged
    router import-roster FILE     convert a markdown policy file into models.json

Jev characterizes the task; everything after that is deterministic. Judgments
are cached, so the same task always produces the same route.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import sys
import time
from pathlib import Path

import cache as cache_mod
import candidates as cand_mod
import catalog as catalog_mod
import eligibility as elig_mod
import jev_router as rules
import route
import router_config

HERE = Path(__file__).parent

def _write_atomic(path: Path, body: str, mode: int = 0o600) -> None:
    """Replace `path` in one step, so an interrupted run leaves no half-file."""
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_CREAT | os.O_TRUNC | os.O_WRONLY, mode)
    try:
        with os.fdopen(fd, "w") as fh:
            fh.write(body)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


# --------------------------------------------------------------------- run --
def cmd_run(args) -> int:
    args._cfg = None
    return asyncio.run(route.run(args))


# ------------------------------------------------------------------ models --
def cmd_models(args) -> int:
    args._cfg = cfg = route.load_config(Path(args.config) if args.config else None)
    cands, source, notes = route.build_candidates(args, cfg)
    route.show_candidates(cands, source)
    for n in notes:
        print(f"\n  warning: {n}")
    return 0


# ------------------------------------------------------------------ status --
def cmd_switch(args) -> int:
    """Turn routing on or off for this project."""
    start = Path(args.cwd) if args.cwd else None
    on = args.command == "enable"
    path = router_config.write(
        on, start, note=None if on else "routing disabled for this project")
    print(f"routing {'enabled' if on else 'disabled'} for "
          f"{router_config.project_root(start)}\n  wrote {path}")
    return 0


def cmd_status(args) -> int:
    start = Path(args.cwd) if args.cwd else None
    route.show_status(start)
    try:
        store = cache_mod.Store.open(router_config.project_root(start))
        st = store.stats()
        print(f"cache  : {st['entries']} judgments, {st['replays']} replays  ({st['path']})")
        if len(st["question_versions"]) > 1:
            print(f"         {len(st['question_versions'])} question versions present; "
                  f"`router cache prune` drops the old ones")
        store.close()
    except Exception as exc:
        print(f"cache  : unavailable ({exc})")
    return 0


# ------------------------------------------------------------------- cache --
def cmd_cache(args) -> int:
    root = router_config.project_root(Path(args.cwd) if args.cwd else None)
    store = cache_mod.Store.open(root)
    try:
        if args.cache_action == "stats":
            st = store.stats()
            print(f"path     : {st['path']}")
            print(f"judgments: {st['entries']}")
            print(f"replays  : {st['replays']}")
            if st["oldest"]:
                age = int(time.time()) - st["oldest"]
                print(f"oldest   : {age // 3600}h {age % 3600 // 60}m ago")
            current = cache_mod.questions_fingerprint(rules.QUESTIONS)[:12]
            for sha, n in sorted(st["question_versions"].items()):
                mark = "  <- current questions" if sha == current else ""
                print(f"questions: {sha}  {n} entries{mark}")
        elif args.cache_action == "clear":
            print(f"removed {store.clear()} judgments")
        elif args.cache_action == "prune":
            n = store.prune(questions_sha=cache_mod.questions_fingerprint(rules.QUESTIONS),
                            older_than=args.older_than)
            print(f"removed {n} judgments")
    finally:
        store.close()
    return 0


# ----------------------------------------------------------- import-roster --
def cmd_import_roster(args) -> int:
    """One-shot conversion of a markdown policy file into a catalog."""
    import roster as roster_mod
    try:
        r = roster_mod.parse(args.path)
    except roster_mod.RosterError as exc:
        print(f"roster error: {exc}", file=sys.stderr)
        return 1
    cands = cand_mod.build(r, route.load_config(None), catalog_mod.Catalog())
    models = []
    for c in cands:
        models.append({
            "id": c.key.replace("/", ":"),
            "role": c.role,
            "harness": c.model.kind,
            "model": c.identifier,
            **({"effort": c.model.effort} if c.model.effort else {}),
            "launchArgs": list(c.model.extra_args),
            "contextWindow": c.context_tokens,
            "capabilities": {k: round(v, 2) for k, v in sorted(c.capabilities.items())},
            "unmetered": c.unmetered,
            "quantized": c.quantized,
            **({"images": c.images} if c.images is not None else {}),
            "notes": (c.model.note or c.model.role_label or "")[:200],
        })
    doc = {
        "updatedAt": time.strftime("%Y-%m-%d"),
        "provenance": (f"Imported from {r.source} by `router import-roster`. Context "
                       f"windows and capability numbers that the policy file did not "
                       f"state were derived from the role and should be reviewed; run "
                       f"`discover.py` to replace self-hosted facts with real ones."),
        "models": models,
    }
    out = Path(args.out)
    body = json.dumps(doc, indent=2) + "\n"
    if args.force:
        _write_atomic(out, body)
    else:
        # Exclusive create, so a file appearing between a check and a write is
        # never silently clobbered.
        try:
            fd = os.open(out, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            print(f"{out} exists; pass --force to overwrite", file=sys.stderr)
            return 1
        with os.fdopen(fd, "w") as fh:
            fh.write(body)
            fh.flush()
            os.fsync(fh.fileno())
    print(f"wrote {len(models)} models to {out}")
    for w in r.warnings:
        print(f"  warning: {w}")
    print("\nReview the capability numbers, then `router models` to check the result.")
    return 0


# --------------------------------------------------------------------- cli --
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="router", description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="command")

    run = sub.add_parser("run", help="route one task",
                         formatter_class=argparse.RawDescriptionHelpFormatter)
    route.add_run_arguments(run)
    run.set_defaults(func=cmd_run)

    models = sub.add_parser("models", help="the candidate table and why each model is there")
    for flag in ("--catalog", "--config", "--cwd"):
        models.add_argument(flag)
    models.set_defaults(func=cmd_models, show_candidates=True)

    st = sub.add_parser("status", help="routing, catalog and cache at a glance")
    st.add_argument("--cwd")
    st.add_argument("--config")
    st.set_defaults(func=cmd_status)

    for name, helptext in (("enable", "turn routing on for this project"),
                           ("disable", "turn routing off for this project")):
        sw = sub.add_parser(name, help=helptext)
        sw.add_argument("--cwd")
        sw.set_defaults(func=cmd_switch)

    c = sub.add_parser("cache", help="the judgment cache that makes routes repeatable")
    csub = c.add_subparsers(dest="cache_action", required=True)
    for name, helptext in (("stats", "size, replays, question versions"),
                           ("clear", "remove every judgment"),
                           ("prune", "remove judgments from older question versions")):
        cp = csub.add_parser(name, help=helptext)
        cp.add_argument("--cwd")
        if name == "prune":
            cp.add_argument("--older-than", type=int, metavar="SECONDS")
        cp.set_defaults(func=cmd_cache, cache_action=name)

    imp = sub.add_parser("import-roster",
                         help="convert a markdown policy file into models.json")
    imp.add_argument("path", help="the markdown policy file")
    imp.add_argument("--out", default="models.json")
    imp.add_argument("--force", action="store_true")
    imp.set_defaults(func=cmd_import_roster)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 1
    # defaults the shared helpers expect
    for name, default in (("cwd", None), ("config", None), ("catalog", None),
                          ("roster", None), ("older_than", None)):
        if not hasattr(args, name):
            setattr(args, name, default)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
