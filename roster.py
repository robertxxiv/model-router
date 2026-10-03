"""Parse a DEVELOPMENT_TEAM.md-shaped policy file into a typed model roster.

The router hardcodes no model identifier. It reads the operator's own policy
file at run time and normalizes it into the abstract tiers of `tiers.py`, so
the routing rules can speak about WORKER and LOCAL_WORKER while the concrete
identifiers stay on the operator's machine and out of this repository.

Two sections are required, located by heading:

    ## Verified runtime model identifiers
    | Role | Identifier | Where it is set |

    ## Local worker model selection
    | Alias | Context | Reasoning | Images | Choose it when |

Parsing is strict: a missing or renamed heading raises RosterError naming the
section it looked for, rather than falling back to a guess. See
tests/fixtures/DEVELOPMENT_TEAM.example.md for the exact shape expected.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import tiers
from model import Model, harness_for  # noqa: F401  (re-exported)

DEFAULT_ROSTER_PATH = Path.home() / ".config" / "herdr" / "DEVELOPMENT_TEAM.md"

IDENTIFIERS_SECTION = ("verified", "identifiers")
LOCAL_SECTION = ("local", "worker", "selection")


class RosterError(Exception):
    """The policy file could not be parsed into a usable roster."""


@dataclass
class Roster:
    source: Path
    tiers: dict[str, Model] = field(default_factory=dict)
    local_aliases: dict[str, Model] = field(default_factory=dict)
    excluded: frozenset[str] = frozenset()
    warnings: list[str] = field(default_factory=list)

    def resolve(self, tier: str, context_class: str | None = None) -> Model:
        """The model for a tier, honoring the local worker's context classes."""
        if tier == tiers.LOCAL_WORKER and self.local_aliases:
            cc = context_class or "default"
            if cc in self.local_aliases:
                return self.local_aliases[cc]
            if "default" in self.local_aliases:
                return self.local_aliases["default"]
        if tier not in self.tiers:
            raise RosterError(
                f"the policy file at {self.source} defines no model for tier {tier}; "
                f"it defines {sorted(self.tiers)}"
            )
        return self.tiers[tier]

    def as_dict(self) -> dict:
        return {
            "source": str(self.source),
            "tiers": {t: m.as_dict() for t, m in self.tiers.items()},
            "local_aliases": {c: m.as_dict() for c, m in self.local_aliases.items()},
            "excluded": sorted(self.excluded),
            "warnings": list(self.warnings),
        }


# --------------------------------------------------------------- md plumbing --
def _sections(text: str) -> list[tuple[str, str]]:
    """Split markdown into (heading text, body) pairs."""
    out: list[tuple[str, str]] = []
    heading, buf = "", []
    for line in text.splitlines():
        if re.match(r"^#{1,6}\s", line):
            out.append((heading, "\n".join(buf)))
            heading, buf = line.lstrip("#").strip(), []
        else:
            buf.append(line)
    out.append((heading, "\n".join(buf)))
    return out


def _find_section(text: str, keywords: tuple[str, ...]) -> str:
    for heading, body in _sections(text):
        low = heading.lower()
        if all(k in low for k in keywords):
            return body
    raise RosterError(
        "could not find the section whose heading contains "
        + ", ".join(repr(k) for k in keywords)
        + ". The policy file must keep that heading for the roster to be parsed; "
          "see tests/fixtures/DEVELOPMENT_TEAM.example.md."
    )


def _table(body: str, section: str) -> list[dict[str, str]]:
    """Parse the first pipe table in `body` into a list of header->cell dicts."""
    rows = [l.strip() for l in body.splitlines() if l.strip().startswith("|")]
    if len(rows) < 3:
        raise RosterError(
            f"the {section!r} section has no pipe table with a header, a separator "
            f"and at least one row (found {len(rows)} table line(s))."
        )

    def cells(line: str) -> list[str]:
        return [c.strip() for c in line.strip().strip("|").split("|")]

    header = cells(rows[0])
    if not re.match(r"^[\s|:-]+$", rows[1]):
        raise RosterError(f"the {section!r} table is missing its |---| separator row.")
    parsed = []
    for line in rows[2:]:
        cs = cells(line)
        if len(cs) < len(header):
            cs += [""] * (len(header) - len(cs))
        parsed.append(dict(zip(header, cs)))
    return parsed


def _col(row: dict[str, str], *keywords: str) -> str:
    """Fetch a cell by fuzzy header match, so column renames do not break us."""
    for key, val in row.items():
        low = key.lower()
        if any(k in low for k in keywords):
            return val
    return ""


def _ticked(cell: str) -> list[str]:
    """Backticked tokens, stripped: `foo  ` is a typo, not a different model."""
    return [t.strip() for t in re.findall(r"`([^`]+)`", cell) if t.strip()]


def _first_identifier(cell: str) -> str | None:
    """First backticked token in a cell that looks like a model identifier."""
    for tok in _ticked(cell):
        if re.fullmatch(r"[A-Za-z][\w.+-]*", tok) and not tok.lower().startswith(
            ("http", "~/", "/", "--", "-c")
        ):
            return tok
    return None


def _effort(cell: str) -> str | None:
    m = re.search(r"effort\s*`?([a-z]+)`?", cell, re.I)
    return m.group(1).lower() if m else None


def _yes(cell: str) -> bool | None:
    low = cell.strip().strip("`").lower()
    if low in {"yes", "true", "y"}:
        return True
    if low in {"no", "false", "n", "none", "-"}:
        return False
    return None


