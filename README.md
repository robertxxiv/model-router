# model-router

**TL;DR** — You have several coding models available: a strong hosted one, a
cheap hosted one, a few you run yourself. For any given task, which should do
it? This answers that, deterministically, and hands you the commands to start
it.

```sh
pip install -r requirements.txt
cp models.example.json models.json      # then edit it to list YOUR models
echo "TYPESAFE_API_KEY=..." > .env      # console.typesafe.ai

router run "wire the retry helper into client.request() and add a test" \
           --files src/http/client.py
```

```
LOCAL_WORKER  local-small-32k   score 1.069   confidence 0.72
task : implement (100%) -> coding capability 0.46 vs need 0.39
ctx  : holds 32,768, task needs about 5,610
why  : overshoot -0.011 unmetered +0.080
next : local-mid-64k score 1.023 (gap +0.046)

herdr agent list
herdr pane split --current --direction right --cwd "$PWD" --no-focus
herdr agent start pi-local-worker-1 --kind pi --pane <pane-id> -- --model local-small-32k
```

It sent the task to a free local model because the task is small, verifiable and
not risky — and it shows the arithmetic that decided so. Paste the last two
lines and the worker is running.

- **Deterministic.** One remote call characterises the *task*; everything after
  that is arithmetic. The answer is cached, so the same task always routes the
  same way. ([why](docs/architecture.md))
- **No hardcoded model names.** You declare what you can run in `models.json`;
  change a number and the route changes. ([reference](docs/configuration.md))
- **Advisory.** It prints commands and never runs them. The only command it
  executes is the read-only `herdr agent list`. ([threat model](docs/security.md))

**Which agents does it work with?** Three different answers, so be clear which
you want ([details](docs/harnesses.md)):

| | Claude Code | Codex | pi |
| --- | --- | --- | --- |
| **Route to it** — the router picks it and prints the start command | yes | yes | yes |
| **Hook into it** — it asks the router before spawning sub-agents, automatically | yes | not verified | no mechanism |
| **Call it yourself** — `router run`, then act on the answer | yes | yes | yes |

Routing *to* a harness needs nothing from the harness and is the main use.
Hooking *into* one needs a pre-spawn hook that can rewrite the model, and only
Claude Code is confirmed to have one — `hooks/route-subagent-model.sh` is a
working example. From Herdr, the plugin works with all three.

