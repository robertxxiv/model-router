#!/usr/bin/env python3
"""Offline tests: catalog, candidates, eligibility, deterministic scoring.

    python tests/test_routing.py

No API calls and no personal config. Everything runs against
models.example.json, so the suite passes on a clean clone.
"""
from __future__ import annotations

import json
import random
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent))

import cache as cache_mod       # noqa: E402
import candidates as cand_mod    # noqa: E402
import catalog as catalog_mod    # noqa: E402
import eligibility as elig_mod   # noqa: E402
import herdr_state               # noqa: E402
import jev_router as rules       # noqa: E402
import roster as roster_mod      # noqa: E402
import route                     # noqa: E402
import router_config             # noqa: E402
import scoring                   # noqa: E402
import tiers                     # noqa: E402

CATALOG = HERE.parent / "models.example.json"
FIXTURE_ROSTER = HERE / "fixtures" / "DEVELOPMENT_TEAM.example.md"
CFG = dict(rules.DEFAULTS)
CASES = json.loads((HERE / "cases.json").read_text())

# Every harness the example catalog uses, so tests never depend on what is
# installed on the machine running them.
HARNESSES = {"pi", "claude", "codex"}

failures: list[str] = []
checks = 0


def check(ok: bool, label: str, detail: str = "") -> None:
    global checks
    checks += 1
    if not ok:
        failures.append(f"{label}{': ' + detail if detail else ''}")


def cands() -> list[cand_mod.Candidate]:
    return cand_mod.from_catalog(catalog_mod.parse(CATALOG), CFG)


def route_it(judgments: dict, **kw) -> scoring.Decision:
    kw.setdefault("harnesses", HARNESSES)
    return scoring.decide(cands(), judgments, CFG, **kw)


# ---------------------------------------------------------------- catalog --
def test_catalog() -> None:
    cat = catalog_mod.parse(CATALOG)
    check(not cat.warnings, "example catalog parses cleanly", str(cat.warnings))
    check(len(cat.entries) == 10, "all entries loaded", str(len(cat.entries)))

    # id is a catalog key; `model` is what the harness is told. That is how one
    # model serves two roles at different efforts.
    plan, high = cat.get("vendor:plan"), cat.get("vendor:plan-high")
    check(plan.launch_identifier == high.launch_identifier == "vendor-plan",
          "two roles can share one model")
    check(plan.effort == "medium" and high.effort == "high", "efforts differ")
    check("model_reasoning_effort=high" in " ".join(high.launch_args),
          "effort reaches the launch arguments")

    with tempfile.TemporaryDirectory() as d:
        bad = Path(d) / "bad.json"
        bad.write_text('{"models": [{"id": "x", "capabilities": {"nope": 2}, '
                       '"contextWindow": "abc"}]}')
        c = catalog_mod.parse(bad)
        check(len(c.warnings) >= 2, "bad fields are reported, not fatal", str(c.warnings))
        check(c.get("x") is not None, "the entry still loads")

        noshape = Path(d) / "noshape.json"
        noshape.write_text('{"nope": 1}')
        try:
            catalog_mod.parse(noshape)
            check(False, "a catalog with no models array raises")
        except catalog_mod.CatalogError as exc:
            check("models" in str(exc), "and says what is missing", str(exc))

    check(catalog_mod.parse(None).entries == {}, "no catalog is not an error")


def test_candidates() -> None:
    cs = cands()
    check(len(cs) == 10, "one candidate per role-bearing entry", str(len(cs)))
    check(all(c.declared("capabilities") for c in cs),
          "the example catalog declares every capability")
    check(all(c.declared("context_tokens") for c in cs), "and every context window")
    keys = [c.key for c in cs]
    check(len(set(keys)) == len(keys), "candidate keys are unique")
    locals_ = [c for c in cs if c.role == tiers.LOCAL_WORKER]
    check(all(c.unmetered and c.cost_class == 0 for c in locals_),
          "locally served models are unmetered and cost class 0")
    check(all(not c.unmetered for c in cs if c.role != tiers.LOCAL_WORKER),
          "hosted models are metered")

    # A markdown policy file still works, and falls back to derived capabilities.
    r = roster_mod.parse(FIXTURE_ROSTER)
    rc = cand_mod.build(r, CFG, catalog_mod.Catalog())
    check(len(rc) >= 7, "a policy file yields candidates too", str(len(rc)))
    check(any(not c.declared("capabilities") for c in rc),
          "and marks those capabilities as derived, not declared")


