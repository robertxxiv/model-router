# Configuration reference

## Configuration files

The normal inputs are:

- `models.json`, the catalog of launchable model entries and their facts;
- `config.json`, optional overrides for thresholds and weights; and
- `.herdr/router.json`, the per-project on/off switch.

The CLI defaults to `models.json` and `config.json` beside the router source.
`--catalog` and `--config` select different files for commands that expose
those options.

## `models.json`

The root must be a JSON object containing a `models` array. Without that array,
catalog parsing fails.

### Top-level fields

| Field | Required | Meaning |
| --- | --- | --- |
| `models` | Yes | Array of catalog entry objects. Invalid non-object entries are skipped. |
| `updatedAt` | No | Free-form string recording when the catalog was refreshed. Used for status/reporting, not selection. |
| `provenance` | No | Free-form string explaining where catalog facts came from. Stored for reporting, not selection. |

### Model entry fields

Only `id` is required by the parser. In practice, an entry needs a valid `role`
to become a candidate, and routing exits if no parsed entry declares a role.
Unknown roles cause that entry to be skipped.

| Field | Required | Accepted value and routing effect |
| --- | --- | --- |
| `id` | Yes | Non-empty string and unique catalog key. Duplicate keys use the last entry. It becomes the candidate key and is also the fallback launch identifier. |
| `role` | Required to become a candidate | String normalized to uppercase with `-` changed to `_`; must be `COORDINATION`, `ORCHESTRATION`, `ORCHESTRATION_ESCALATION`, `LOCAL_WORKER`, `WORKER`, `WORKER_ESCALATION`, or `SPECIALIST`. Supplies fallback capabilities, cost class, and role policy. |
| `model` | No | String passed to the harness instead of `id`. If absent, the launch identifier is `id`. |
| `effort` | No | String identifying this entry's reasoning effort. It appears in labels and is passed to launch inference; identifiers inferred as Codex receive `model_reasoning_effort=<effort>` when `launchArgs` is absent. |
| `harness` | No | One of `claude`, `codex`, or `pi`. Determines the Herdr agent kind and harness-availability check. If absent, it is inferred from the launch identifier. |
| `agent` | No | Compatibility alias for `harness`; it is read only when `harness` is absent and accepts the same values. |
| `launchArgs` | No | Array of strings placed after `--` in the generated Herdr start command. If absent or empty, arguments are inferred from the launch identifier and effort. |
| `contextWindow` | No | Numeric token capacity from `1` through `100000000`. It controls the measured context eligibility guard and context-size scoring. Missing values use `200000` assumed tokens. |
| `supportedEfforts` | No | Array of strings copied into candidate metadata and JSON output. The current eligibility and scoring code does not validate `effort` against it. |
| `capabilities` | No | Object containing any of `planning`, `coding`, `debugging`, `creativity`, and `research`, each numeric in `0.0..1.0`. Declared families override role-derived values; undeclared families remain derived. |
| `relativeCost` | No | Numeric value from `0` through `1000`. When present, scoring uses it instead of role cost class for the `cost` term. |
| `unmetered` | No | Boolean. True forces cost class `0` and earns the unmetered score bonus. If absent, `pi` candidates are assumed unmetered. |
| `quantized` | No | Boolean. Drives the planning, architecture, high-risk, and testability eligibility guards. If absent, `pi` candidates are assumed quantized. |
| `images` | No | Boolean copied into candidate metadata. It currently does not participate in eligibility or scoring. |
| `notes` | No | String used as the model's descriptive role label. It does not participate in selection. |
| `rejected` | No | Boolean. `true` marks both the catalog key and launch identifier rejected and prevents candidate construction from catalog-only input. |
| `enabled` | No | Boolean compatibility switch. `false` has the same rejection effect as `rejected: true`; other values do not reject the entry. |

Invalid optional values produce catalog warnings and are ignored. An invalid
`id` skips the entry. An invalid explicit `role` also skips the entry.

### `id` versus `model`

`id` identifies a catalog row. `model` identifies what the selected harness
actually launches. Keeping them separate allows one underlying model to have
multiple candidates with different purposes or settings.

