# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project
follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Fixed

- `.env` is now read before the judgment cache key is built. A Jev model named
  only in `.env` was previously missing from the key, so a judgment made with
  one model could be replayed under another model's key.
- `jev_timeout_seconds` and `jev_total_timeout_seconds` from `config.json` now
  reach the Jev call; it was always using the built-in defaults. A `config.json`
  that sets only some keys no longer makes the call fail.
- A Jev timeout, authentication failure or API error now exits with a one-line
  message pointing at `--judgments-file`, instead of a traceback, and closes the
  cache connection on the way out.
- Expanding a named directory into repository facts skips history, caches and
  vendored trees (`.git`, `__pycache__`, `node_modules`, ...). Their contents
  were counted as source lines, and that count is a hard context filter, so an
  over-count could exclude a model that would in fact have held the task.

### Changed

- The capability the task demands is derived in one place (`families.py`) and
  read by both the hard capability floor and the shortfall term, which could
  previously drift apart.
- The catalog is parsed once per route instead of twice; `build_candidates`
  returns a `CandidateSet` carrying the candidates, their source, the parse
  warnings and the rejected identifiers.
- `README.md` and `requirements.txt` state the Python 3.11 minimum.

### Removed

- Dead code: a no-op branch in `candidates.from_catalog`, the unread
  `quant_family` field and its duplicate regex in `discover.py`, unused imports
  and re-exports, and two unused function parameters.


## [0.1.0] - 2026-10-03

First public release.

### Added

- Deterministic model selection. A remote model (TypeSafe/Jev) characterises the
  task with typed questions; everything after that is arithmetic over declared
  capabilities, so a route can be explained term by term and reproduced.
- A judgment cache (`cache.py`). Its key covers the task, the measured
  repository facts, the fingerprint of every question and the model, so
  rewording a question invalidates the cache automatically. Only a hash of the
  task is stored, never the text.
- `models.json` catalog declaring each model's harness, launch arguments,
  context window and per-family capability vector. `id` is a catalog key and
  `model` is the launch identifier, so one model can serve two roles at two
  reasoning efforts.
- `discover.py`, which reads an OpenAI-compatible inference server's own launch
  arguments to record real context windows, quantization and vision support for
  self-hosted models, refreshing facts without overwriting tuned capabilities.
- Hard eligibility filters, each emitting a named rule and a reason: rejected
  identifiers, a missing harness, a context window too small for the measured
  files, a capability floor, and policy guards keeping planning, high-risk and
  cross-module work off quantized local models.
- An independent reviewer recommendation for high-risk domains, on a different
  model from the implementer.
- `router` CLI: `run`, `models`, `status`, `cache`, `import-roster`.
- A Herdr plugin (`herdr-plugin.toml`) exposing routing as actions and overlay
  panes.
- A per-project on/off switch (`.herdr/router.json`); absent means enabled, and
  a corrupt flag fails open.
- `hooks/route-subagent-model.sh`, an example pre-spawn hook for Claude Code
  that routes in-process subagents and falls through safely in every case it
  does not handle.
- An offline test suite with no API key or personal configuration required.

### Security

- Task text reaches the CLI after a `--` end-of-options marker, so a task
  beginning with a dash can never be read as an option.
- Harness names are allowlisted and every emitted shell argument is quoted,
  because the printed Herdr commands are executable output.
- Worker names are validated, so a name cannot be parsed as a flag.
- The judgment store refuses a symlinked state directory or database.
- URLs are stripped of userinfo before appearing in any diagnostic.