# ------------------------------------------------------------ eligibility --
def test_eligibility_rules() -> None:
    cs = cands()
    base = {c["name"]: c for c in CASES}["local-default"]["judgments"]

    el, ex = elig_mod.filter_candidates(cs, base, CFG, harnesses=HARNESSES)
    rules_fired = {e.rule for e in ex}
    check(all(e.reason for e in ex), "every exclusion carries a reason")
    check("planning-role-for-planning-only" in rules_fired,
          "planning roles are excluded from doing the work", str(rules_fired))
    check("specialist-not-permitted" in rules_fired, "the specialist is gated off")

    # a harness that is not installed is not a candidate
    _, ex2 = elig_mod.filter_candidates(cs, base, CFG, harnesses={"pi"})
    check(any(e.rule == "harness-missing" for e in ex2), "a missing harness excludes")
    check(all(c.model.kind == "pi"
              for c in elig_mod.filter_candidates(cs, base, CFG, harnesses={"pi"})[0]),
          "only installed harnesses survive")

    # a measured context bigger than a window excludes that candidate as a fact
    facts = {"total_lines": 20_000}       # ~200K tokens
    el3, ex3 = elig_mod.filter_candidates(cs, base, CFG, facts=facts, harnesses=HARNESSES)
    check(any(e.rule == "context-too-small" for e in ex3), "a too-small window excludes")
    check(all(c.context_tokens >= 200_000 for c in el3),
          "and only windows that hold the task survive")

    # policy guards on quantized local models
    hr = {**base, "high_risk_domain": 0.95}
    _, ex4 = elig_mod.filter_candidates(cs, hr, CFG, harnesses=HARNESSES)
    check(any(e.rule == "quantized-not-for-high-risk" for e in ex4),
          "high-risk work is kept off quantized models")
    nt = {**base, "independently_testable": 0.1}
    _, ex5 = elig_mod.filter_candidates(cs, nt, CFG, harnesses=HARNESSES)
    check(any(e.rule == "quantized-needs-acceptance-criteria" for e in ex5),
          "unverifiable work is kept off quantized models")
    pl = {**base, "task_kind": "orchestrate"}
    _, ex6 = elig_mod.filter_candidates(cs, pl, CFG, harnesses=HARNESSES)
    check(any(e.rule == "quantized-not-for-planning" for e in ex6),
          "planning is kept off quantized models")

    # --failed removes a target by key, role or identifier
    for target in ("vendor:worker", "WORKER", "vendor-worker"):
        el7, _ = elig_mod.filter_candidates(cs, base, CFG, failed={target},
                                            harnesses=HARNESSES)
        check(all(c.key != "vendor:worker" for c in el7),
              f"--failed {target} removes it")


# ---------------------------------------------------------------- scoring --
def test_scoring_is_deterministic() -> None:
    base = {c["name"]: c for c in CASES}["local-default"]["judgments"]
    first = route_it(base)
    for _ in range(5):
        shuffled = cands()
        random.shuffle(shuffled)
        again = scoring.decide(shuffled, base, CFG, harnesses=HARNESSES)
        check([s.key for s in again.ranked] == [s.key for s in first.ranked],
              "input order never changes the ranking")
        check(again.winner.score == first.winner.score, "nor the score")


def test_shortfall_beats_cost() -> None:
    """A hard task must not be given to a cheap weak model to save money."""
    hard = {c["name"]: c for c in CASES}["worker-escalation-by-difficulty"]["judgments"]
    d = route_it(hard)
    check(d.winner.candidate.capability(d.family) >= 0.80,
          "hard work goes to a capable model",
          f"{d.winner.key} cap {d.winner.candidate.capability(d.family):.2f}")
    check(not d.winner.candidate.quantized, "and not to a quantized local model")


