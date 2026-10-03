"""Optional models.json catalog: the facts a markdown policy file cannot state.

The policy file says *which model fills which role* - that is the operator's
decision and stays the source of truth. It cannot say how big a model's context
window is, which reasoning efforts it accepts, or how good it is at debugging
versus planning. A catalog supplies those, joined onto the roster by identifier.

    {
      "updatedAt": "2026-10-02",
      "provenance": "where these numbers came from, and how much to trust them",
      "models": [
        {
          "id": "vendor-large",            // matches the policy file's identifier
          "harness": "claude",             // claude | codex | pi | ...
          "launchArgs": ["--model", "vendor-large"],
          "contextWindow": 200000,
          "supportedEfforts": ["low", "medium", "high"],
          "capabilities": {"planning": 0.8, "coding": 0.8, "debugging": 0.8,
                           "creativity": 0.7, "research": 0.75},
          "relativeCost": 1,
          "unmetered": false,
          "role": "WORKER"                 // optional: lets a catalog stand alone
        }
      ]
    }

Everything is optional. An entry fills in what it states and leaves the rest to
the role defaults, and a candidate records which of the two it got its numbers
from, so a decision card can show whether a figure was declared or assumed.

Shape borrowed from nidhi-singh02/agent-router's packages/router/config/models.json.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import tiers

FAMILIES = ("planning", "coding", "debugging", "creativity", "research")
HARNESSES = frozenset({"claude", "codex", "pi"})
DEFAULT_CATALOG_NAME = "models.json"


class CatalogError(Exception):
    """The catalog file exists but could not be used."""


@dataclass(frozen=True)
class Entry:
    id: str                              # catalog key, unique; need not be the model name
    model: str | None = None             # the identifier the harness is given
    effort: str | None = None            # so one model can serve two roles
    harness: str | None = None
    launch_args: tuple[str, ...] | None = None
    context_window: int | None = None
    supported_efforts: tuple[str, ...] | None = None
    capabilities: dict[str, float] = field(default_factory=dict)
    relative_cost: float | None = None
    unmetered: bool | None = None
    quantized: bool | None = None
    role: str | None = None
    images: bool | None = None
    notes: str = ""
    rejected: bool = False               # "rejected": true, or "enabled": false

    @property
    def launch_identifier(self) -> str:
        """What to pass to the harness: `model` when given, else the catalog key."""
        return self.model or self.id


@dataclass
class Catalog:
    source: Path | None = None
    entries: dict[str, Entry] = field(default_factory=dict)
    provenance: str = ""
    updated_at: str = ""
    warnings: list[str] = field(default_factory=list)

    def get(self, identifier: str) -> Entry | None:
        return self.entries.get(identifier)

    @property
    def rejected(self) -> frozenset[str]:
        """Identifiers the catalog marks unusable, by key and by launch name."""
        out: set[str] = set()
        for e in self.entries.values():
            if e.rejected:
                out.add(e.id)
                out.add(e.launch_identifier)
        return frozenset(out)

    def as_dict(self) -> dict:
        return {
            "source": str(self.source) if self.source else None,
            "updated_at": self.updated_at,
            "provenance": self.provenance,
            "models": sorted(self.entries),
            "warnings": list(self.warnings),
            "rejected": sorted(self.rejected),
        }


def _num(v, label: str, warnings: list[str], lo=0.0, hi=1.0) -> float | None:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        warnings.append(f"{label}: {v!r} is not a number; ignored")
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        warnings.append(f"{label}: {v!r} is not a number; ignored")
        return None
    if not lo <= f <= hi:
        warnings.append(f"{label}: {f} is outside {lo}..{hi}; ignored")
        return None
    return f


def _text(v, label: str, warnings: list[str]) -> str | None:
    if v is None:
        return None
    if not isinstance(v, str):
        warnings.append(f"{label} must be a string; ignored")
        return None
    return v or None


def _boolean(v, label: str, warnings: list[str]) -> bool | None:
    # JSON strings such as "false" are truthy in Python and must never become
    # policy merely because bool() accepts them.
    if v is None:
        return None
    if not isinstance(v, bool):
        warnings.append(f"{label} must be a boolean; ignored")
        return None
    return v


def parse(path: Path | str | None, *, required: bool = False) -> Catalog:
    """Load a catalog. A missing file is fine unless explicitly required."""
    if path is None:
        return Catalog()
    p = Path(path)
    if not p.exists():
        if required:
            raise CatalogError(f"no catalog at {p}")
        return Catalog()
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CatalogError(f"could not read the catalog at {p}: {exc}") from exc

    if not isinstance(raw, dict):
        raise CatalogError(
            f"{p} must contain a JSON object with a \"models\" array."
        )
    models = raw.get("models")
    if not isinstance(models, list):
        raise CatalogError(
            f"{p} has no \"models\" array. See models.example.json for the shape."
        )

    cat = Catalog(source=p)
    cat.provenance = _text(raw.get("provenance"), "provenance", cat.warnings) or ""
    cat.updated_at = _text(raw.get("updatedAt"), "updatedAt", cat.warnings) or ""
    for i, m in enumerate(models):
        if not isinstance(m, dict):
            cat.warnings.append(f"models[{i}] must be an object; skipped")
            continue
        if not isinstance(m.get("id"), str) or not m["id"]:
            cat.warnings.append(f"models[{i}] has no string \"id\"; skipped")
            continue
        ident = m["id"]

        role = _text(m.get("role"), f"{ident}.role", cat.warnings)
        if role is not None:
            role = role.upper().replace("-", "_")
            if role not in tiers.ALL:
                cat.warnings.append(
                    f"{ident}.role: unknown role {role!r}; entry skipped")
                continue

        caps: dict[str, float] = {}
        raw_caps = m.get("capabilities")
        if raw_caps is not None and not isinstance(raw_caps, dict):
            cat.warnings.append(f"{ident}.capabilities must be an object; ignored")
        elif raw_caps:
            for fam, v in raw_caps.items():
                if fam not in FAMILIES:
                    cat.warnings.append(
                        f"{ident}: unknown capability {fam!r}; "
                        f"known: {', '.join(FAMILIES)}")
                    continue
                got = _num(v, f"{ident}.capabilities.{fam}", cat.warnings)
                if got is not None:
                    caps[fam] = got

        ctx = m.get("contextWindow")
        if ctx is not None:
            ctx = _num(ctx, f"{ident}.contextWindow", cat.warnings, lo=1, hi=100_000_000)
            ctx = int(ctx) if ctx else None

        cost = m.get("relativeCost")
        if cost is not None:
            cost = _num(cost, f"{ident}.relativeCost", cat.warnings, lo=0, hi=1000)

        args = m.get("launchArgs")
        if args is not None and not (isinstance(args, list)
                                     and all(isinstance(a, str) for a in args)):
            cat.warnings.append(f"{ident}.launchArgs must be a list of strings; ignored")
            args = None

        efforts = m.get("supportedEfforts")
        if efforts is not None and not (isinstance(efforts, list)
                                        and all(isinstance(e, str) for e in efforts)):
            cat.warnings.append(f"{ident}.supportedEfforts must be a list of strings; ignored")
            efforts = None

        harness_field = "harness" if "harness" in m else "agent"
        harness = _text(m.get(harness_field), f"{ident}.{harness_field}", cat.warnings)
        if harness is not None and harness not in HARNESSES:
            cat.warnings.append(
                f"{ident}.{harness_field}: unknown harness {harness!r}; ignored")
            harness = None

        unmetered = _boolean(m.get("unmetered"), f"{ident}.unmetered", cat.warnings)
        quantized = _boolean(m.get("quantized"), f"{ident}.quantized", cat.warnings)
        images = _boolean(m.get("images"), f"{ident}.images", cat.warnings)
        rejected = _boolean(m.get("rejected"), f"{ident}.rejected", cat.warnings)
        enabled = _boolean(m.get("enabled"), f"{ident}.enabled", cat.warnings)

        if ident in cat.entries:
            cat.warnings.append(f"{ident} appears more than once; the last entry wins")

        cat.entries[ident] = Entry(
            id=ident,
            model=_text(m.get("model"), f"{ident}.model", cat.warnings),
            effort=_text(m.get("effort"), f"{ident}.effort", cat.warnings),
            notes=_text(m.get("notes"), f"{ident}.notes", cat.warnings) or "",
            harness=harness,
            launch_args=tuple(args) if args else None,
            context_window=ctx,
            supported_efforts=tuple(efforts) if efforts else None,
            capabilities=caps,
            relative_cost=cost,
            unmetered=unmetered,
            quantized=quantized,
            role=role,
            images=images,
            rejected=rejected is True or enabled is False,
        )
    return cat


def default_path(project_dir: Path | str | None = None) -> Path:
    return Path(project_dir or Path(__file__).parent) / DEFAULT_CATALOG_NAME
