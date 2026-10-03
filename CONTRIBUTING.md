# Contributing

Keep changes small, deterministic, and explainable. Routing policy should remain
ordinary Python that can be exercised without an API call or personal machine
configuration.

## Run the tests

Install the declared dependency in a virtual environment, then run:

```sh
python tests/test_routing.py
```

The suite does not need an API key, `models.json`, `config.json`, or any other
personal configuration. It uses `models.example.json` and local fixtures. At the
time this document was written, the complete successful output was:

```text
ok: 104 checks passed
```

Add focused `test_*` functions to `tests/test_routing.py` and include them in the
function list in its `__main__` block. Keep tests offline, deterministic, and
independent of installed model harnesses.

Selection logic must be deterministic for identical candidates, judgments,
facts, and configuration. Keep eligibility and scoring in pure, testable
functions. A new eligibility rule needs at least two assertions: one showing
that the rule excludes a candidate when it should fire, and one showing that it
does not over-fire immediately outside its intended boundary. When ranking
changes, assert both the selected winner and the relevant exclusion or scoring
rationale.

## Code style

Follow the existing Python style:

- Use four-space indentation, `snake_case` names, `UPPER_CASE` constants, and
  type hints.
- Prefer the standard library. The TypeSafe SDK is the project's deliberate
  external dependency and its use should remain isolated to the Jev integration;
  do not add a package when the standard library is sufficient.
- Give modules concise docstrings that explain why the module exists and what
  invariant it owns.
- Write comments that explain policy, intent, or a non-obvious tradeoff, not a
  mechanical restatement of the next line.
- Use `pathlib.Path`, deterministic ordering, and explicit reasons for policy
  exclusions.

There is no configured formatter or linter. Preserve the surrounding style and
group imports as standard library first, then local modules.

## Local and example files

Never commit personal `models.json`, `config.json`, `.env`, or `.model-router/`
state. They can contain machine-specific inventory, policy, credentials, or
sensitive judgments. `models.example.json` is the portable, reviewable catalog
example; use temporary directories and repository fixtures in tests.

## Add a model

For a local installation, add an entry to the uncommitted `models.json` catalog.
An entry with a `role` becomes a candidate. Declare its stable catalog `id`,
`harness`, launch `model` or `launchArgs`, `contextWindow`, capability values,
cost properties, and other supported facts rather than relying on role-derived
defaults. Run this smoke test after editing the catalog:

```sh
python router.py models --catalog models.json
```

If the model is useful as a portable example, add a non-personal entry to
`models.example.json` and update the offline tests. Do not put endpoints or
credentials in either catalog.

## Add a harness

A harness name crosses the catalog-to-shell boundary and therefore requires an
explicit code change. Add the kind to `KNOWN_KINDS` in `herdr_state.py`, add it
to the catalog parser's `HARNESSES` allowlist, and map it to its executable in
`eligibility.HARNESS_BINARY`. Define or supply the correct argument vector in
the catalog entry; do not construct a shell command string.

Add tests that reject an unknown or hostile kind, accept the new known kind,
and verify that every emitted argument remains shell-quoted. Then add or update
a portable catalog entry in `models.example.json` when the harness is intended
to appear in the example roster.