def test_cheapest_capable() -> None:
    """Among candidates that clear the bar, the cheaper one wins."""
    easy = {c["name"]: c for c in CASES}["local-default"]["judgments"]
    d = route_it(easy)
    check(d.winner.candidate.unmetered, "easy work goes to unmetered local capacity",
          d.winner.key)
    strong = next(s for s in d.ranked if s.key == "vendor:strong")
    check(d.winner.score > strong.score, "over a stronger, costlier model")


def test_breadth_widens_never_escalates() -> None:
    """THE load-bearing rule, now arithmetic rather than a special case.

    A wide but low-reasoning task must land on a wide LOCAL model, never on a
    hosted escalation. Context is a fact; difficulty is what escalates.
    """
    base = {c["name"]: c for c in CASES}["local-default"]["judgments"]
    wide = {**base, "context_breadth": 1.0}
    d = route_it(wide)
    check(d.winner.candidate.role == tiers.LOCAL_WORKER,
          "a wide low-reasoning sweep stays local", d.winner.key)
    check(d.winner.candidate.context_tokens >= 131_072,
          "on a wide-context model", str(d.winner.candidate.context_tokens))

    # and the whole sweep of breadths never leaves the local role
    seen = set()
    for breadth in (0.0, 0.25, 0.5, 0.75, 1.0):
        w = route_it({**base, "context_breadth": breadth}).winner
        check(w.candidate.role == tiers.LOCAL_WORKER,
              f"breadth {breadth} stays local", w.key)
        seen.add(w.key)
    check(len(seen) > 1, "but breadth does change which local model", str(seen))


def test_smallest_sufficient_context() -> None:
    """Widening is for scope that does not fit, not a default."""
    base = {c["name"]: c for c in CASES}["local-default"]["judgments"]
    d = route_it(base, facts={"total_lines": 400})          # ~4K tokens
    fitting = [s for s in d.ranked if s.candidate.role == tiers.LOCAL_WORKER]
    smallest = min(fitting, key=lambda s: s.candidate.context_tokens)
    check(d.winner.key == smallest.key,
          "a small task takes the smallest sufficient window",
          f"{d.winner.key} ({d.winner.candidate.context_tokens}) vs {smallest.key}")


def test_high_risk_reviewer() -> None:
    base = {c["name"]: c for c in CASES}["local-default"]["judgments"]
    d = route_it({**base, "high_risk_domain": 0.95, "reasoning_complexity": 0.6})
    check(d.reviewer is not None, "high-risk work gets a reviewer")
    if d.reviewer:
        check(d.reviewer.key != d.winner.key, "a different candidate")
        check(d.reviewer.candidate.identifier != d.winner.candidate.identifier,
              "and a different model")
        check(d.reviewer.candidate.capability("debugging")
              >= d.winner.candidate.capability("debugging"),
              "at least as strong at review")
    check(not route_it(base).reviewer, "ordinary work gets no reviewer")


def test_specialist_is_never_implicit() -> None:
    base = {c["name"]: c for c in CASES}["specialist-allowed"]["judgments"]
    check(route_it(base).winner.candidate.role != tiers.SPECIALIST,
          "the specialist is unreachable without the flag")
    d = route_it(base, allow_specialist=True)
    check(any(s.candidate.role == tiers.SPECIALIST for s in d.ranked),
          "and eligible with it")


def test_planning_routes_to_planning_roles() -> None:
    base = {c["name"]: c for c in CASES}["orchestration"]["judgments"]
    d = route_it(base)
    check(d.winner.candidate.role in tiers.PLANNING_ROLES,
          "planning work goes to a planning role", d.winner.key)
    check(all(not s.candidate.quantized for s in d.ranked),
          "and no quantized model is even a candidate")


def test_no_route_is_explained() -> None:
    base = {c["name"]: c for c in CASES}["local-default"]["judgments"]
    d = scoring.decide(cands(), base, CFG, harnesses=set())
    check(d.winner is None, "no harness means no route")
    check(len(d.exclusions) == 10 and d.notes, "and every exclusion is recorded",
          f"{len(d.exclusions)} exclusions")


