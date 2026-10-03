# Architecture

## Purpose and boundary

The router chooses a launchable AI coding model for one task and prints the
Herdr commands needed to start it. It does not start the worker. The routing
boundary is deliberate: Jev characterizes the task, while local Python code
constructs candidates, enforces policy, ranks the survivors, and selects the
winner.

## Routing pipeline

```text
models.json -> candidates -> eligibility -> scoring -> winner (+ reviewer)
                              ^              ^
                              |              |
task + repository facts -> Jev typed task judgments
```

The stages are:

1. `catalog.py` parses declared model facts from `models.json`.
2. `candidates.py` turns each usable catalog entry into one launchable
   `(model, effort)` candidate. Missing facts are derived from role metadata.
3. `eligibility.py` removes candidates that violate operational facts or hard
   policy guards.
4. `scoring.py` computes an arithmetic score for every survivor.
5. The highest score wins. High-risk work may also receive an independent
   reviewer selected from the remaining ranked candidates.

Jev feeds only typed judgments about the task into eligibility and scoring. It
does not construct candidates, apply policy, score models, or choose a winner.

## What Jev is asked

`jev_router.QUESTIONS` contains eight questions. They are sent together in one
`system_one` request, so the questions cannot condition on one another's
answers. `Score` results are normalized from their four anchors to `0.0..1.0`.

| Question ID | Type | What it asks |
| --- | --- | --- |
| `task_kind` | `Choice` | Classifies the work as `orchestrate`, `explore`, `implement`, `test`, `refactor`, `debug`, `review`, or `summarize`. |
| `reasoning_complexity` | `Score` | Rates thinking difficulty from mechanical through ordinary and hard to very hard, separately from typing volume or file count. |
| `context_breadth` | `Score` | Rates how much code must be held in mind, from one function or short file through repository-wide work. |
| `ambiguity` | `Score` | Rates specification clarity from fully specified through unclear. |
| `independently_testable` | `Noul` | Estimates whether the worker can verify success against existing or obvious criteria without taste or human review. |
| `architecture_scope` | `Noul` | Estimates whether the task changes interfaces, contracts, or responsibilities on which other modules depend. |
| `high_risk_domain` | `Noul` | Detects authentication, authorization, permissions, migrations, payments, concurrency, public API contracts, destructive operations, secrets, or deployment configuration. |
| `long_horizon` | `Noul` | Detects many-hour, many-file autonomous work such as a repository-scale migration. |

Jev sees the assignment, measured repository facts when present, and previous
failure descriptions when present. A previous attempt is represented only as
`outcome: failed` plus what went wrong.

Jev is deliberately not told any model name, role, tier, catalog entry, roster,
or desired routing verdict. Previous-attempt data also omits the model that
failed. This prevents verdict leakage: the task characterization cannot anchor
on a proposed model or reverse-engineer the answer the router appears to want.

## Replay and determinism

The Jev call is the only non-deterministic stage. `cache.py` makes repeated
routes replayable by hashing these inputs:

- normalized task text;
- canonicalized repository facts;
- a fingerprint of the exact serialized question definitions; and
- the Jev model identifier.

The component hashes are combined into the cache key. Rewording a question
changes the questions fingerprint and therefore causes a cache miss. Equal
dictionaries serialize with sorted keys, so incidental dictionary ordering
does not alter the key.

Previous failure descriptions are part of the state sent to Jev, but are not
part of the cache key. A cached judgment for the same task, facts, questions,
and Jev model therefore takes precedence over a changed `--failed` reason;
`--failed` still excludes its named candidate locally.

The cache stores `task_sha`, not the raw assignment. The raw task text is never
written to the cache database. Cached judgments omit only the `_usage` field;
the task characterization and Jev model remain available for replay and
reporting.

After judgments are fixed, candidate generation, eligibility, scoring, sorting,
and reviewer selection are ordinary deterministic Python operations.

## Eligibility

Eligibility is a hard boundary. Every rejection records a rule name and an
explanation. No score, cost advantage, unmetered bonus, or lack of alternatives
can overrule an exclusion.

For each candidate, `filter_candidates` evaluates these rules in this exact
order and stops at the first matching rule:

| Order | Emitted rule | Trigger |
| ---: | --- | --- |
| 1 | `rejected-by-policy` | The candidate's launch identifier is in the catalog-derived rejected identifier set. |
| 2 | `harness-missing` | Its harness kind is not among the installed `claude`, `codex`, or `pi` harnesses, unless harness checking was disabled by the caller. |
| 3 | `context-too-small` | Named-file measurements imply `total_lines * 10` tokens and that measured requirement exceeds the candidate's context window. |
| 4 | `already-failed` | `--failed` matches the candidate key, role, or launch identifier. |
| 5 | `specialist-not-permitted` | The role is `SPECIALIST` and `--allow-specialist` was not passed. |
| 6 | `below-capability-floor` | The relevant family capability is less than task need minus `max_capability_shortfall`. |
| 7 | `quantized-not-for-planning` | The task kind is `orchestrate` and the candidate is quantized. |
| 8 | `quantized-not-for-architecture` | `architecture_scope` reaches `architecture_threshold` and the candidate is quantized. |
| 9 | `planning-role-for-planning-only` | The task is not orchestration, but the candidate has an orchestration role. |
| 10 | `quantized-not-for-high-risk` | `high_risk_domain` reaches `high_risk_threshold` and the candidate is quantized. |
| 11 | `quantized-needs-acceptance-criteria` | `independently_testable` is below `testable_threshold` and the candidate is quantized. |

There is then one set-level pass for planning work. If at least one planning
candidate survived, every surviving non-planning candidate is removed with
`planning-work-needs-a-planning-role`. If no planning role survived, this pass
does not dead-end a catalog that has no usable planning entry.

Task kind selects the relevant capability family: orchestration uses
`planning`; exploration and summarization use `research`; implementation,
testing, and refactoring use `coding`; debugging and review use `debugging`.
Unknown kinds default to `coding`.

## Capability need and floor

Task need is:

```text
clamp(reasoning_complexity + ambiguity_lifts_need * ambiguity, 0.0, 1.0)
```

The capability floor is `need - max_capability_shortfall`. A candidate below
that floor is ineligible before ranking. The floor exists so cost pressure, an
unmetered bonus, or the exclusion of stronger models cannot silently make an
incapable model the winner. It expresses “cheapest capable,” not merely
“cheapest remaining.”

## Scoring

`score_one` starts at `1.0` and adds all of these terms:

| Term | Weight/configuration name | Calculation and intent |
| --- | --- | --- |
| `shortfall` | `w_shortfall` | Negative weight times `max(0, need - capability)`. Penalizes insufficient family capability. |
| `context` | `w_context` | Negative weight times the bounded fraction by which context headroom is below `1.0`. |
| `overshoot` | `w_overshoot` | Negative weight times `max(0, capability - need)`. Mildly discourages excess capability. |
| `oversized` | `w_oversized`, `oversize_at` | Penalizes bounded excess when context headroom exceeds `oversize_at`, preferring the smallest sufficient window. |
| `cost` | `w_cost`, `relative_cost_full` | Penalizes declared `relativeCost / relative_cost_full`, capped at `1.0`; otherwise uses role cost divided by `tiers.MAX_COST`. |
| `unmetered` | `w_unmetered` | Adds the configured bonus when the candidate is unmetered. |
| `horizon` | `w_horizon`, `long_horizon_threshold` | Adds the configured bonus only to a `SPECIALIST` when `long_horizon` reaches its threshold. |

Shortfall is intentionally punished much more heavily than overshoot. An
under-capable model risks incorrect work; an over-capable model primarily costs
more. The eligibility floor reinforces the same asymmetry before scoring.

Measured context comes from named files when available. Otherwise scoring
derives an implied requirement as
`implied_tokens_base * implied_tokens_span ** context_breadth`. Measured facts
therefore take precedence over Jev's breadth estimate.

Ranking sorts by descending score, then ascending role cost class, then
ascending stable candidate key. It never relies on dictionary insertion order.
Those explicit tie-breakers make the winner deterministic for identical
catalog facts, configuration, repository facts, and judgments.

## Reviewer selection

When `high_risk_domain` reaches `high_risk_threshold`, the router scans the
ranked candidates after the winner. The reviewer must have both a different
candidate key and a different launch identifier, and its `debugging`
capability must be at least the winner's. The first matching ranked candidate
is selected. If none exists, the decision records that limitation.

For high-risk work, a winner/runner-up gap below `confirm_margin` sets
`needs_confirmation`, but the router still selects a winner.

## Roles are metadata, not a ladder

`tiers.py` defines `COORDINATION`, `ORCHESTRATION`,
`ORCHESTRATION_ESCALATION`, `LOCAL_WORKER`, `WORKER`, `WORKER_ESCALATION`, and
`SPECIALIST`. A role supplies fallback strength, capability shape, cost class,
and a small number of policy meanings such as planning-only or specialist
gating.

There is deliberately no escalation ladder and no algorithm that walks roles
in order. Candidate enumeration, hard eligibility filters, the capability
floor, arithmetic scoring, and stable tie-breakers replaced ladder traversal.
Changing a declared capability or context fact can change the result without
changing a role name.
