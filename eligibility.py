"""Deterministic eligibility: which candidates may run this task at all.

Facts and policy only - no scoring, no ranking, no judgment about quality.
Every exclusion carries a reason, so a route can always be explained by what
was ruled out as well as by what won.

Two of these filters need Jev's characterization of the task (is it high-risk,
is it self-verifiable, is it planning). They are still deterministic: given the
same judgments they always exclude the same candidates, and they are the guards
a ranking must never be able to override.
"""
from __future__ import annotations

import shutil
from dataclasses import dataclass

import candidates as cand_mod
import tiers
from families import capability_needed, family_for

# Roughly how many tokens a line of source costs. Only used to turn a measured
# file size into a context requirement, and only when files were named.
TOKENS_PER_LINE = 10

HARNESS_BINARY = {"claude": "claude", "codex": "codex", "pi": "pi"}


@dataclass(frozen=True)
class Exclusion:
    key: str
    rule: str
    reason: str


def available_harnesses() -> set[str]:
    """Which harnesses are actually installed. An operational fact."""
    return {kind for kind, binary in HARNESS_BINARY.items() if shutil.which(binary)}


def context_required(facts: dict | None) -> int | None:
    """Tokens the task plausibly needs, from measured files. None if unmeasured."""
    if not facts:
        return None
    lines = facts.get("total_lines")
    if not lines:
        return None
    return int(lines * TOKENS_PER_LINE)


def filter_candidates(
    all_candidates: list[cand_mod.Candidate],
    judgments: dict,
    cfg: dict,
    *,
    facts: dict | None = None,
    excluded_identifiers: frozenset[str] = frozenset(),
    failed: set[str] | None = None,
    allow_specialist: bool = False,
    harnesses: set[str] | None = None,
) -> tuple[list[cand_mod.Candidate], list[Exclusion]]:
    """Return (eligible, exclusions). Order of `all_candidates` is preserved."""
    failed = failed or set()
    harnesses = available_harnesses() if harnesses is None else harnesses
    needed = context_required(facts)
    planning = judgments.get("task_kind") == "orchestrate"
    family = family_for(judgments)
    need = capability_needed(judgments, cfg)
    floor = need - cfg["max_capability_shortfall"]
    architecture = (
        judgments.get("architecture_scope", 0.0) >= cfg["architecture_threshold"]
    )
    high_risk = judgments.get("high_risk_domain", 0.0) >= cfg["high_risk_threshold"]
    testable = judgments.get("independently_testable", 1.0) >= cfg["testable_threshold"]

    eligible: list[cand_mod.Candidate] = []
    out: list[Exclusion] = []

    def drop(c: cand_mod.Candidate, rule: str, reason: str) -> None:
        out.append(Exclusion(key=c.key, rule=rule, reason=reason))

    for c in all_candidates:
        # --- facts about the model itself ---------------------------------
        if c.identifier in excluded_identifiers:
            drop(c, "rejected-by-policy",
                 f"{c.identifier} is marked rejected in the policy file")
            continue
        if c.model.kind not in harnesses:
            drop(c, "harness-missing",
                 f"the {c.model.kind} harness is not installed on this machine")
            continue
        if needed is not None and needed > c.context_tokens:
            drop(c, "context-too-small",
                 f"the task needs about {needed // 1000}K tokens of context, "
                 f"this model holds {c.context_tokens // 1000}K"
                 + ("" if c.declared("context_tokens") else " (assumed)"))
            continue

        # --- what the operator asked for -----------------------------------
        if c.key in failed or c.role in failed or c.identifier in failed:
            drop(c, "already-failed", "it already failed this task")
            continue
        if c.role == tiers.SPECIALIST and not allow_specialist:
            drop(c, "specialist-not-permitted",
                 "the specialist role is never implicit; pass --allow-specialist")
            continue

        # A model far below what the task demands is not a cheap option, it is
        # the wrong answer. Without this floor, cost pressure - or the stronger
        # candidates being excluded for some other reason - could quietly push
        # hard work onto a weak model.
        cap = c.capability(family)
        if cap < floor:
            drop(c, "below-capability-floor",
                 f"{family} capability {cap:.2f} is more than "
                 f"{cfg['max_capability_shortfall']:.2f} below what this task needs "
                 f"({need:.2f})")
            continue

        # --- policy guards a ranking must never overrule --------------------
        if planning and c.quantized:
            drop(c, "quantized-not-for-planning",
                 "a quantized local model must not own architecture or planning")
            continue
        if architecture and c.quantized:
            drop(c, "quantized-not-for-architecture",
                 "a quantized local model must not own cross-module contract changes")
            continue
        if not planning and c.role in tiers.PLANNING_ROLES:
            drop(c, "planning-role-for-planning-only",
                 "this role orchestrates; it is not a candidate for doing the work")
            continue
        if high_risk and c.quantized:
            drop(c, "quantized-not-for-high-risk",
                 f"high-risk work ({judgments['high_risk_domain']:.0%}) may not go to a "
                 f"quantized local model")
            continue
        if not testable and c.quantized:
            drop(c, "quantized-needs-acceptance-criteria",
                 f"a quantized local model needs verifiable acceptance criteria; this "
                 f"task is {judgments.get('independently_testable', 0):.0%} self-verifiable")
            continue

        eligible.append(c)

    # Planning is the orchestrator's own authority, so planning work goes to a
    # planning role - the mirror of the rule above. Applied only if such a role
    # actually survived, so a catalog without one still routes rather than
    # dead-ending.
    if planning:
        planners = [c for c in eligible if c.role in tiers.PLANNING_ROLES]
        if planners:
            for c in eligible:
                if c.role not in tiers.PLANNING_ROLES:
                    drop(c, "planning-work-needs-a-planning-role",
                         "planning and task assignment belong to an orchestration role")
            eligible = planners

    return eligible, out
