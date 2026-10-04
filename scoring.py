"""Deterministic selection: score eligible candidates, pick the best fit.

No Jev call happens here. Jev characterized the task; this is arithmetic over
that characterization and the candidates' declared capabilities, so a route can
be recomputed, explained term by term, and tested offline.

The shape of the score:

    start at 1.0
      - shortfall   the task needs more capability than this candidate has   (heavy)
      - context     its window is too small for the measured or implied size (heavy)
      - overshoot   it is stronger than the task needs                       (light)
      - oversized   its window is far larger than the task needs              (light)
      - cost        what the route costs, as a class                         (light)
      + unmetered   locally served capacity consumes no subscription
      + horizon     a specialist on genuinely long-horizon work

Shortfall is penalized far harder than overshoot: sending hard work to a weak
model produces bad output, while sending easy work to a strong one only wastes
money. Cost and the unmetered bonus are what implement "use the cheapest
capable model" without ever letting cost beat capability.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import candidates as cand_mod
import eligibility as elig_mod
import tiers

from families import capability_needed, family_for


@dataclass(frozen=True)
class Scored:
    candidate: cand_mod.Candidate
    score: float
    terms: dict[str, float]

    @property
    def key(self) -> str:
        return self.candidate.key

    def as_dict(self) -> dict:
        return {"key": self.key, "score": round(self.score, 4),
                "terms": {k: round(v, 4) for k, v in self.terms.items()},
                "candidate": self.candidate.as_dict()}


@dataclass
class Decision:
    family: str
    need: float
    required_tokens: int
    ranked: list[Scored] = field(default_factory=list)
    exclusions: list[elig_mod.Exclusion] = field(default_factory=list)
    reviewer: Scored | None = None
    needs_confirmation: bool = False
    notes: list[str] = field(default_factory=list)

    @property
    def winner(self) -> Scored | None:
        return self.ranked[0] if self.ranked else None

    @property
    def runner_up(self) -> Scored | None:
        return self.ranked[1] if len(self.ranked) > 1 else None

    @property
    def gap(self) -> float | None:
        if self.runner_up is None:
            return None
        return round(self.ranked[0].score - self.ranked[1].score, 4)


def tokens_needed(judgments: dict, cfg: dict, facts: dict | None) -> int:
    """Context the task needs: measured when files were named, else implied.

    Measured beats implied - a file's line count is a fact and a breadth score
    is an opinion.
    """
    measured = elig_mod.context_required(facts)
    if measured:
        return measured
    breadth = float(judgments.get("context_breadth", 0.0))
    base, span = cfg["implied_tokens_base"], cfg["implied_tokens_span"]
    return int(base * (span ** breadth))


def score_one(c: cand_mod.Candidate, family: str, need: float, required: int,
              judgments: dict, cfg: dict) -> Scored:
    cap = c.capability(family)
    terms: dict[str, float] = {}

    terms["shortfall"] = -cfg["w_shortfall"] * max(0.0, need - cap)
    headroom = c.context_tokens / required if required else 2.0
    terms["context"] = -cfg["w_context"] * max(0.0, min(1.0, 1.0 - headroom))
    terms["overshoot"] = -cfg["w_overshoot"] * max(0.0, cap - need)

    # Widening is for scope that does not fit, not a default. Among candidates
    # that all hold the task, prefer the smallest sufficient window: a bigger
    # one costs load time and memory for nothing.
    excess = (headroom / cfg["oversize_at"]) - 1.0 if required else 0.0
    terms["oversized"] = -cfg["w_oversized"] * max(0.0, min(1.0, excess))

    if c.relative_cost is not None:
        cost = min(1.0, c.relative_cost / cfg["relative_cost_full"])
    else:
        cost = c.cost_class / tiers.MAX_COST
    terms["cost"] = -cfg["w_cost"] * cost

    terms["unmetered"] = cfg["w_unmetered"] if c.unmetered else 0.0
    terms["horizon"] = (
        cfg["w_horizon"]
        if c.role == tiers.SPECIALIST
        and float(judgments.get("long_horizon", 0.0)) >= cfg["long_horizon_threshold"]
        else 0.0
    )
    return Scored(candidate=c, score=round(1.0 + sum(terms.values()), 4), terms=terms)


def decide(all_candidates: list[cand_mod.Candidate], judgments: dict, cfg: dict, *,
           facts: dict | None = None,
           excluded_identifiers: frozenset[str] = frozenset(),
           failed: set[str] | None = None,
           allow_specialist: bool = False,
           harnesses: set[str] | None = None) -> Decision:
    """Filter deterministically, score deterministically, pick the best fit."""
    eligible, exclusions = elig_mod.filter_candidates(
        all_candidates, judgments, cfg, facts=facts,
        excluded_identifiers=excluded_identifiers, failed=failed,
        allow_specialist=allow_specialist, harnesses=harnesses)

    family = family_for(judgments)
    need = capability_needed(judgments, cfg)
    required = tokens_needed(judgments, cfg, facts)

    ranked = [score_one(c, family, need, required, judgments, cfg) for c in eligible]
    # Deterministic order: score, then cheapest, then key. Never dict order.
    ranked.sort(key=lambda s: (-s.score, s.candidate.cost_class, s.candidate.key))

    d = Decision(family=family, need=need, required_tokens=required,
                 ranked=ranked, exclusions=exclusions)

    if not ranked:
        d.notes.append("no candidate survived eligibility; nothing can run this task")
        return d

    high_risk = float(judgments.get("high_risk_domain", 0.0)) >= cfg["high_risk_threshold"]

    # Close call on consequential work: surface it, but still commit.
    if d.gap is not None and d.gap < cfg["confirm_margin"] and high_risk:
        d.needs_confirmation = True
        d.notes.append(
            f"close call ({d.gap:.3f} apart) on high-risk work - confirm before delegating")

    # An independent reviewer: a different candidate, at least as capable at
    # judging the work as the one doing it.
    if high_risk:
        win = ranked[0]
        for s in ranked[1:]:
            if (s.candidate.key != win.candidate.key
                    and s.candidate.identifier != win.candidate.identifier
                    and s.candidate.capability("debugging")
                    >= win.candidate.capability("debugging")):
                d.reviewer = s
                break
        if d.reviewer is None:
            d.notes.append(
                "high-risk work, but no eligible candidate is independent of the "
                "implementer and at least as strong at review")
    return d
