# model-router

Pick a coding model for a task and get the [Herdr](https://herdr.dev) commands
to start it. The router considers the models you can run, the task's difficulty
and size, and your cost and safety rules. It prints a recommendation; you decide
whether to run the commands.

## Agent's setup

Paste this into your coding agent:

```text
Install model-router from https://github.com/robertxxiv/model-router. Follow
its README to install dependencies and configure real, launchable models and
a TypeSafe API key; never commit the key. If you're in Claude Code, connect
the included hooks/route-subagent-model.sh to PreToolUse for Agent|Task. In
another harness, use the CLI directly. Verify the setup with
python3 router.py models and one test route.
```

## Quickstart

Needs Python 3.11 or newer (the Jev call bounds itself with
`asyncio.timeout`). Run these commands from the repository directory:

```sh
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -r requirements.txt
cp .env.example .env
cp models.example.json models.json
```

Add your `TYPESAFE_API_KEY` to `.env` ([get a key](https://console.typesafe.ai/)).
Edit `models.json` to list models you can actually launch. The example catalog
contains invented model IDs, so copying it alone is not enough for a usable
route. At minimum, each entry needs an `id` and a valid `role`; declare its
`harness`, launch arguments, context window, capabilities, and cost when you
know them. Missing values are derived from the role and marked as such in the
candidate output. See the [catalog reference](docs/configuration.md#modelsjson).

Check your catalog, then route a task:

```sh
python3 router.py models
python3 router.py run "Add a test for router.py argument parsing" \
  --files router.py tests/test_routing.py
```

Name real files when you can: the router measures them to estimate context
needs. If the listed harness is not installed, that candidate is excluded.
The first route calls TypeSafe System One (Jev); later runs with the same task,
measured facts, questions, and Jev model replay its cached judgment.

A route looks like this (the model names and numbers are illustrative):

```text
LOCAL_WORKER  local-small-32k   score 1.069   confidence 0.72
task : implement (100%) -> coding capability 0.46 vs need 0.39
ctx  : holds 32,768, task needs about 5,610
why  : overshoot -0.011 unmetered +0.080
next : local-mid-64k score 1.023 (gap +0.046)

herdr agent list
herdr pane split --current --direction right --cwd /path/to/your/project --no-focus
herdr agent start pi-local-worker-1 --kind pi --pane <pane-id> -- --model local-small-32k
```

The winner, runner-up, score terms, and any exclusions are explanations. The
router prints the Herdr commands; it does not start an agent. Run the pane split,
replace `<pane-id>` with the returned ID, then run the agent start command. Use
`-v` to see every candidate and exclusion. For high-risk work, the output may
also recommend an independent reviewer.

## Other ways to set up a catalog

If you serve models through an OpenAI-compatible llama.cpp or llama-swap
endpoint, discovery can read their reported context and quantization facts:

```sh
export LLAMA_BASE_URL=https://your-inference-host
export LLAMA_API_KEY=...  # if your server requires a key
python3 discover.py --print
python3 discover.py --out models.json
```

Discovery preserves capability values you have tuned in existing entries.
New capability values are estimates based on quantization; review them before
routing. You can also convert an existing markdown policy roster with
`python3 router.py import-roster PATH --out models.json`. See
[generating a catalog](docs/configuration.md#generating-a-catalog).

## How the decision works

Jev answers eight typed questions about the task, such as its kind, reasoning
difficulty, context breadth, and risk. It does not receive the model catalog or
choose the winner. Local code builds candidates, excludes those that fail
policy or capacity checks, then ranks the survivors by capability fit, context,
and cost. A task that needs more context can go to a wider model without being
treated as harder reasoning work.

The live Jev judgment is model-generated. The same task and inputs produce a
repeatable route after that judgment is cached. You can inspect the full
[routing policy and cache key](docs/architecture.md), and change thresholds
and weights with [optional `config.json`](docs/configuration.md#configjson).

## Everyday commands

```sh
python3 router.py models             # candidate table
python3 router.py status             # project switch, catalog, cache
python3 router.py run "$TASK" -v      # ranking and exclusion reasons
python3 router.py run "$TASK" --json  # machine-readable result
python3 router.py cache stats        # cache size and replay count
python3 router.py disable            # turn off routing for this project
python3 router.py enable             # turn it back on
```

If a worker failed, rerun with `--failed WORKER:reason` to exclude that role
and pass the failure description to Jev. `--judgments-file PATH` routes from
saved judgments without an API call. `python3 router.py run --help` lists every
option. The reference pages use `router` as shorthand for `python3 router.py`.
The legacy `route.py` entry point also works.

## Integrations and safety

The router can recommend a Claude Code, Codex, or pi model and print a Herdr
start command for any of them. Automatic subagent model rewriting is confirmed
for Claude Code only. The [harness guide](docs/harnesses.md) covers hooks and
the Herdr plugin.

Task text and measured repository facts are sent to TypeSafe for a live
judgment. Do not put secrets in task descriptions. The router stores a hash of
the task rather than its raw text in the local judgment cache. It does not run
the printed agent commands; its only Herdr command is the read-only
`herdr agent list`. See the [security model](docs/security.md) for details.

## Reference

- [Architecture](docs/architecture.md): questions, filters, scoring, reviewer,
  and replay behavior.
- [Configuration](docs/configuration.md): catalog fields, roles, thresholds,
  discovery, and the project switch.
- [Harnesses](docs/harnesses.md): Claude Code, Codex, pi, hooks, and Herdr.
- [Security](docs/security.md): trust boundaries, credentials, and cache data.
- [Contributing](CONTRIBUTING.md): tests and development workflow.
- [Changelog](CHANGELOG.md): release history.

Run the offline test suite with `python3 tests/test_routing.py`.

## License

[MIT](LICENSE).
