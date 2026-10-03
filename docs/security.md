# Security model

The model router is a local advisory tool. It characterizes a task through
TypeSafe/Jev, applies local deterministic policy, and prints Herdr commands for
an operator to inspect and run. This document describes the security properties
implemented by the current code, not properties of TypeSafe, the inference
server, Herdr, or a model harness.

## Assets and trust boundaries

The main assets are API credentials, repository information contained in task
descriptions and measured facts, cached judgments about internal work, and the
integrity of commands presented to the operator.

The following values are data, not authority, and must be treated as untrusted:

- Task text, including text received by the subagent hook. It is sent to Jev as
  the assignment, but must not become router options or shell syntax.
- `models.json`. Catalog fields affect eligibility, ranking, and emitted command
  arguments. A catalog can be malformed or deliberately hostile.
- The response from the remote model-discovery endpoint. `discover.py` checks
  the top-level shape and model identifiers before constructing catalog entries,
  and limits the response body to 8 MiB.
- JSON returned by `herdr agent list`. Invalid JSON is rejected. The parsed
  structure is used only to suggest reuse; a reused worker name is validated
  again before it can appear in a prompt command. The code does not fully
  schema-validate every nested Herdr field, so malformed but valid JSON can
  still make the advisory probe fail.
- Repository file contents and names. `repo_facts.py` reads named files only to
  derive metadata such as line counts, suffixes, nearby tests, and a bounded
  `git diff --stat`; it does not send file contents to Jev. File names and those
  derived facts can still disclose repository structure to Jev.

Explicit path arguments are treated as local-user authority to select a
resource. This includes `--files`, `--catalog`, `--config`, `--task-file`,
`--judgments-file`, `--cwd`, and discovery's `--auth-file`. The router does not
sandbox those paths to the repository. Supplying one authorizes the associated
read or use, including an absolute path or a path outside the project. That
authority does not make the file contents well formed; parsers can still reject
them.

The local account and files deliberately installed or selected by that account
are otherwise inside the trust boundary. Protection against a malicious process
already running as the same user is out of scope.

## Defects addressed in the code

The following defect classes have explicit defenses:

### Task-text argument injection

The Claude Code hook passes its fixed router flags first, then an end-of-options
marker (`--`), then the task. A task beginning with an option-like string such
as `--task-file=...` therefore remains positional task text. The implementation
is in `hooks/route-subagent-model.sh`.

### Shell injection through emitted Herdr commands

Catalog data eventually appears in commands intended for a shell. Before a
harness kind is emitted, `herdr_state.safe_kind()` requires membership in
`KNOWN_KINDS`. Worker names must match `SAFE_NAME`, which permits at most 64
letters, digits, dots, underscores, and dashes and does not permit a leading
dash. `spawn_commands()` applies `shlex.quote()` to the working directory,
worker name, harness kind, and every launch argument. `prompt_command()` also
validates and quotes its worker name.

`catalog.py` has an additional, narrower `HARNESSES` allowlist for catalog
parsing. An unknown catalog harness is ignored rather than adopted.

### Symlinked cache state

`cache.Store.open()` refuses to use either a symlinked `.model-router` directory
or a symlinked `state.sqlite` path. A new database is created with
`O_NOFOLLOW`, exclusive creation, and mode `0600`. This prevents a checked-out
repository from intentionally redirecting the normal cache write through those
paths. The checks are not a defense against a same-user process racing or
replacing paths concurrently.

### Credentials in discovery diagnostics

`discover._safe_url()` parses diagnostic URLs, removes userinfo from the
authority, and removes query and fragment components. Network errors report
that sanitized URL rather than exception text from the URL library, which could
repeat the original credential-bearing URL.

## Secrets

The Jev credential is `TYPESAFE_API_KEY`. `route.py` obtains it from the process
environment or from `.env` in the router directory or its parent; an existing
environment variable takes precedence.

Discovery obtains an inference-server key from `LLAMA_API_KEY` or the `key`
field in the selected pi-style auth JSON file. Server URLs may come from
`LLAMA_BASE_URL`, `OPENAI_BASE_URL` in that auth file, or the corresponding
provider environment. The key is sent in an HTTP `Authorization` header.

Credentials must never be printed, logged, stored in `models.json`, or passed as
command-line arguments. Use environment variables or the auth file. The
discovery output intentionally stores model facts only; it does not write the
server URL or key into the catalog. `.env`, `models.json`, `config.json`, auth
files, and `.model-router/` are local files and must not be committed.

Task text and measured repository facts are sent to TypeSafe/Jev when a live
judgment is required. Do not place secrets in task descriptions. A cache hit or
`--judgments-file` avoids that live request.

## Data at rest

The cache is `.model-router/state.sqlite` beneath the detected project root. Its
`judgments` table stores:

- the typed judgment JSON, excluding `_usage`;
- a SHA-256 digest of the whitespace-normalized task, never the raw task text;
- SHA-256 digests of the repository facts and question definitions;
- the Jev model name, timestamps, and replay count; and
- a composite SHA-256 cache key derived from those inputs.

Judgments can reveal the nature, risk, scope, and difficulty of internal work
and should be treated as sensitive even though the raw assignment is absent.
The state directory is created and hardened to mode `0700`. The SQLite database
and existing `-wal` and `-shm` sidecars are hardened to `0600`. `_harden()`
ignores an operating-system error while changing permissions, so operators
should also rely on appropriate ownership and verify permissions on filesystems
that do not implement normal Unix modes.

## Command execution

The router does not execute the pane-split, agent-start, or agent-prompt commands
it prints. Its only Herdr subprocess is the read-only argument-vector invocation
`herdr agent list`; `--no-herdr` disables even that probe. Repository measurement
can separately invoke the read-only `git diff --stat` when `--diff` is supplied.

Printed commands are nevertheless executable output: their purpose is to be
copied into a shell, and machine-readable output includes the same command
strings for downstream consumers. For that reason catalog fields and other
interpolated data are validated or shell-quoted before emission. Operators
should still inspect commands before running them, especially when the catalog
or repository is not trusted.

## Reporting a vulnerability

Use the GitHub repository's **Security** tab to open a private security advisory.
Do not disclose a suspected vulnerability in a public issue.