For example, `models.example.json` uses the distinct keys `vendor:plan` and
`vendor:plan-high`, but both launch `vendor-plan`. Their roles, efforts, launch
arguments, capabilities, and costs can differ. This represents one model
serving two roles at two reasoning efforts without collapsing the candidates
into one catalog identity.

Reviewer independence checks both candidate key and launch identifier. Thus
two entries that share the same `model` are not considered independent
reviewers of one another.

### Derived catalog values

When a catalog fact is absent:

- capabilities start with `tiers.ROLE_STRENGTH`, modified by the role shape in
  `candidates.ROLE_SHAPE`;
- context defaults to `tiers.ASSUMED_CONTEXT_TOKENS`, which is `200000`;
- harness and launch arguments are inferred from the launch identifier;
- `pi` implies unmetered and quantized, while other harnesses do not; and
- cost class comes from `tiers.ROLE_COST`, except unmetered candidates use `0`.

Candidate output records whether capabilities and context were declared,
partly declared, or assumed.

## `config.json`

`config.json` is a flat JSON object. `route.load_config` starts with every
value in `jev_router.DEFAULTS` and overlays keys from the selected file. The
following tables list every default key.

### Eligibility gates

| Key | Default | Effect | Raising / lowering |
| --- | ---: | --- | --- |
| `architecture_threshold` | `0.80` | Minimum `architecture_scope` that bars quantized candidates from cross-module contract work. | Raising permits quantized candidates for more borderline architecture judgments; lowering excludes them more often. |
| `high_risk_threshold` | `0.60` | Minimum `high_risk_domain` that bars quantized candidates, requests an independent reviewer, and enables close-call confirmation. | Raising makes all three behaviors less frequent; lowering makes them more frequent. |
| `testable_threshold` | `0.60` | Minimum `independently_testable` required for a quantized candidate. | Raising excludes quantized candidates more often; lowering accepts less-verifiable work on them. |
| `long_horizon_threshold` | `0.70` | Minimum `long_horizon` for the specialist horizon bonus. It does not bypass specialist gating. | Raising makes the bonus harder to earn; lowering makes it easier. |
| `jev_timeout_seconds` | `20.0` | Timeout for each Jev operation passed to the SDK. | Raising allows slower operations; lowering fails them sooner. |
| `jev_total_timeout_seconds` | `45.0` | Outer timeout for the complete retrying Jev request. | Raising allows more total retry time; lowering bounds the request more tightly. |

### Task requirement derivation

| Key | Default | Effect | Raising / lowering |
| --- | ---: | --- | --- |
| `ambiguity_lifts_need` | `0.25` | Multiplies ambiguity before adding it to reasoning complexity. | Raising increases capability need for ambiguous tasks; lowering reduces ambiguity's influence. |
| `max_capability_shortfall` | `0.15` | Maximum gap below task need allowed by the capability floor. | Raising admits weaker candidates; lowering makes the floor stricter. |
| `implied_tokens_base` | `8000` | Required tokens when unmeasured context breadth is `0.0`. | Raising increases implied context needs and favors larger windows; lowering does the reverse. |
| `implied_tokens_span` | `32` | Exponential span in `base * span ** context_breadth`; at breadth `1.0`, implied tokens are base times span. | Raising grows implied needs more sharply with breadth; lowering flattens that growth. |

### Scoring weights

