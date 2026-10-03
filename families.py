"""Which capability a task family draws on.

Its own module because both eligibility (the capability floor) and scoring (the
fit term) need it, and eligibility must not import scoring.
"""
from __future__ import annotations

FAMILY_FOR_KIND = {
    "orchestrate": "planning",
    "explore": "research",
    "summarize": "research",
    "implement": "coding",
    "test": "coding",
    "refactor": "coding",
    "debug": "debugging",
    "review": "debugging",
}
DEFAULT_FAMILY = "coding"


def family_for(judgments: dict) -> str:
    return FAMILY_FOR_KIND.get(judgments.get("task_kind", ""), DEFAULT_FAMILY)
