# Example development team policy

This fixture is the worked example of the file `roster.py` parses. It mirrors
the *structure* the parser depends on — the two headings and their table
columns — with invented identifiers, so the test suite runs on a clean clone
with no real policy file present.

Copy it, replace the identifiers with your own, and point `--roster` at it.

## Verified runtime model identifiers

| Role | Identifier | Where it is set |
| --- | --- | --- |
| Orchestrator | `gpt-example-mid`, effort `medium` | orchestrator config: `model`, `model_reasoning_effort` |
| Orchestration escalation | `gpt-example-mid`, effort `high` | per-launch: `-m gpt-example-mid -c model_reasoning_effort=high` |
| Cheap coordination | `gpt-example-cheap` | per-launch `-m` |
| Local bounded worker | `local-default-64k` is the default; `local-mechanical-64k` and `local-wide-262k` stay available for wider context. The orchestrator picks per task — see "Local worker model selection". | local agent settings: `defaultModel`; per-launch `--model` |
| Default worker | `claude-example-worker` | per-launch `--model` |
| Worker escalation | `claude-example-escalation` | per-launch `--model` |
| Optional specialist | `claude-example-specialist` | per-launch `--model`; never a default |

`claude-example-rejected` is rejected by the installed catalog — do not
configure it.

## Local worker model selection

All three local aliases stay available. The orchestrator chooses per task; do
not treat the default as the only option.

| Alias | Context | Reasoning | Images | Choose it when |
| --- | --- | --- | --- | --- |
| `local-default-64k` | 64K | `medium` | yes | Default. Bounded work in one module, a handful of files, clear acceptance criteria. |
| `local-mechanical-64k` | 64K | no | no | The task legitimately spans several files and does not need the model to reason much — mechanical refactors, lint and type fixes, boilerplate, repetitive edits. |
| `local-wide-262k` | 262K | `medium` | no | Wide repository sweeps, large files, or a long task thread that would otherwise compact. |

Rules:

- Start at the default alias. Move to a wider one when the scope genuinely does
  not fit, not pre-emptively.
- Running out of context is **not** a reason to escalate to a stronger tier;
  pick the wider alias instead. Escalate when the task is *reasoning*-hard.
