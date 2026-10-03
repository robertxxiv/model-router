# Harnesses: Claude Code, Codex, pi

There are three different things people mean by "use the router with my agent",
and they are not equally supported. Be clear which one you want.

| | What it means | Claude Code | Codex | pi |
| --- | --- | --- | --- | --- |
| **Route to it** | The router picks this harness and prints the command to start it | yes | yes | yes |
| **Hook into it** | The harness asks the router before spawning its own sub-agents, automatically | yes | not verified | no mechanism |
| **Call it yourself** | You run `router run` and act on the answer | yes | yes | yes |

Routing *to* a harness is the main use and works for all three. Hooking *into*
one needs the harness to expose a pre-spawn hook that can rewrite the model
before the spawn happens, and only Claude Code is confirmed to do that.

## Routing to a harness (all three)

This needs nothing from the harness. A catalog entry says which harness runs a
model and how to invoke it, and the router prints the matching Herdr command:

```json
{ "id": "claude:sonnet", "role": "WORKER", "harness": "claude",
  "model": "vendor-worker", "launchArgs": ["--model", "vendor-worker"] }

{ "id": "codex:plan", "role": "ORCHESTRATION", "harness": "codex",
  "model": "vendor-plan", "effort": "medium",
  "launchArgs": ["-m", "vendor-plan", "-c", "model_reasoning_effort=medium"] }

{ "id": "local-mid-64k", "role": "LOCAL_WORKER", "harness": "pi",
  "launchArgs": ["--model", "local-mid-64k"] }
```

`harness` becomes `herdr agent start … --kind <harness>`, and `launchArgs` is
passed to the harness after `--`:

```sh
herdr agent start worker-1 --kind claude --pane w1:p4 -- --model vendor-worker
herdr agent start plan-1   --kind codex  --pane w1:p5 -- -m vendor-plan -c model_reasoning_effort=medium
herdr agent start local-1  --kind pi     --pane w1:p6 -- --model local-mid-64k
```

Harness names are allowlisted in `herdr_state.py` (`KNOWN_KINDS`) and every
emitted argument is shell-quoted, because these lines are printed for a human to
run. An unknown harness is refused rather than printed.

Note what `launchArgs` buys you: Codex takes the reasoning effort as a `-c`
override, so one model at two efforts is two catalog entries sharing one
`model`. See [configuration.md](configuration.md#id-versus-model).

## Hooking into Claude Code (confirmed)

Claude Code fires `PreToolUse` before the `Agent`/`Task` tool runs, and a hook
may return `updatedInput`, which is what makes rewriting the model possible.
`hooks/route-subagent-model.sh` is a working example. Install it by pointing
`~/.claude/settings.json` at it:

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Agent|Task",
        "hooks": [
          { "type": "command",
            "command": "bash ~/.claude/hooks/route-subagent-model.sh",
            "timeout": 12 }
        ]
      }
    ]
  }
}
```

Configure it with environment variables rather than editing it:

```sh
MODEL_ROUTER=/path/to/model-router/route.py
MODEL_ROUTER_PYTHON=/path/to/.venv/bin/python
MODEL_ROUTER_NEXT_HOOK=/path/to/your/existing/hook.sh   # optional
```

It adds roughly 1.5s to a spawn, of which about 0.7s is importing the SDK and
0.25s is the API call; a cached judgment removes the call. It is deliberately
conservative and hands the spawn to `MODEL_ROUTER_NEXT_HOOK` unchanged — so an
existing hook is never lost — whenever:

- routing is disabled for the project (`router disable`)
- a model was named explicitly, because a deliberate choice always wins
- the spawn cannot take a model (Claude Code's `fork` ignores it)
- the router is missing, has no API key, or is slow or unreachable

**A spawn is never blocked.** A model chooser that can wedge your agent harness
is worse than no model chooser.

One constraint: Claude Code's sub-agents accept only the aliases
`haiku|sonnet|opus|fable`, never a full model identifier, and they cannot run a
locally served model at all. So `--for-subagent` returns an alias chosen from
the winning role, and `LOCAL_WORKER` — which has no in-process equivalent —
maps to the cheapest alias and says so in the hook's reason string.

## Codex

Codex has a hook system: `features.hooks = true` in `~/.codex/config.toml`, hook
definitions in `~/.codex/hooks.json` in the same shape Claude Code uses, and
hooks are trust-pinned by hash (there is a `--dangerously-bypass-hook-trust`
flag, which you should not need).

```json
{
  "hooks": {
    "SessionStart": [
      { "hooks": [ { "type": "command", "command": "bash /path/to/script.sh", "timeout": 10 } ] }
    ]
  }
}
```

**What is not established:** whether Codex exposes a pre-spawn event that can
*rewrite* a model the way Claude Code's `PreToolUse` + `updatedInput` can. Only
`SessionStart` is confirmed in use here. Until that is verified, treat Codex as
route-to and call-it-yourself, not hook-into. If you confirm a suitable event,
`route.py --for-subagent` already emits a single JSON line designed for exactly
this, and a PR adding the integration is welcome.

A `SessionStart` hook is still useful for advice rather than enforcement — for
example printing `router status` into a new session so the model in use and the
catalog are visible from the start.

## pi

pi has no hook mechanism: its settings expose providers, models, extensions and
MCP adapters, but no event hooks. Use it through the Herdr plugin or by calling
the router yourself:

```sh
router run "$TASK" --files src/thing.py     # read the chosen --model off the output
```

pi takes `--model <pattern>`, `--provider <name>` and `--models <patterns>` for
Ctrl+P cycling, so a catalog entry's `launchArgs` maps onto it directly. Models
served locally are marked `unmetered` in the catalog, which is what earns them a
scoring bonus — see [architecture.md](architecture.md#scoring).

## From Herdr, for any harness

The Herdr plugin is harness-agnostic, because it drives the router rather than
the agent:

```sh
herdr plugin link /path/to/model-router
herdr plugin action list --plugin model-router
```

That gives you `route a task`, `models`, `status` and `cache stats` as
keybindable actions and overlay panes. This is the integration that works
everywhere, and the one to reach for first.
