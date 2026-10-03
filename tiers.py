"""Roster roles, and the capability ordering derived from them.

These are the roles a policy file declares, normalized. They are **metadata**
about a model, not a decision structure: nothing routes by stepping through
them. Selection enumerates candidates and scores them (see scoring.py).
"""
from __future__ import annotations

COORDINATION = "COORDINATION"
ORCHESTRATION = "ORCHESTRATION"
ORCHESTRATION_ESCALATION = "ORCHESTRATION_ESCALATION"
LOCAL_WORKER = "LOCAL_WORKER"
WORKER = "WORKER"
WORKER_ESCALATION = "WORKER_ESCALATION"
SPECIALIST = "SPECIALIST"

ALL = (
    COORDINATION,
    ORCHESTRATION,
    ORCHESTRATION_ESCALATION,
    LOCAL_WORKER,
    WORKER,
    WORKER_ESCALATION,
    SPECIALIST,
)

# Roles whose job is deciding what others should do, rather than doing it.
PLANNING_ROLES = frozenset({ORCHESTRATION, ORCHESTRATION_ESCALATION})

# --------------------------------------------------------------- capability --
# The one place capability is *asserted* rather than measured. Everything else
# in a candidate's vector comes from the policy file's own tables (context
# window, whether the alias reasons, which harness runs it).
#
# The ordering is the policy file's: a default worker is stronger than cheap
# coordination, an escalation role stronger than the default worker. The exact
# numbers are a starting point and are overridable from config.json, because
# they encode a judgment about models this file cannot inspect.
ROLE_STRENGTH = {
    COORDINATION: 0.25,
    LOCAL_WORKER: 0.45,               # refined per alias by its Reasoning column
    ORCHESTRATION: 0.60,
    SPECIALIST: 0.90,
    WORKER: 0.85,
    ORCHESTRATION_ESCALATION: 0.95,
    WORKER_ESCALATION: 0.97,
}

# What a route costs, as a class rather than a price: locally served models are
# unmetered, so they cost 0; each escalation step up the roster costs more.
ROLE_COST = {
    LOCAL_WORKER: 0,
    COORDINATION: 1,
    ORCHESTRATION: 2,
    WORKER: 2,
    ORCHESTRATION_ESCALATION: 3,
    WORKER_ESCALATION: 3,
    SPECIALIST: 4,
}
MAX_COST = max(ROLE_COST.values())

# A context window the policy file does not state. Hosted models do not declare
# one in the roster table, so this is an assumption, surfaced as one.
ASSUMED_CONTEXT_TOKENS = 200_000
