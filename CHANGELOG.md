# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project
follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