def test_close_call_is_surfaced() -> None:
    """A near-tie on high-risk work is flagged but still decided."""
    base = {c["name"]: c for c in CASES}["local-default"]["judgments"]
    found = False
    for need in [x / 100 for x in range(30, 100, 2)]:
        d = route_it({**base, "high_risk_domain": 0.95,
                      "reasoning_complexity": need, "ambiguity": 0.2})
        if d.needs_confirmation:
            found = True
            check(d.winner is not None, "it still commits to a winner")
            check(d.gap is not None and d.gap < CFG["confirm_margin"],
                  "and the gap really is inside the margin", str(d.gap))
            check(any("close call" in n for n in d.notes), "and says so")
            break
    check(found, "a close high-risk call is reachable and flagged")


# ----------------------------------------------------- the project switch --
def test_router_switch() -> None:
    with tempfile.TemporaryDirectory() as d:
        root = Path(d) / "proj"
        (root / "sub").mkdir(parents=True)
        (root / ".git").mkdir()
        check(router_config.enabled(root), "absent flag means routing is on")
        router_config.write(False, root / "sub")
        check(not router_config.enabled(root / "sub"), "writing false turns it off")
        check(router_config.project_root(root / "sub") == root.resolve(),
              "found from a subdirectory")
        router_config.flag_path(root).write_text("{not json")
        state = router_config.read(root)
        check(state["enabled"] and state["source"] == "unreadable",
              "a corrupt flag fails open", str(state))


def test_subagent_alias() -> None:
    for role, expected in ((tiers.WORKER, "sonnet"),
                           (tiers.WORKER_ESCALATION, "opus"),
                           (tiers.SPECIALIST, "fable"),
                           (tiers.LOCAL_WORKER, route.CHEAPEST_SUBAGENT),
                           (tiers.COORDINATION, route.CHEAPEST_SUBAGENT)):
        alias, why = route.subagent_alias(role, "vendor-neutral-name")
        check(alias == expected, f"{role} -> {expected}", f"got {alias} ({why})")
    alias, _ = route.subagent_alias(tiers.WORKER, "some-opus-build")
    check(alias == "opus", "an identifier that states its alias wins over the role")
    for role in tiers.ALL:
        a, _ = route.subagent_alias(role, "x")
        check(a in route.SUBAGENT_ALIASES, f"{role} yields a valid alias", a)


def test_capability_floor() -> None:
    """A model far below what the task needs is excluded, not merely outscored.

    Without this, excluding the strong candidates for any other reason lets a
    weak, cheap one win work it cannot do.
    """
    base = {c["name"]: c for c in CASES}["worker-escalation-by-difficulty"]["judgments"]
    _, ex = elig_mod.filter_candidates(cands(), base, CFG, harnesses=HARNESSES)
    floored = [e for e in ex if e.rule == "below-capability-floor"]
    check(bool(floored), "the floor excludes under-powered candidates")
    d = route_it(base)
    if d.winner:
        need = min(1.0, base["reasoning_complexity"]
                   + CFG["ambiguity_lifts_need"] * base["ambiguity"])
        check(d.winner.candidate.capability(d.family)
              >= need - CFG["max_capability_shortfall"],
              "and the winner clears it", d.winner.key)
    # even with every strong candidate knocked out, a weak one must not win
    d2 = route_it(base, failed={"vendor:worker", "vendor:strong", "vendor:specialist"})
    check(d2.winner is None or not d2.winner.candidate.quantized,
          "knocking out the strong models does not promote a weak one",
          d2.winner.key if d2.winner else "no route")


def test_architecture_guard() -> None:
    """Cross-module contract changes stay off quantized local models."""
    base = {c["name"]: c for c in CASES}["local-default"]["judgments"]
    hi = {**base, "architecture_scope": 0.95}
    _, ex = elig_mod.filter_candidates(cands(), hi, CFG, harnesses=HARNESSES)
    check(any(e.rule == "quantized-not-for-architecture" for e in ex),
          "a high architecture score excludes quantized models")
    # ...but an ordinary signature change must not. The threshold exists so
    # routine plumbing is not treated as owning architecture.
    lo = {**base, "architecture_scope": CFG["architecture_threshold"] - 0.05}
    el, _ = elig_mod.filter_candidates(cands(), lo, CFG, harnesses=HARNESSES)
    check(any(c.quantized for c in el),
          "a score below the threshold leaves local models eligible")


