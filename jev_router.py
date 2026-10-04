"""Jev judgments + plain-Python routing rules for model selection.

Division of labour, deliberately strict:
  * Jev answers narrow questions about one task assignment and returns typed
    judgments. It never sees a model name and never decides policy.
  * Everything below RULES is ordinary Python you can edit without re-running
    Jev: thresholds, tier ladder, gates.

All questions go in ONE request per task, so they run in parallel and cannot
see one another's answers.

The rules name abstract tiers only (see tiers.py). roster.py turns a tier into
a concrete identifier, so this file stays free of vendor names.
"""
from __future__ import annotations

import asyncio

from typesafe_sdk import Choice, Noul, RetryPolicy, Score

import tiers

# ---------------------------------------------------------------- questions --
QUESTIONS = {
    "task_kind": Choice(
        instructions="What kind of work does this assignment ask for?",
        criteria={
            "orchestrate": "Planning, decomposing work, assigning it to others, or deciding how pieces fit together",
            "explore": "Finding out how something currently works by reading the codebase; no change is requested",
            "implement": "Writing new behaviour or wiring up a new feature",
            "test": "Writing or fixing tests for behaviour that already exists",
            "refactor": "Restructuring existing code without changing what it does",
            "debug": "Finding and fixing the cause of a failure or wrong behaviour",
            "review": "Judging work someone else produced, for correctness or risk",
            "summarize": "Condensing, reformatting or reporting on material that already exists",
        },
    ),
    "reasoning_complexity": Score(
        instructions=(
            "How hard is the thinking this task requires? Judge the difficulty of "
            "the reasoning, not the amount of typing or the number of files."
        ),
        criteria=[
            "mechanical: the change is fully determined once you see the code; follow an obvious pattern",
            "ordinary: normal programming judgment, familiar problem, one plausible approach",
            "hard: requires designing something, weighing approaches, or inferring behaviour that is not written down",
            "very hard: novel design, subtle interactions, or a cause that resists the obvious explanations",
        ],
    ),
    "context_breadth": Score(
        instructions=(
            "How much code must be read or held in mind at once to do this task?"
        ),
        criteria=[
            "one function or a single short file",
            "one module: a handful of closely related files",
            "several modules that must be changed together",
            "a repository-wide sweep, or files too large to hold at once",
        ],
    ),
    "ambiguity": Score(
        instructions="How clearly specified is this assignment?",
        criteria=[
            "fully specified: the exact change and where to make it are stated",
            "mostly clear: the goal is stated, the details are implied",
            "underspecified: the goal is clear but the approach is open",
            "unclear: what success even looks like has to be worked out first",
        ],
    ),
    # Reasoning complexity already captures whether work is pattern-following;
    # a second mechanical judgment added request cost without changing policy.
    "independently_testable": Noul(
        instructions=(
            "Can whoever does this verify for themselves that they got it right, "
            "against criteria already stated or obvious from the code — a test "
            "that passes, a type that checks, a documented output? Answer no if "
            "judging success needs taste or human review."
        ),
    ),
    "architecture_scope": Noul(
        instructions=(
            "Would doing this change interfaces, contracts or responsibilities "
            "that other modules depend on, rather than only the internals of the "
            "code being edited?"
        ),
    ),
    # This is the policy's mandatory-independent-reviewer list, which is wider
    # wider than "security" alone: a public API contract or a destructive migration needs a
    # second pair of eyes for the same reason auth does.
    "high_risk_domain": Noul(
        instructions=(
            "Does this task touch authentication, authorization or permissions, "
            "a data migration, payments or money movement, concurrency or "
            "locking, a public API contract, destructive or irreversible "
            "operations, secret handling, or deployment configuration?"
        ),
    ),
    "long_horizon": Noul(
        instructions=(
            "Is this an exceptionally large piece of work — many hours of "
            "continuous autonomous effort across a great many files, such as a "
            "repository-scale migration — rather than something finishable in one "
            "sitting?"
        ),
    ),
}

SCORE_QUESTIONS = {k: q for k, q in QUESTIONS.items() if isinstance(q, Score)}
NOUL_QUESTIONS = {k: q for k, q in QUESTIONS.items() if isinstance(q, Noul)}


def _state(task: str, facts: dict | None, attempts: list[dict] | None) -> dict:
    """The state Jev sees: the assignment and measured facts.

    No model names, no tier names, no hint of the desired verdict - exactly as
    email-triage withholds triage hints. `attempts` carries only what went
    wrong, never which model it was, so Jev cannot anchor on a previous choice.
    """
    state: dict = {"assignment": task}
    if facts:
        state["repository_facts"] = facts
    if attempts:
        state["previous_attempts"] = [
            {"outcome": "failed", "what_went_wrong": a.get("reason") or "unspecified"}
            for a in attempts
        ]
    return state


