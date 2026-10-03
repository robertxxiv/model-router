"""A launchable model, and how to start it.

Extracted from the old markdown-roster module so nothing in the routing path
depends on parsing a policy document.
"""
from __future__ import annotations

from dataclasses import dataclass


def harness_for(identifier: str, effort: str | None = None) -> tuple[str, tuple[str, ...]]:
    """Fallback (herdr --kind, args after `--`) when the catalog does not say.

    The catalog should state `harness` and `launchArgs`; this only guesses from
    well-known public prefixes so a terse entry still works. Anything
    unrecognized is assumed to be a locally served alias.
    """
    low = identifier.lower()
    if low.startswith("claude"):
        return "claude", ("--model", identifier)
    if low.startswith(("gpt", "o3", "o4", "codex")):
        args = ["-m", identifier]
        if effort:
            args += ["-c", f"model_reasoning_effort={effort}"]
        return "codex", tuple(args)
    return "pi", ("--model", identifier)


@dataclass(frozen=True)
class Model:
    """One launchable (model, effort) pair."""

    tier: str                      # the role it fills
    identifier: str                # what the harness is told
    kind: str                      # herdr --kind
    extra_args: tuple[str, ...]    # args after `--`
    role_label: str = ""
    effort: str | None = None
    context: str | None = None
    reasoning: str | None = None
    images: bool | None = None
    note: str = ""                  # free text from whichever source declared it

    @property
    def label(self) -> str:
        return f"{self.identifier} (effort {self.effort})" if self.effort else self.identifier

    def as_dict(self) -> dict:
        d = {"tier": self.tier, "identifier": self.identifier, "kind": self.kind,
             "extra_args": list(self.extra_args)}
        for k in ("effort", "context", "role_label"):
            if getattr(self, k):
                d[k] = getattr(self, k)
        return d
