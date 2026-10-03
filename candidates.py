"""Enumerate the concrete, launchable candidates a roster offers.

A candidate is one (model, effort) pair that could actually be started: every
role row from the policy file, plus every local alias as its own candidate.
That last part matters - because the local aliases compete separately, a task
too wide for the small one is excluded from it by the context-window fact in
eligibility.py and the wider alias wins on its merits. Widening the alias is no
longer a special rule; it falls out of the candidate set.

Each candidate carries a capability vector over task families
(planning, coding, debugging, creativity, research) plus operational facts:

    capabilities  declared by models.json, else derived from the role ordering
    context       models.json, else the policy file's Context column, else assumed
    reasons       the policy file's Reasoning column
    unmetered     locally served, so no subscription capacity is consumed
    quantized     locally served, so quality is bounded (the policy says so)
    cost_class    role ordering; 0 for unmetered

`facts` records, per field, whether the number was declared or assumed, so a
decision card never passes off a guess as a fact.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

import catalog as catalog_mod
import model as model_mod
import tiers

# How a role's single strength spreads over the task families, when no catalog
# declares otherwise. Deltas, not absolutes: the shape of the role, applied to
# whatever strength the role ordering gives it.
ROLE_SHAPE: dict[str, dict[str, float]] = {
    tiers.COORDINATION: {"planning": -0.10, "coding": -0.10, "debugging": -0.10,
                         "creativity": -0.05, "research": +0.10},
    # The policy is explicit that a quantized local model must not own
    # architecture, so its planning capability is deliberately below its coding.
    tiers.LOCAL_WORKER: {"planning": -0.20, "coding": +0.05, "debugging": -0.05,
                         "creativity": -0.10, "research": 0.0},
    tiers.ORCHESTRATION: {"planning": +0.10, "coding": -0.15, "debugging": -0.10,
                          "creativity": 0.0, "research": +0.05},
    tiers.ORCHESTRATION_ESCALATION: {"planning": +0.03, "coding": -0.15,
                                     "debugging": 0.0, "creativity": 0.0,
                                     "research": +0.03},
    tiers.WORKER: {"planning": -0.05, "coding": 0.0, "debugging": 0.0,
                   "creativity": -0.05, "research": -0.05},
    tiers.WORKER_ESCALATION: {"planning": 0.0, "coding": 0.0, "debugging": +0.03,
                              "creativity": 0.0, "research": 0.0},
    tiers.SPECIALIST: {"planning": 0.0, "coding": +0.05, "debugging": -0.05,
                       "creativity": +0.05, "research": +0.05},
}


@dataclass(frozen=True)
class Candidate:
    key: str                  # stable and unique: "WORKER", "LOCAL_WORKER/wide-context"
    model: model_mod.Model
    capabilities: dict[str, float]
    context_tokens: int
    reasons: bool
    unmetered: bool
    quantized: bool
    cost_class: int
    relative_cost: float | None = None
    supported_efforts: tuple[str, ...] | None = None
    images: bool | None = None
    facts: dict[str, str] = field(default_factory=dict)   # field -> "declared"|"assumed"

    @property
    def role(self) -> str:
        return self.model.tier

    @property
    def identifier(self) -> str:
        return self.model.identifier

    def capability(self, family: str) -> float:
        return self.capabilities.get(family, min(self.capabilities.values(), default=0.5))

    def declared(self, field_name: str) -> bool:
        return self.facts.get(field_name) == "declared"

    def as_dict(self) -> dict:
        return {
            "key": self.key,
            "role": self.role,
            "model": self.model.as_dict(),
            "capabilities": {k: round(v, 3) for k, v in sorted(self.capabilities.items())},
            "context_tokens": self.context_tokens,
            "reasons": self.reasons,
            "unmetered": self.unmetered,
            "quantized": self.quantized,
            "cost_class": self.cost_class,
            "relative_cost": self.relative_cost,
            "supported_efforts": list(self.supported_efforts or ()),
            "facts": dict(self.facts),
        }


def _context_tokens(context: str | None) -> tuple[int, bool]:
    """Parse '64K' / '262K' / '1M' into tokens. Returns (tokens, was_stated)."""
    if not context:
        return tiers.ASSUMED_CONTEXT_TOKENS, False
    m = re.search(r"(\d+)\s*([kKmM])", context)
    if m:
        n = int(m.group(1))
        return (n * 1_000_000 if m.group(2).lower() == "m" else n * 1_000), True
    m = re.search(r"(\d{4,})", context)
    if m:
        return int(m.group(1)), True
    return tiers.ASSUMED_CONTEXT_TOKENS, False


def _strength(role: str, reasons: bool, cfg: dict) -> float:
    table = {**tiers.ROLE_STRENGTH, **(cfg.get("role_strength") or {})}
    base = table.get(role, 0.5)
    if role == tiers.LOCAL_WORKER and not reasons:
        # An alias with reasoning off is for mechanical work by its own
        # description, so it is weaker than the reasoning alias beside it.
        base -= cfg.get("no_reasoning_penalty", 0.20)
    return max(0.0, min(1.0, base))


def _capabilities(role: str, strength: float, entry: catalog_mod.Entry | None,
                  facts: dict[str, str]) -> dict[str, float]:
    shape = ROLE_SHAPE.get(role, {})
    caps = {fam: max(0.0, min(1.0, strength + shape.get(fam, 0.0)))
            for fam in catalog_mod.FAMILIES}
    declared = entry.capabilities if entry else {}
    caps.update(declared)
    facts["capabilities"] = "declared" if len(declared) == len(catalog_mod.FAMILIES) \
        else ("partly-declared" if declared else "assumed")
    return caps


def _one(model: model_mod.Model, key: str, role: str, reasons: bool,
         cfg: dict, cat: catalog_mod.Catalog) -> Candidate:
    entry = cat.get(key) or cat.get(model.identifier)
    facts: dict[str, str] = {}

    ctx, stated = _context_tokens(model.context)
    if entry and entry.context_window:
        ctx, stated = entry.context_window, True
    facts["context_tokens"] = "declared" if stated else "assumed"

    strength = _strength(role, reasons, cfg)
    caps = _capabilities(role, strength, entry, facts)

    unmetered = entry.unmetered if (entry and entry.unmetered is not None) \
        else model.kind == "pi"
    quantized = entry.quantized if (entry and entry.quantized is not None) \
        else model.kind == "pi"
    facts["unmetered"] = "declared" if (entry and entry.unmetered is not None) else "assumed"

    cost_class = 0 if unmetered else tiers.ROLE_COST.get(role, 2)

    return Candidate(
        key=key, model=model, capabilities=caps,
        context_tokens=ctx, reasons=reasons,
        unmetered=unmetered, quantized=quantized, cost_class=cost_class,
        relative_cost=entry.relative_cost if entry else None,
        supported_efforts=entry.supported_efforts if entry else None,
        images=(entry.images if entry and entry.images is not None else model.images),
        facts=facts,
    )


def from_catalog(cat: catalog_mod.Catalog, cfg: dict | None = None) -> list[Candidate]:
    """Candidates built from a catalog alone - no policy file needed.

    This is the primary path: a catalog states facts (context window, harness,
    launch arguments, capabilities) instead of a markdown table implying them.
    Every entry carrying a `role` becomes a candidate.
    """
    cfg = cfg or {}
    out: list[Candidate] = []
    for entry in cat.entries.values():
        if not entry.role or entry.rejected:
            continue
        ident = entry.launch_identifier
        kind, args = model_mod.harness_for(ident, entry.effort)
        model = model_mod.Model(
            tier=entry.role, identifier=ident,
            kind=entry.harness or kind,
            extra_args=entry.launch_args or args,
            effort=entry.effort,
            role_label=entry.notes or f"catalog: {entry.id}",
        )
        reasons = True
        if entry.quantized or (entry.harness == "pi"):
            # A local alias's reasoning behaviour is an operator convention, not a
            # server fact, so it is only believed when the catalog states it.
            reasons = True
        out.append(_one(model, entry.id, entry.role, reasons, cfg, cat))
    out.sort(key=lambda c: (c.cost_class, c.capability("coding"), c.key))
    return out


def build(roster, cfg: dict | None = None,
          cat: catalog_mod.Catalog | None = None) -> list[Candidate]:
    """Every launchable candidate from a policy file, in a deterministic order."""
    cfg = cfg or {}
    cat = cat or catalog_mod.Catalog()
    out: list[Candidate] = []

    for role in tiers.ALL:
        if role == tiers.LOCAL_WORKER:
            continue                       # handled per alias below
        model = roster.tiers.get(role)
        if model:
            out.append(_one(model, role, role, True, cfg, cat))

    seen: set[str] = set()
    for context_class, model in roster.local_aliases.items():
        if model.identifier in seen:
            continue                       # a roster may map two classes to one alias
        seen.add(model.identifier)
        reasons = (model.reasoning or "").strip().lower() not in {
            "no", "false", "none", "-", ""}
        out.append(_one(model, f"{tiers.LOCAL_WORKER}/{context_class}",
                        tiers.LOCAL_WORKER, reasons, cfg, cat))

    # A catalog entry carrying a `role` the policy file does not fill can stand
    # on its own, so a catalog is usable without a markdown policy file.
    covered = {c.identifier for c in out}
    for entry in cat.entries.values():
        if not entry.role or entry.rejected or entry.launch_identifier in covered:
            continue
        kind, args = model_mod.harness_for(entry.id)
        model = model_mod.Model(
            tier=entry.role, identifier=entry.id,
            kind=entry.harness or kind,
            extra_args=entry.launch_args or args,
            role_label=f"catalog ({cat.source.name if cat.source else 'inline'})",
        )
        out.append(_one(model, f"{entry.role}/catalog/{entry.id}",
                        entry.role, True, cfg, cat))

    out.sort(key=lambda c: (c.cost_class, c.capability("coding"), c.identifier))
    return out
