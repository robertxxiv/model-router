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


def capability_needed(judgments: dict, cfg: dict) -> float:
    """How much capability the task demands.

    Reasoning difficulty sets the bar; ambiguity raises it, because an
    underspecified task has to be figured out before it can be done.

    Defined here, beside `family_for`, because the hard capability floor in
    eligibility.py and the shortfall term in scoring.py must agree on the
    number. Two copies of this formula could drift apart, and a floor that
    disagreed with the scorer would exclude a candidate the score preferred.
    """
    need = float(judgments.get("reasoning_complexity", 0.5))
    need += cfg["ambiguity_lifts_need"] * float(judgments.get("ambiguity", 0.0))
    return max(0.0, min(1.0, need))