async def judge(client, task: str, facts: dict | None = None,
                attempts: list[dict] | None = None, *, model: str | None = None,
                cfg: dict | None = None) -> dict:
    """Ask Jev every question about one assignment, in a single request."""
    settings = DEFAULTS if cfg is None else {**DEFAULTS, **cfg}
    operation_timeout = float(settings["jev_timeout_seconds"])
    total_timeout = float(settings["jev_total_timeout_seconds"])
    try:
        async with asyncio.timeout(total_timeout):
            resp = await client.system_one(
                state=_state(task, facts, attempts),
                questions=QUESTIONS,
                model=model,
                retry=RetryPolicy(max_retries=3),
                timeout=operation_timeout,
            )
    except TimeoutError as exc:
        raise RuntimeError(
            "Jev judgment timed out "
            f"({operation_timeout:g}s per operation, "
            f"{total_timeout:g}s total)"
        ) from exc

    def norm(qid: str) -> float:
        """Score runs 0..len(criteria)-1; normalize to 0..1 so thresholds mean what they say."""
        return resp.scores[qid].score / (len(SCORE_QUESTIONS[qid].criteria) - 1)

    out = {
        "task_kind": resp.choices["task_kind"].choice,
        "task_kind_confidence": resp.choices["task_kind"].confidence,
    }
    for qid in SCORE_QUESTIONS:
        out[qid] = norm(qid)
        out[f"{qid}_confidence"] = resp.scores[qid].confidence
    for qid in NOUL_QUESTIONS:
        out[qid] = resp.nouls[qid].noul
    out["_usage"] = {"input_tokens": resp.usage.input_tokens,
                     "output_tokens": resp.usage.output_tokens}
    out["_model"] = resp.model
    return out


# -------------------------------------------------------------------- rules --
# Plain Python from here down, and no routing decision at all: selection moved to
# scoring.py, which is pure arithmetic over these judgments. What stays here is
# the thresholds the deterministic filters and the scorer read, plus the two
# helpers that report how firm a judgment was.

DEFAULTS = {
    # --- eligibility gates (hard, deterministic) -------------------------
    "architecture_threshold": 0.80,   # a signature change is not an architecture change  # cross-module contracts need a full model
    "high_risk_threshold": 0.60,      # auth, migrations, payments, public APIs, ...
    "testable_threshold": 0.60,       # a quantized local model needs clear criteria
    "long_horizon_threshold": 0.70,

    # Bound individual attempts and the complete retrying request separately.
    "jev_timeout_seconds": 20.0,
    "jev_total_timeout_seconds": 45.0,

    # --- how the task's requirement is derived ---------------------------
    "ambiguity_lifts_need": 0.25,     # an underspecified task demands more capability
    # A candidate this far below what the task needs is excluded outright, so
    # no amount of cost pressure, nor a --failed exclusion elsewhere, can push
    # hard work onto a model that cannot do it.
    "max_capability_shortfall": 0.15,
    "implied_tokens_base": 8_000,     # context implied by breadth 0.0
    "implied_tokens_span": 32,        # breadth 1.0 implies base * span tokens

    # --- scoring weights -------------------------------------------------
    # Shortfall dominates: too weak produces bad work, too strong only wastes
    # money. Cost and the unmetered bonus implement "cheapest capable" without
    # ever letting price beat capability.
    "w_shortfall": 1.20,
    "w_context": 0.90,
    "w_overshoot": 0.15,
    "w_cost": 0.20,
    "w_oversized": 0.10,              # prefer the smallest sufficient context
    "oversize_at": 8.0,               # headroom beyond this multiple is "oversized"
    "w_unmetered": 0.08,
    "w_horizon": 0.10,
    "relative_cost_full": 3.0,        # relativeCost treated as 1.0 at this value

    # --- reporting -------------------------------------------------------
    "confirm_margin": 0.05,           # closer than this, on high-risk work, ask
    "near_margin": 0.10,              # report a gate this close to flipping
}

# Signals whose confidence is worth reporting, because they move the score.
DECISIVE = ("task_kind", "reasoning_complexity", "context_breadth", "ambiguity")


def confidence(j: dict, *signals: str) -> float:
    """Confidence in the signals that drove the score.

    Only scores and the choice carry a confidence of their own; the weakest of
    them sets the number, because that is the one that could flip the verdict.
    A noul is deliberately excluded: it is a probability, and a noul near 0.5 is
    a confident "genuinely borderline", not an uncertain answer. Borderline
    nouls are reported by near_thresholds instead.
    """
    keys = signals or DECISIVE
    vals = [float(j[f"{s}_confidence"]) for s in keys if f"{s}_confidence" in j]
    return round(min(vals), 2) if vals else round(float(j.get("task_kind_confidence", 0.0)), 2)


def near_thresholds(j: dict, t: dict) -> list[str]:
    """Eligibility gates close enough to their threshold that they could flip."""
    margin = t["near_margin"]
    pairs = (
        ("architecture_scope", "architecture_threshold"),
        ("high_risk_domain", "high_risk_threshold"),
        ("independently_testable", "testable_threshold"),
        ("long_horizon", "long_horizon_threshold"),
    )
    return [f"{k} {j[k]:.2f} vs threshold {t[thr]:.2f}"
            for k, thr in pairs
            if k in j and abs(float(j[k]) - t[thr]) <= margin]