def _context_k(cell: str) -> int:
    """Context size in thousands of tokens, 0 when the cell says nothing."""
    m = re.search(r"(\d+)\s*([kKmM])", cell)
    if not m:
        m2 = re.search(r"(\d{4,})", cell)
        return int(m2.group(1)) // 1000 if m2 else 0
    n = int(m.group(1))
    return n * 1000 if m.group(2).lower() == "m" else n


# ---------------------------------------------------------- role -> tier map --
# Checked in order; the first match wins, so the compound roles come first.
_ROLE_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    (tiers.ORCHESTRATION_ESCALATION, ("orchestrat", "escalat")),
    (tiers.COORDINATION, ("coordination",)),
    (tiers.COORDINATION, ("cheap",)),
    (tiers.ORCHESTRATION, ("orchestrat",)),
    (tiers.LOCAL_WORKER, ("local",)),
    (tiers.SPECIALIST, ("specialist",)),
    (tiers.WORKER_ESCALATION, ("escalat",)),
    (tiers.WORKER, ("worker",)),
)


def tier_for_role(role: str) -> str | None:
    low = role.lower()
    for tier, keys in _ROLE_RULES:
        if all(k in low for k in keys):
            return tier
    return None


def _excluded(text: str) -> frozenset[str]:
    """Identifiers the policy explicitly rejects; never emitted by the router."""
    out: set[str] = set()
    for sentence in re.split(r"(?<=[.\n])", text):
        low = sentence.lower()
        if "reject" in low or "do not configure" in low:
            for tok in _ticked(sentence):
                if re.fullmatch(r"[A-Za-z][\w.+-]*", tok):
                    out.add(tok)
    return frozenset(out)


# --------------------------------------------------------------------- parse --
def parse(path: Path | str | None = None) -> Roster:
    path = Path(path) if path else DEFAULT_ROSTER_PATH
    if not path.exists():
        raise RosterError(
            f"no policy file at {path}. Point --roster at a "
            f"DEVELOPMENT_TEAM.md-shaped file, or copy "
            f"tests/fixtures/DEVELOPMENT_TEAM.example.md and edit it."
        )
    text = path.read_text(encoding="utf-8")
    roster = Roster(source=path, excluded=_excluded(text))

    # --- tier table ---------------------------------------------------------
    body = _find_section(text, IDENTIFIERS_SECTION)
    rows = _table(body, "verified runtime model identifiers")
    local_row_default: str | None = None
    for row in rows:
        role = _col(row, "role", "tier")
        cell = _col(row, "identifier", "model")
        tier = tier_for_role(role)
        ident = _first_identifier(cell)
        if not tier or not ident:
            continue
        if ident in roster.excluded:
            continue
        if tier == tiers.LOCAL_WORKER:
            # The local row names its default alias; the aliases themselves are
            # described by the second table.
            local_row_default = ident
            continue
        eff = _effort(cell)
        kind, args = harness_for(ident, eff)
        roster.tiers[tier] = Model(
            tier=tier, identifier=ident, kind=kind, extra_args=args,
            role_label=role, effort=eff, note=_col(row, "where"),
        )

    if tiers.WORKER not in roster.tiers:
        raise RosterError(
            "the roster table defines no default worker row. Expected a Role cell "
            "containing 'worker' with a backticked identifier."
        )

    # --- local alias table --------------------------------------------------
    lbody = _find_section(text, LOCAL_SECTION)
    lrows = _table(lbody, "local worker model selection")
    aliases: list[Model] = []
    for row in lrows:
        ident = _first_identifier(_col(row, "alias", "model"))
        if not ident or ident in roster.excluded:
            continue
        kind, args = harness_for(ident)
        aliases.append(Model(
            tier=tiers.LOCAL_WORKER, identifier=ident, kind=kind, extra_args=args,
            context=_col(row, "context") or None,
            reasoning=_col(row, "reasoning").strip("`") or None,
            images=_yes(_col(row, "image")),
            note=_col(row, "choose", "when", "use"),
        ))
    if not aliases:
        raise RosterError(
            "the 'local worker model selection' table yielded no aliases. Expected "
            "rows whose first column is a backticked alias."
        )
    roster.local_aliases = _classify(aliases, local_row_default, roster.warnings)
    roster.tiers[tiers.LOCAL_WORKER] = roster.local_aliases["default"]
    return roster


def _classify(aliases: list[Model], declared_default: str | None,
              warnings: list[str]) -> dict[str, Model]:
    """Assign the three context classes over however many aliases exist.

    `default`         the alias the policy calls the default
    `wide-context`    the largest context window
    `wide-mechanical` a wide alias that is not expected to reason
    """
    by_ident = {a.identifier: a for a in aliases}
    default = by_ident.get(declared_default or "")
    if default is None:
        default = next(
            (a for a in aliases if "default" in (a.note or "").lower()), aliases[0]
        )
        if declared_default:
            warnings.append(
                f"the roster table calls {declared_default!r} the default local "
                f"alias, but the local alias table does not list it (it lists "
                f"{', '.join(a.identifier for a in aliases)}). Falling back to "
                f"{default.identifier!r}; the two tables have drifted."
            )

    widest = max(aliases, key=lambda a: _context_k(a.context or ""))

    def is_mechanical(a: Model) -> bool:
        reasoning_off = _yes(a.reasoning or "") is False
        return reasoning_off or "mechanical" in (a.note or "").lower()

    mech = next(
        (a for a in aliases if a is not default and is_mechanical(a)),
        None,
    ) or next((a for a in aliases if a is not default), default)

    out = {"default": default, "wide-context": widest, "wide-mechanical": mech}
    # A single-alias roster collapses all three classes onto it, which is correct:
    # there is nothing wider to widen to.
    return {k: (v or default) for k, v in out.items()}
