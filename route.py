#!/usr/bin/env python3
"""Model router: a task assignment -> who should do it, and how to start them.

    route.py "add retry with exponential backoff to the HTTP client" --files src/http/client.py

Jev characterizes the task. Deterministic code does everything else: it
enumerates the candidates your catalog declares, filters them on facts and
policy, scores the survivors, and prints the winner with the arithmetic that
chose it. No ladder, no hardcoded model names, and the selection is replayable
offline from stored judgments.

Advisory for pane spawns: it prints the herdr commands and never runs them. The
only command it runs is the read-only `herdr agent list`.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

import cache as cache_mod
import candidates as cand_mod
import catalog as catalog_mod
import eligibility as elig_mod
import herdr_state
import jev_router as rules
import repo_facts
import router_config
import scoring
import tiers

EXIT_OK = 0
EXIT_NO_ROUTE = 5      # nothing can run this task

HERE = Path(__file__).parent

# Claude Code's in-process subagents accept only these aliases, never a full
# identifier, and cannot run a locally served model at all.
SUBAGENT_ALIASES = ("haiku", "sonnet", "opus", "fable")
CHEAPEST_SUBAGENT = "haiku"
ROLE_ALIAS = {
    tiers.COORDINATION: CHEAPEST_SUBAGENT,
    tiers.LOCAL_WORKER: CHEAPEST_SUBAGENT,
    tiers.ORCHESTRATION: "sonnet",
    tiers.ORCHESTRATION_ESCALATION: "opus",
    tiers.WORKER: "sonnet",
    tiers.WORKER_ESCALATION: "opus",
    tiers.SPECIALIST: "fable",
}


def load_env() -> None:
    """Minimal .env reader; real environment variables win. Checks here and ../"""
    for p in (HERE / ".env", HERE.parent / ".env"):
        if not p.exists():
            continue
        for line in p.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip("\"'"))


def load_config(path: Path | None) -> dict:
    p = path or (HERE / "config.json")
    if not p.exists():
        return dict(rules.DEFAULTS)
    return {**rules.DEFAULTS, **json.loads(p.read_text())}


def _catalog_for(args) -> catalog_mod.Catalog:
    """The catalog this invocation uses, or an empty one."""
    path = Path(args.catalog) if args.catalog else catalog_mod.default_path(HERE)
    try:
        return catalog_mod.parse(path, required=bool(args.catalog))
    except catalog_mod.CatalogError:
        return catalog_mod.Catalog()


def build_candidates(args, cfg: dict) -> tuple[list[cand_mod.Candidate], str, list[str]]:
    """Candidates from the catalog, or from a policy file when asked.

    The catalog is primary: it states facts. A markdown policy file is still
    supported for anyone who prefers one source of truth over two files.
    """
    notes: list[str] = []
    cat_path = Path(args.catalog) if args.catalog else catalog_mod.default_path(HERE)
    try:
        cat = catalog_mod.parse(cat_path, required=bool(args.catalog))
    except catalog_mod.CatalogError as exc:
        sys.exit(f"catalog error: {exc}")
    notes += [f"catalog: {w}" for w in cat.warnings]

    if not any(e.role for e in cat.entries.values()):
        sys.exit(
            f"no usable catalog at {cat_path}: no entry declares a `role`.\n"
            f"Build one with `discover.py --out models.json` (from your inference "
            f"server), convert a markdown policy file with "
            f"`router import-roster <file>`, or copy models.example.json."
        )
    return cand_mod.from_catalog(cat, cfg), f"catalog {cat.source}", notes


def judge_cached(task: str, facts: dict | None, attempts, args, cfg: dict):
    """Judgments for this task, replayed from the cache when possible.

    This is what makes the router deterministic: identical input gives
    identical judgments, so it gives an identical route. The key covers the
    task, the measured facts, the wording of every question and the Jev model,
    so nothing stale can survive a change that would have altered the answer.
    """
    root = router_config.project_root(Path(args.cwd) if args.cwd else None)
    qsha = cache_mod.questions_fingerprint(rules.QUESTIONS)
    jev_model = args.jev_model or os.environ.get("TYPESAFE_DEFAULT_MODEL") or "jev-latest"
    key, task_sha, facts_sha = cache_mod.Store.key(task, facts, qsha, jev_model)

    store = None
    if not args.no_cache:
        try:
            store = cache_mod.Store.open(root)
            hit = store.get(key)
            if hit is not None:
                return hit, "cache"
        except Exception as exc:                     # a broken cache must never block
            print(f"# cache unavailable ({exc}); judging live", file=sys.stderr)
            store = None

    load_env()
    if not os.environ.get("TYPESAFE_API_KEY"):
        sys.exit("TYPESAFE_API_KEY not found. Put it in .env or export it. "
                 "(Or pass --judgments-file to route from stored judgments.)")
    from typesafe_sdk import AsyncTypeSafeClient
    return (AsyncTypeSafeClient, store, key, task_sha, facts_sha, qsha, jev_model), "live"


def parse_failed(values: list[str] | None) -> tuple[set[str], list[dict]]:
    """`--failed KEY|ROLE|IDENTIFIER[:reason]` -> (things to avoid, notes for Jev)."""
    avoid: set[str] = set()
    notes: list[dict] = []
    for raw in values or []:
        target, _, reason = raw.partition(":")
        target = target.strip()
        if not target:
            continue
        avoid.add(target)
        avoid.add(target.upper().replace("-", "_"))      # tolerate a role spelling
        notes.append({"reason": reason.strip() or None})
    return avoid, notes


def subagent_alias(role: str, identifier: str) -> tuple[str, str]:
    """(alias, why) for a Claude Code subagent `model` field."""
    low = identifier.lower()
    for alias in SUBAGENT_ALIASES:
        if alias in low:
            return alias, f"{role} -> {identifier}"
    fallback = ROLE_ALIAS.get(role, "sonnet")
    return fallback, f"{role} has no in-process equivalent; closest alias is {fallback}"


# ------------------------------------------------------------------ output --
def show_candidates(cands: list[cand_mod.Candidate], source: str) -> None:
    print(f"candidates from {source}\n")
    print(f"  {'key':24} {'role':26} {'launch':24} {'ctx':>8} {'cost':>5}  capabilities")
    for c in cands:
        caps = " ".join(f"{k[:4]} {v:.2f}" for k, v in sorted(c.capabilities.items()))
        print(f"  {c.key:24} {c.role:26} {c.identifier:24} {c.context_tokens:>8} "
              f"{c.cost_class:>5}  {caps}")
    assumed = [c.key for c in cands if not c.declared("capabilities")]
    if assumed:
        print(f"\n  capabilities not declared (derived from role): {', '.join(assumed)}")


def show_status(start) -> None:
    state = router_config.read(start)
    print(f"routing: {'ON' if state['enabled'] else 'OFF'}  ("
          f"{'project default, no flag file' if state['source'] == 'default' else state['path']})")
    print(f"project: {router_config.project_root(start)}")
    cat_path = catalog_mod.default_path(HERE)
    if cat_path.exists():
        cat = catalog_mod.parse(cat_path)
        roles = sorted({e.role for e in cat.entries.values() if e.role})
        print(f"catalog: {cat_path} ({len(cat.entries)} models, roles: {', '.join(roles)})")
        if cat.updated_at:
            print(f"         updated {cat.updated_at}")
    else:
        print(f"catalog: none at {cat_path} - run discover.py --out models.json")


def report(d: scoring.Decision, j: dict, args, reuse, herdr_err, source: str,
           notes: list[str], judgment_source: str = "jev") -> None:
    win = d.winner
    if win is None:
        print("\nNO ROUTE - every candidate was excluded\n")
        for n in d.notes:
            print(f"note : {n}")
        for e in d.exclusions:
            print(f"  {e.key:24} {e.rule:34} {e.reason}")
        return

    c = win.candidate
    conf = rules.confidence(j)
    print(f"\n{c.role}  {c.model.label}   score {win.score:.3f}   confidence {conf:.2f}")
    print(f"task : {j['task_kind']} ({j['task_kind_confidence']:.0%}) -> "
          f"{d.family} capability {c.capability(d.family):.2f} vs need {d.need:.2f}")
    print(f"ctx  : holds {c.context_tokens:,}, task needs about {d.required_tokens:,}")

    terms = " ".join(f"{k} {v:+.3f}" for k, v in win.terms.items() if abs(v) > 1e-9)
    print(f"why  : {terms or 'no penalties'}")

    if judgment_source != "jev":
        print(f"judgments: replayed from {judgment_source} - this route is reproducible")

    if d.runner_up:
        r = d.runner_up
        print(f"next : {r.candidate.model.label} score {r.score:.3f} (gap {d.gap:+.3f})")
    for note in d.notes:
        print(f"note : {note}")
    for near in rules.near_thresholds(j, {**rules.DEFAULTS, **(args._cfg or {})}):
        print(f"note : close to a gate, could flip - {near}")
    for n in notes:
        print(f"warn : {n}")

    if args.verbose:
        print("\n  ranked:")
        for s in d.ranked:
            print(f"    {s.score:6.3f}  {s.key:24} {s.candidate.identifier}")
        print("  excluded:")
        for e in d.exclusions:
            print(f"    {e.key:24} {e.rule:34} {e.reason[:70]}")

    if d.reviewer:
        print(f"\nreviewer: {d.reviewer.candidate.model.label} "
              f"({d.reviewer.candidate.role}, score {d.reviewer.score:.3f})")
        print(f"why     : high-risk work needs a reviewer independent of the implementer")

    print()
    if herdr_err:
        print(f"# herdr not consulted: {herdr_err}")
    if reuse:
        print(f"# {reuse['match_strength']} reuse: {reuse['name']} "
              f"(pane {reuse['pane_id']}, {reuse['status']}, cwd {reuse['cwd']})")
        print("#   herdr does not report an agent's model - confirm before reusing")
        print(herdr_state.prompt_command(reuse["name"]))
        print("# or spawn a fresh worker:")
    name = args.name or f"{c.model.kind}-{c.role.lower().replace('_', '-')}-1"
    for line in herdr_state.spawn_commands(c.model, name, args.cwd or Path.cwd()):
        print(line)


# --------------------------------------------------------------------- run --
async def run(args: argparse.Namespace) -> int:
    start = Path(args.cwd) if args.cwd else None

    if args.status:
        show_status(start)
        return 0
    if args.enable or args.disable:
        p = router_config.write(bool(args.enable), start,
                                note=None if args.enable else "routing disabled for this project")
        print(f"routing {'enabled' if args.enable else 'disabled'} for "
              f"{router_config.project_root(start)}\n  wrote {p}")
        return 0

    cfg = load_config(Path(args.config) if args.config else None)
    args._cfg = cfg
    cands, source, notes = build_candidates(args, cfg)

    if args.show_candidates:
        show_candidates(cands, source)
        for n in notes:
            print(f"\n  warning: {n}")
        return 0

    state = router_config.read(start)
    if not state["enabled"] and not args.ignore_switch:
        if args.for_subagent:
            return 0            # the hook must stay silent and change nothing
        sys.exit(f"routing is disabled for {router_config.project_root(start)} "
                 f"({state['path']}). Re-enable with `route.py --enable`, or pass "
                 f"--ignore-switch for a one-off.")

    task = args.task
    if args.task_file:
        task = Path(args.task_file).read_text(encoding="utf-8").strip()
    if not task:
        sys.exit('give a task: route.py "<assignment>" or --task-file PATH')

    avoid, attempt_notes = parse_failed(args.failed)
    facts = repo_facts.measure(args.files, include_diff=args.diff,
                               cwd=Path(args.cwd) if args.cwd else None)

    judgment_source = "file"
    if args.judgments_file:
        j = json.loads(Path(args.judgments_file).read_text())
    else:
        got, how = judge_cached(task, facts or None, attempt_notes, args, cfg)
        if how == "cache":
            j, judgment_source = got, "cache"
        else:
            client_cls, store, key, task_sha, facts_sha, qsha, jev_model = got
            async with client_cls() as client:
                j = await rules.judge(client, task, facts or None,
                                      attempt_notes or None, model=args.jev_model)
            judgment_source = "jev"
            if store is not None:
                try:
                    store.put(key, task_sha, facts_sha, qsha, jev_model, j)
                finally:
                    store.close()

    # Models the catalog marks unusable.
    excluded = _catalog_for(args).rejected

    d = scoring.decide(cands, j, cfg, facts=facts, excluded_identifiers=excluded,
                       failed=avoid, allow_specialist=args.allow_specialist,
                       harnesses=None if not args.no_harness_check else {
                           c.model.kind for c in cands})


    if args.for_subagent:
        if not d.winner:
            return 0
        alias, why = subagent_alias(d.winner.candidate.role, d.winner.candidate.identifier)
        print(json.dumps({"model": alias, "role": d.winner.candidate.role,
                          "key": d.winner.key, "score": d.winner.score, "why": why,
                          "needs_confirmation": d.needs_confirmation}))
        return 0

    reuse, herdr_err = None, None
    if not args.no_herdr and d.winner:
        agent_list, herdr_err = herdr_state.agents()
        if agent_list:
            reuse = herdr_state.find_reuse(agent_list, d.winner.candidate.model,
                                           Path(args.cwd) if args.cwd else None)

    if args.json:
        win = d.winner
        source_note = judgment_source
        name = args.name or (f"{win.candidate.model.kind}-"
                             f"{win.candidate.role.lower().replace('_', '-')}-1" if win else None)
        print(json.dumps({
            "task": task,
            "source": source,
            "family": d.family,
            "need": d.need,
            "required_tokens": d.required_tokens,
            "winner": win.as_dict() if win else None,
            "runner_up": d.runner_up.as_dict() if d.runner_up else None,
            "gap": d.gap,
            "reviewer": d.reviewer.as_dict() if d.reviewer else None,
            "needs_confirmation": d.needs_confirmation,
            "notes": d.notes + notes,
            "ranked": [s.as_dict() for s in d.ranked],
            "exclusions": [vars(e) for e in d.exclusions],
            "judgments": {k: v for k, v in j.items() if not k.startswith("_")},
            "judgment_source": judgment_source,
            "repo_facts": facts,
            "reuse_candidate": reuse,
            "herdr_error": herdr_err,
            "commands": herdr_state.spawn_commands(
                win.candidate.model, name, args.cwd or Path.cwd()) if win else [],
            "usage": j.get("_usage"),
            "jev_model": j.get("_model"),
        }, indent=2))
    else:
        report(d, j, args, reuse, herdr_err, source, notes, judgment_source)
    return EXIT_NO_ROUTE if d.winner is None else EXIT_OK


def add_run_arguments(p: argparse.ArgumentParser) -> argparse.ArgumentParser:
    """Flags for routing one task. Shared by `router run` and the legacy entry."""
    p.add_argument("task", nargs="?", default="", help="the task assignment, as text")
    p.add_argument("--task-file", help="read the assignment from a file instead")
    p.add_argument("--files", nargs="+", help="paths the task touches")
    p.add_argument("--diff", action="store_true", help="include git diff --stat as a fact")
    p.add_argument("--failed", action="append", metavar="TARGET[:REASON]",
                   help="a candidate, role or model that already failed this task")
    p.add_argument("--name", help="worker name for the emitted command")
    p.add_argument("--cwd", help="cwd for the emitted pane split (default: $PWD)")
    p.add_argument("--allow-specialist", action="store_true", help="permit the specialist role")
    p.add_argument("--no-herdr", action="store_true",
                   help="skip the read-only `herdr agent list` probe")
    p.add_argument("--no-harness-check", action="store_true",
                   help="do not require a harness to be installed")
    p.add_argument("--json", action="store_true", help="machine-readable decision")
    p.add_argument("-v", "--verbose", action="store_true",
                   help="show the full ranking and every exclusion")
    p.add_argument("--judgments-file", help="offline: route stored judgments, no Jev call")
    p.add_argument("--status", action="store_true", help="is routing on, and from what catalog")
    p.add_argument("--enable", action="store_true", help="turn routing on for this project")
    p.add_argument("--disable", action="store_true", help="turn routing off for this project")
    p.add_argument("--ignore-switch", action="store_true", help="route even if disabled")
    p.add_argument("--for-subagent", action="store_true",
                   help="emit one JSON line with a subagent model alias (for a hook)")
    p.add_argument("--show-candidates", action="store_true",
                   help="print the candidate table and exit")
    p.add_argument("--catalog", help="models.json (default: ./models.json)")
    p.add_argument("--config", help="thresholds and weights (default: ./config.json)")
    p.add_argument("--jev-model", help="TypeSafe model (default: jev-latest)")
    p.add_argument("--no-cache", action="store_true",
                   help="judge live even if this task was judged before")
    return p


def main(argv: list[str] | None = None) -> int:
    p = add_run_arguments(argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter))
    args = p.parse_args(argv)
    args._cfg = None
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
