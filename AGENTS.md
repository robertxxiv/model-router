# Repository Guidelines

## Project Structure & Module Organization

This repository is a flat Python CLI. `router.py` is the CLI entry point (subcommands: run, models, status, cache, import-roster) and `route.py` holds the routing command it dispatches to; `jev_router.py` defines TypeSafe/Jev questions and policy defaults. Candidate construction, hard filters, and ranking live in `catalog.py`, `candidates.py`, `eligibility.py`, and `scoring.py`. Supporting modules handle metadata, cached judgments, repository facts, Herdr state, and configuration. `discover.py` refreshes the local `models.json` catalog. Shell integration belongs in `hooks/`. Tests are in `tests/test_routing.py`, with scenarios in `tests/cases.json` and fixtures in `tests/fixtures/`.

Keep reusable logic in focused modules; keep CLI parsing in `router.py` and presentation in `route.py`. Treat `models.example.json` and `config.example.json` as documented, portable examples. Personal `models.json`, `config.json`, `.env`, caches, and router state must remain uncommitted.

## Build, Test, and Development Commands

- `python3 -m venv .venv && source .venv/bin/activate` creates a local environment.
- `python3 -m pip install -r requirements.txt` installs the TypeSafe SDK dependency.
- `python3 tests/test_routing.py` runs the complete offline suite without API keys or personal configuration.
- `python3 router.py models --catalog models.example.json` smoke-tests catalog parsing and candidate generation.
- `python3 discover.py --print` inspects models exposed by the configured OpenAI-compatible server; it requires the relevant environment variables.

There is no separate build step; scripts run directly with Python 3.

## Coding Style & Naming Conventions

Follow the existing PEP 8-style Python: four-space indentation, `snake_case` functions and variables, `UPPER_CASE` constants, type hints, and concise module docstrings. Prefer `pathlib.Path`, deterministic pure functions for routing policy, and explicit exclusion reasons. Keep vendor/model names out of policy logic. No formatter or linter is configured, so preserve the surrounding style and keep imports grouped as standard library, then local modules.

## Testing Guidelines

Add focused `test_*` functions to `tests/test_routing.py` and register them in its `__main__` test list. Use the example catalog and temporary directories so tests stay offline, deterministic, and machine-independent. For routing changes, cover both the selected winner and exclusion or scoring rationale.

## Commit & Pull Request Guidelines

Git history is unavailable in this checkout, so no repository-specific commit convention can be inferred. Use short, imperative subjects such as `Add quota freshness guard`, and keep each commit scoped. Pull requests should explain the behavior change, policy implications, and verification command; link related issues and include before/after CLI output when routing results change.

## Security & Configuration

Copy `.env.example` locally and never commit API keys, private endpoints, or machine-specific catalogs. Changes affecting authentication, destructive operations, deployment, or public contracts should include explicit tests and independent review.