Full walkthrough: [Using it](#using-it).

---

Pick the cheapest capable model for a coding task, and print the exact
[Herdr](https://herdr.dev) commands to put a worker on it.

You declare the models you can actually run. The router asks
[TypeSafe](https://typesafe.ai) System One (Jev) a handful of narrow typed
questions *about the task* — how hard is the reasoning, how wide is the scope,
is it self-verifiable, is it high-risk — and then **deterministic code does
everything else**: it enumerates candidates, filters them on facts and policy,
scores the survivors, and shows the arithmetic that chose the winner.

```
$ router run "Wire the existing retry helper into client.request() and add a unit test." \
             --files src/http/client.py src/http/retry.py

LOCAL_WORKER  local-small-32k   score 1.069   confidence 0.72
task : implement (100%) -> coding capability 0.46 vs need 0.39
ctx  : holds 32,768, task needs about 5,610
why  : overshoot -0.011 unmetered +0.080
next : local-mid-64k score 1.023 (gap +0.046)

herdr agent list
herdr pane split --current --direction right --cwd "$PWD" --no-focus
herdr agent start pi-local-worker-1 --kind pi --pane <pane-id> -- --model local-small-32k
```

No model name is hardcoded anywhere, and there is no fixed ladder of tiers to
step through. Change a capability number and the route changes.

**The same task always gets the same route.** Jev's characterization of a task is
the only non-deterministic step, so it is cached: the first run calls the API,
every later run replays the stored judgments. The cache key covers the task, the
measured repository facts, the exact wording of every question and the model, so
rewording a question invalidates it automatically - a stale judgment can never
outlive the question that produced it. The raw task text is never stored, only its
hash.

## Why

The interesting rule in most model policies is the one everybody breaks:

> Running out of context is **not** a reason to escalate to a stronger model.
> Pick a wider model instead. Escalate when the task is *reasoning*-hard.

That is easy to write down and hard to apply under pressure, because "this is
big" feels like "this is hard". Here the two cannot be confused, because they
are different kinds of thing:

- **Context is a fact.** A 64K model cannot hold a 200K task, so it is filtered
  out before anything is scored. Nothing can overrule that.
- **Difficulty is a judgment.** Only reasoning difficulty and ambiguity raise
  the capability the task demands, and only that can reach a stronger model.

So a repository-wide rename lands on the cheap long-context local model, while a
subtle concurrency bug in one file escalates. Neither outcome is a special case
in the code — both fall out of the candidate set and the score.

## Commands

```
router run "<task>" [--files ...]   route one task
router models                       the candidate table and why each model is there
router status                       routing, catalog and cache at a glance
router enable | router disable      turn routing on or off for this project
router cache stats|clear|prune      the judgment cache that makes routes repeatable
router import-roster FILE           convert a markdown policy file into models.json
```

A `router` shim on your PATH is two lines:

```sh
#!/bin/bash
exec /path/to/.venv/bin/python /path/to/model-router/router.py "$@"
```

## Install

```sh
pip install -r requirements.txt          # typesafe-sdk
cp .env.example .env                     # then add your TYPESAFE_API_KEY
```

A key comes from [console.typesafe.ai](https://console.typesafe.ai/). You need
it to judge a task; `--judgments-file`, `--show-candidates` and `--status` work
offline.

## Declaring your models

The catalog, `models.json`, is the one thing you must supply. Each entry states
what the model is and how to start it. `models.example.json` is a working
example — copy it and replace the ids.

```json
{
  "id": "vendor:worker",
  "role": "WORKER",
  "harness": "claude",
  "model": "vendor-worker",
  "launchArgs": ["--model", "vendor-worker"],
  "contextWindow": 200000,
  "capabilities": {"planning": 0.80, "coding": 0.85, "debugging": 0.85,
                   "creativity": 0.72, "research": 0.78},
  "relativeCost": 1.0
}
```

`id` is a catalog key, `model` is what the harness is told — which is how one
model can serve two roles at two reasoning efforts (`vendor:plan` and
`vendor:plan-high` in the example are the same model). `role` is what the entry
is *for*; the roles are listed below. Only `id` and `role` are required, and
anything you leave out is derived from the role and marked as derived rather
than declared.

Schema shape borrowed from
[`nidhi-singh02/agent-router`](https://github.com/nidhi-singh02/agent-router),
which does this well.

### Discovering local models

If you serve models yourself through an OpenAI-compatible endpoint
(llama.cpp / llama-swap), do not hand-write their facts — the server reports the
command line each model was launched with, which is ground truth for the context
window, the quantization and whether a vision projector is loaded:

```sh
export LLAMA_BASE_URL=https://your-inference-host    # or --auth-file
export LLAMA_API_KEY=...
python discover.py --print                # what the server exposes
python discover.py --out models.json      # write/merge a catalog
```

It merges: facts are refreshed on every run, capability numbers you have tuned
are never overwritten. Capability numbers it writes initially are derived from
quantization alone and are a starting point, which the catalog's `provenance`
field records.

Nothing about your server is stored in the catalog — no host, no key.

## Using it

### 1. Tell it what you can run

`models.json` is the only file you must supply. Copy `models.example.json` and
replace the entries with your own models — one entry per model you could
actually start, each saying which harness runs it and how good it is.

If you serve models yourself through an OpenAI-compatible endpoint, do not type
their facts by hand:

```sh
export LLAMA_BASE_URL=https://your-inference-host
export LLAMA_API_KEY=...
python discover.py --out models.json     # reads real context windows and quantization
```

Check the result:

```sh
router models          # every candidate, its capabilities, context window and cost class
router status          # is routing on, which catalog, how warm the cache is
```

### 2. Route a task

Describe the task the way you would to a colleague, and name the files it
touches — measured file sizes beat a guess about scope:

```sh
router run "Split the Store class into read and write paths; every caller
            depends on it. Keep the public API stable." --files src/store.py
```

```
WORKER  vendor-worker   score 0.884   confidence 0.98
task : refactor (99%) -> coding capability 0.85 vs need 0.67
ctx  : holds 200,000, task needs about 61,420
why  : overshoot -0.027 cost -0.067
next : vendor-strong score 0.755 (gap +0.129)
```

Same task, different answer — this one changes contracts other modules depend
on, so the local models were filtered out before scoring and the strong hosted
model won. Ask why anything lost with `-v`:

```sh
router run "$TASK" -v        # the full ranking, plus every exclusion and its reason
```

### 3. Start the worker

The router prints the Herdr commands; you run them. Split a pane, read the pane
id it returns, then start the agent:

```sh
herdr pane split --current --direction right --cwd "$PWD" --no-focus
herdr agent start worker-1 --kind claude --pane w1:p4 -- --model vendor-worker
herdr agent prompt worker-1 "$TASK" --wait --timeout 600000
```

If an idle agent already runs the chosen model, the router suggests reusing it
instead — labelled `likely` or `possible`, because Herdr does not report an
agent's model and the match is a heuristic. Confirm before trusting it.

### 4. When a worker fails

Tell the router what failed and why. It will not pick that model again for this
task, and the reason goes into the next judgment:

```sh
router run "$TASK" --failed WORKER:"could not reproduce the race"
```

Escalation is never automatic and never happens because a task is *large* —
only because it is reasoning-hard. A wide, shallow task widens the model
instead.

### 5. High-risk work gets a second pair of eyes

When a task touches auth, permissions, migrations, payments, concurrency, a
public API contract, destructive operations or deployment, the router also names
an **independent reviewer** on a different model from the implementer:

```
reviewer: vendor-strong (WORKER_ESCALATION, score 0.755)
why     : high-risk work needs a reviewer independent of the implementer
```

### 6. Make it disagree with you less

Every threshold and weight is in `config.json`, and retuning costs nothing
because selection never calls the API:

```sh
router run "$TASK" --json > /tmp/v.json          # keep the judgments
jq .judgments /tmp/v.json > /tmp/j.json
router run "$TASK" --judgments-file /tmp/j.json  # re-route offline, free
```

Route five or six tasks whose right answer you already know, then adjust
`config.json` until it agrees. `docs/configuration.md` says what each key does.

### 7. From Herdr, or from a hook

Link the plugin and routing becomes a keybinding instead of a prompt:

```sh
herdr plugin link /path/to/model-router
herdr plugin action list --plugin model-router
```

`hooks/route-subagent-model.sh` is a worked example of routing an agent
harness's own in-process subagents automatically. It falls through safely —
never blocking a spawn — in every case it does not handle.

### Turn it off for a project

```sh
router disable      # writes .herdr/router.json
router enable
```

Absent flag means enabled. A corrupt flag fails open, because a typo in a config
file should not silently stop a safety mechanism.

## Roles

A role says what an entry is for. The router never walks them in order; they
carry the policy guards and a cost class.

| Role | For | Guard |
| --- | --- | --- |
| `COORDINATION` | summaries, exploration, repetitive work | |
| `ORCHESTRATION` | planning, decomposition, assignment | planning work goes **only** to a planning role |
| `ORCHESTRATION_ESCALATION` | planning that is hard and ambiguous | as above |
| `LOCAL_WORKER` | self-hosted capacity, unmetered | never gets planning, high-risk, or unverifiable work |
| `WORKER` | the default for real work | |
| `WORKER_ESCALATION` | what the worker could not resolve; review | |
| `SPECIALIST` | long-horizon work | unreachable without `--allow-specialist` |

## How a route is decided

```
models.json ──► candidates ──► eligibility ──► scoring ──► winner (+ reviewer)
                               (facts and         (arithmetic)
                                policy guards)
task text ──► Jev ──► typed judgments ──────────────┘
                      (about the task only)
```

Jev never sees a model, a role, or the roster. It is asked nine narrow questions
about the assignment and nothing else, so its answers cannot be anchored on the
answer you were hoping for.

**Eligibility** is hard. Each exclusion carries a reason, and `-v` prints them
all: a rejected identifier, a harness that is not installed, a context window
too small for the measured files, a role that may not do this class of work, a
candidate that already failed (`--failed`), the gated specialist.

**Eligibility** also enforces a **capability floor**: a candidate more than
`max_capability_shortfall` below what the task demands is excluded outright, not
merely scored low. Without it, cost pressure - or the stronger candidates being
excluded for some other reason - could quietly push hard work onto a model that
cannot do it.

**Scoring** is arithmetic over the capability vector and the task's demands:

```
start at 1.0
  - shortfall   the task needs more capability than this candidate has   (heavy)
  - context     its window is too small for the measured or implied size (heavy)
  - overshoot   it is stronger than the task needs                       (light)
  - oversized   its window is far larger than the task needs             (light)
  - cost        what the route costs, as a class                         (light)
  + unmetered   locally served capacity consumes no subscription
  + horizon     a specialist on genuinely long-horizon work
```

Shortfall is penalized far harder than overshoot: too weak produces bad work,
while too strong only wastes money. Cost and the unmetered bonus are what
implement "cheapest capable" without ever letting price beat capability. Ties
break on cost, then on key — never on dictionary order, so the same inputs
always give the same route.

When a task lands in a high-risk domain — auth, permissions, migrations,
payments, concurrency, public API contracts, destructive operations, deployment
— the router also names an **independent reviewer**: a different model, at least
as strong at review as the one doing the work. If the top two candidates are
within `confirm_margin` on such a task, it says so and sets
`needs_confirmation` in the JSON, but still commits to a winner.

## Automatic routing, where a hook point exists

| Path | Automatic? |
| --- | --- |
| Claude Code's in-process sub-agents (`Agent`/`Task`) | **yes** — `PreToolUse` can rewrite the model |
| Codex sub-agents | **not verified** — Codex has hooks, but no model-rewriting event is confirmed |
| pi | **no** — pi exposes no hooks |
| A pane you start yourself (`herdr agent start`) | **no hook point.** The router advises. |

`hooks/route-subagent-model.sh` is the Claude Code example. It adds about 1.5s
to a spawn and is deliberately conservative: it hands the spawn to whatever hook
ran before it, unchanged, when routing is off, when a model was named
explicitly, on a `fork`, or if the router is missing, keyless, slow or
unreachable. **A spawn is never blocked by it** — a model chooser that can wedge
your agent harness is worse than no model chooser.

Per-harness setup, the environment variables, and the sub-agent alias
constraint are in **[docs/harnesses.md](docs/harnesses.md)**.

## Usage

```
route.py "<assignment>" | --task-file PATH
  --files A B C          paths the task touches (measured: count, LOC, nearby tests)
  --diff                 include git diff --stat as a repository fact
  --failed TARGET[:WHY]  a candidate, role or model that already failed; repeatable
  --allow-specialist     permit the specialist role
  --name NAME            worker name for the emitted command
  --cwd PATH             cwd for the emitted pane split (default: $PWD)
  -v, --verbose          show the full ranking and every exclusion
  --json                 machine-readable decision
  --judgments-file PATH  offline: route stored judgments, no Jev call
  --for-subagent         one JSON line with a subagent model alias (for a hook)
  --status               is routing on, and from what catalog
  --enable / --disable   turn routing on or off for this project
  --ignore-switch        route even if this project has routing disabled
  --show-candidates      print the candidate table and exit
  --no-herdr             skip the read-only `herdr agent list` probe
  --no-harness-check     do not require a harness to be installed
  --catalog PATH         models.json (default: ./models.json)
  --config PATH          thresholds and weights (default: ./config.json)
```

One `system_one` call per decision, about a thousand input tokens.

## Coming from a markdown policy file

Routing reads the catalog and nothing else, so a policy document is converted
once rather than parsed on every run:

```sh
router import-roster ~/.config/herdr/DEVELOPMENT_TEAM.md --out models.json
```

It parses a role table and a local-alias table, derives what the catalog would
have declared, and records in `provenance` which numbers were derived rather than
stated so you know what to review. `tests/fixtures/DEVELOPMENT_TEAM.example.md`
is the worked example. Afterwards run `discover.py` to replace the self-hosted
models' derived facts with real ones.

The policy document stays useful as policy for *people* — the delegation rules,
the escalation discipline. The catalog is how those rules become executable.

## Layout

```
models.json       the models you can run      (yours; gitignored)
discover.py       build it from an inference server's launch arguments
catalog.py        read it
roster.py         read a markdown policy file instead
candidates.py     catalog/roster -> candidates with capability vectors
eligibility.py    hard filters: facts and policy guards, each with a reason
scoring.py        deterministic selection, plus the reviewer rule
jev_router.py     the nine questions, and the thresholds and weights
repo_facts.py     paths -> file count, LOC, nearby tests, diff stat
herdr_state.py    read-only `herdr agent list`; the printed spawn commands
router_config.py  the per-project on/off flag
cache.py          the judgment cache that makes a route reproducible
router.py         the CLI (subcommands)
route.py          the routing command itself
roster.py         markdown policy parser, used only by `import-roster`
```

## Herdr plugin

`herdr-plugin.toml` exposes routing as Herdr actions and overlay panes, so it is
reachable from a keybinding instead of a shell prompt:

```sh
herdr plugin link /path/to/model-router
herdr plugin action list --plugin model-router
```

### Reuse is a suggestion

No herdr command reports the model an agent was launched with — `agent list`,
`agent get` and `agent explain --json` were all checked and none carries it. A
reuse candidate is matched on harness kind, idle status, matching cwd, and
whether the agent's name or terminal title mentions the chosen identifier. The
router labels it `likely` or `possible` and tells you the model is unconfirmed.
Name your workers after their models and this works well; do not treat it as a
guarantee.

## Tests

```sh
python tests/test_routing.py    # 104 checks, no API calls, no personal config
```

Everything runs against `models.example.json`, so the suite passes on a clean
clone. The assertions worth knowing about: that a maximum-breadth low-reasoning
task stays on a local model and only changes *which* one, that a small task
takes the smallest sufficient window, that shuffling the candidate list never
changes the ranking, that hard work never goes to a cheap weak model to save
money, that a high-risk reviewer is always a different model from the
implementer, that the specialist is unreachable without its flag, and that when
no candidate survives, every exclusion is recorded with a reason.

## Documentation

| Document | What is in it |
| --- | --- |
| [docs/architecture.md](docs/architecture.md) | The pipeline, what Jev is and is not told, every eligibility rule and scoring term, and why the design is deterministic |
| [docs/configuration.md](docs/configuration.md) | Complete reference for `models.json` and every threshold and weight in `config.json` |
| [docs/harnesses.md](docs/harnesses.md) | Wiring it to Claude Code, Codex and pi: what each supports, the hook, and the constraints |
| [docs/security.md](docs/security.md) | Trust boundaries, the defect classes addressed and how, secret handling, and data at rest |
| [CONTRIBUTING.md](CONTRIBUTING.md) | Running the tests, code style, and how to add a model or a harness |
| [CHANGELOG.md](CHANGELOG.md) | Release history |

## License

[MIT](LICENSE).