| Key | Default | Effect | Raising / lowering |
| --- | ---: | --- | --- |
| `w_shortfall` | `1.20` | Penalty per unit of capability below need among eligible candidates. | Raising favors candidates closer to or above need more strongly; lowering tolerates eligible shortfall. |
| `w_context` | `0.90` | Penalty for a context window below the measured or implied requirement. | Raising favors sufficient windows more strongly; lowering weakens this preference. |
| `w_overshoot` | `0.15` | Penalty per unit of capability above need. | Raising favors closer-fit, weaker models; lowering tolerates excess capability. |
| `w_cost` | `0.20` | Multiplier for declared relative cost or fallback role cost. | Raising favors cheaper candidates more strongly; lowering reduces cost influence. |
| `w_oversized` | `0.10` | Penalty for context headroom beyond `oversize_at`. | Raising favors the smallest sufficient context window more strongly; lowering tolerates oversized windows. |
| `oversize_at` | `8.0` | Context-headroom multiple at which the oversized penalty begins. | Raising delays the penalty to larger windows; lowering begins it sooner. |
| `w_unmetered` | `0.08` | Bonus for an unmetered candidate. | Raising favors unmetered capacity more strongly; lowering weakens that preference. |
| `w_horizon` | `0.10` | Bonus for an allowed specialist on long-horizon work. | Raising favors that specialist more strongly; lowering weakens the bonus. |
| `relative_cost_full` | `3.0` | Declared `relativeCost` is divided by this value and capped at `1.0` before applying `w_cost`. | Raising reduces normalized declared-cost penalties; lowering reaches the full penalty sooner. |

### Reporting

| Key | Default | Effect | Raising / lowering |
| --- | ---: | --- | --- |
| `confirm_margin` | `0.05` | On high-risk work, a winner/runner-up gap below this value sets `needs_confirmation`. | Raising marks more close decisions; lowering marks fewer. |
| `near_margin` | `0.10` | Distance from a noul threshold within which text output reports that a gate is close to flipping. | Raising reports more near-threshold judgments; lowering reports fewer. |

The code does not apply a schema to arbitrary extra `config.json` keys, but
only keys consumed by the routing code affect behavior. Values are used as
loaded; malformed types can fail at runtime.

## Per-project routing switch

The switch is `.herdr/router.json` under the detected project root. The root is
the nearest ancestor containing `.herdr`, `.git`, `AGENTS.md`, or `CLAUDE.md`;
if no marker exists, the starting directory is used.

The file shape written by the CLI is:

```json
{
  "enabled": false,
  "note": "routing disabled for this project"
}
```

`note` is optional. The behavior is:

- absent file: routing is enabled by default;
- valid file: `enabled` is converted with Python `bool`, defaulting to `true`
  when the key is absent;
- unreadable or corrupt JSON: routing fails open, remains enabled, and records
  an explanatory note.

`router disable` writes the disabled form atomically;
`router enable` writes `{"enabled": true}`. `--ignore-switch` routes once
even when the project switch is off.

## Generating a catalog

### Self-hosted OpenAI-compatible models

`discover.py` queries the configured server's `/v1/models` response and reads
the reported server launch arguments. It derives context window, quantization,
vision-projector presence, and descriptive facts from that response.

Set `LLAMA_BASE_URL` and, when needed, `LLAMA_API_KEY`, or pass a pi-style
`--auth-file`. Then inspect or write the catalog:

```sh
python discover.py --print
python discover.py --out models.json
```

`--role` assigns a role to discovered models and defaults to `LOCAL_WORKER`.
`--only` limits discovery output to named model IDs. On merge, discovery
refreshes `contextWindow`, `images`, `harness`, and `notes` for existing IDs,
but preserves operator-tuned capability numbers and other fields. Newly
discovered capability values are starting estimates based on quantization, not
measurements of model quality.

### Markdown policy roster

Convert an existing markdown policy file with:

```sh
router import-roster PATH --out models.json
```

The default output is `models.json`. The command creates catalog rows from the
parsed roster, including derived context and capability values where the
policy did not state them. It refuses to overwrite an existing output unless
`--force` is passed. Review the generated numbers, then run `router models`.

## Precedence rules

Configuration precedence is straightforward:

1. `jev_router.DEFAULTS` supplies every threshold and weight.
2. Keys present in `config.json` replace same-named defaults.

Candidate fact precedence is similarly local to each field:

1. A valid catalog-declared capability, context window, harness, launch
   argument list, cost, or boolean fact is used when present.
2. Missing values are derived from role metadata, harness convention, or the
   documented assumptions.

For a partially declared `capabilities` object, each declared family replaces
only that family; role-derived values fill the remainder. Catalog-declared
facts therefore take precedence over role-derived facts without requiring an
entry to declare every field.