def test_emitted_commands_are_injection_safe() -> None:
    """Printed herdr lines are executable output; catalog data must not reach a shell."""
    import model as model_mod
    bad_kind = model_mod.Model(tier=tiers.WORKER, identifier="x",
                               kind="claude; rm -rf ~", extra_args=("--model", "x"))
    try:
        herdr_state.spawn_commands(bad_kind, "w1", "/tmp")
        check(False, "an unknown harness kind is refused")
    except ValueError as exc:
        check("unknown harness kind" in str(exc), "an unknown harness kind is refused")

    ok = model_mod.Model(tier=tiers.WORKER, identifier="x", kind="claude",
                         extra_args=("--model", "x"))
    for name in ("--help", "a b", "", "-x"):
        try:
            herdr_state.spawn_commands(ok, name, "/tmp")
            check(False, f"worker name {name!r} is refused")
        except ValueError:
            check(True, f"worker name {name!r} is refused")

    lines = herdr_state.spawn_commands(ok, "worker-1", "/tmp/a dir")
    check("'/tmp/a dir'" in " ".join(lines), "a cwd with a space is quoted")


def test_cache_refuses_symlinked_state() -> None:
    """A repository must not be able to redirect our writes outside itself."""
    import os
    with tempfile.TemporaryDirectory() as d:
        os.symlink("/tmp", Path(d) / cache_mod.STATE_DIR)
        try:
            cache_mod.Store.open(d)
            check(False, "a symlinked state directory is refused")
        except cache_mod.CacheError as exc:
            check("symlink" in str(exc), "a symlinked state directory is refused")


def test_cache_makes_routes_repeatable() -> None:
    """Same task, same facts, same questions -> the same stored judgments."""
    with tempfile.TemporaryDirectory() as d:
        store = cache_mod.Store.open(d)
        qsha = cache_mod.questions_fingerprint(rules.QUESTIONS)
        key, tsha, fsha = cache_mod.Store.key("do it", {"total_lines": 7}, qsha, "m")
        check(store.get(key) is None, "a cold cache misses")
        store.put(key, tsha, fsha, qsha, "m", {"task_kind": "implement", "_usage": {"a": 1}})
        got = store.get(key)
        check(got == {"task_kind": "implement"}, "a warm cache replays exactly", str(got))
        check("_usage" not in got, "usage counters are not replayed")

        same, _, _ = cache_mod.Store.key("do   it", {"total_lines": 7}, qsha, "m")
        check(same == key, "whitespace does not change the key")
        for differs, label in (
            (cache_mod.Store.key("do it", {"total_lines": 8}, qsha, "m")[0], "facts"),
            (cache_mod.Store.key("do it", {"total_lines": 7}, "other", "m")[0], "questions"),
            (cache_mod.Store.key("do it", {"total_lines": 7}, qsha, "n")[0], "model"),
        ):
            check(differs != key, f"a different {label} misses the cache")
        store.close()


if __name__ == "__main__":
    for fn in (test_catalog, test_candidates, test_eligibility_rules,
               test_scoring_is_deterministic, test_shortfall_beats_cost,
               test_cheapest_capable, test_breadth_widens_never_escalates,
               test_smallest_sufficient_context, test_high_risk_reviewer,
               test_specialist_is_never_implicit, test_planning_routes_to_planning_roles,
               test_no_route_is_explained, test_close_call_is_surfaced,
               test_router_switch, test_subagent_alias,
               test_capability_floor, test_architecture_guard,
               test_emitted_commands_are_injection_safe,
               test_cache_refuses_symlinked_state,
               test_cache_makes_routes_repeatable):
        fn()
    if failures:
        print(f"FAILED {len(failures)}/{checks} checks\n")
        for f in failures:
            print(f"  - {f}")
        raise SystemExit(1)
    print(f"ok: {checks} checks passed")
