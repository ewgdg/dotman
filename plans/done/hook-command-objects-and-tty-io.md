# Hook command objects and per-command TTY IO plan

Date: 2026-04-22

Updated: 2026-04-22

## Goal

Extend hook command syntax so hook command lists can carry per-command execution metadata.

Initial target:

- support hook command objects with `run` and optional `io`
- support per-command `io = "pipe" | "tty"`
- keep privilege handling manual inside hook commands via explicit `sudo`
- preserve current repo/package/target hook ordering, `run_noop`, and guard semantics

This change should give hooks the same terminal-native escape hatch that reconcile already has, but without introducing dotman-managed privileged hooks yet.

## Why this change exists

Current hook metadata is hook-level only:

- one hook owns ordered command list
- commands are plain strings only
- all hook commands execute as piped, non-interactive subprocesses

That is too rigid for real hook workflows such as:

- prep command
- interactive command that needs real tty
- follow-up command

At same time, adding coarse hook-level `privileged = true` now would be misleading because many real hooks need mixed user/root behavior inside one ordered flow.

Per-command command objects solve the real missing shape without committing dotman to privileged-hook semantics yet.

## Scope

In scope:

- new `HookCommandSpec`-style normalized model for hook commands
- hook parser support for command objects in table-form hook definitions
- per-command `io = "pipe" | "tty"`
- execution support for hook commands that need tty
- JSON/info payload updates so command-object-backed hooks still render clearly
- docs and tests for command object syntax and tty behavior

Out of scope:

- dotman-managed hook privilege escalation
- `privileged = true` at hook or command level
- parallel metadata arrays such as `privileged = [true, false]`
- changing target `render` / `capture` / `reconcile` schemas
- reworking reconcile config in same change
- per-command hook selection rows
- hook parallelism

## Locked behavior

### 1. Hook ownership and ordering stay unchanged

Repo/package/target ownership rules stay exactly as they are now.

Execution order stays exactly as it is now:

- repo `guard_*`
- repo `pre_*`
- package `guard_*`
- package `pre_*`
- target `guard_*`
- target `pre_*`
- target action steps
- target `post_*`
- package `post_*`
- repo `post_*`

Within each hook, command list order stays declaration order.

### 2. `run_noop` stays hook-level

`run_noop` remains metadata on the hook definition, not on individual commands.

Reason:

- noop eligibility is owner-level retention policy
- command-level noop flags would create confusing partial-hook states

### 3. Command objects are supported only inside table-form hook definitions

Supported shorthand stays:

```toml
[hooks]
pre_push = "echo hi"
post_pull = ["echo one", "echo two"]
```

Supported table form becomes:

```toml
[hooks.pre_pull]
commands = [
  "echo prep",
  { run = "nvim some-file", io = "tty" },
  "echo done",
]
run_noop = true
```

Lock this rule:

- shorthand string/list forms normalize to plain command specs with `io = "pipe"`
- command objects are accepted only inside `commands = [...]` table-form hooks
- do not require or encourage mixed shorthand arrays under `[hooks] pre_push = [...]`

This keeps syntax readable and avoids edge-case TOML parsing ambiguity.

### 4. Command object schema

Per-command object shape for this phase:

```toml
{ run = "...", io = "pipe" | "tty" }
```

Rules:

- `run` is required and must be a non-empty string after trim
- `io` is optional and defaults to `"pipe"`
- unknown keys fail fast
- `io` values other than `"pipe"` or `"tty"` fail fast

No `privileged`, `cwd`, `env`, `timeout`, or soft-fail metadata in this phase.

### 5. Hooks remain unprivileged by dotman

Keep current contract:

- hooks never auto-escalate through dotman
- hook command objects do not add `privileged`
- if a hook command needs root, it must request root explicitly inside `run`, for example with `sudo`

Document this clearly.

Reason:

- manual `sudo` composes cleanly with mixed user/root workflows inside one script
- coarse dotman-managed hook privilege would still not solve mixed-identity flows well
- this plan should solve tty, not reopen privilege policy at same time

### 6. Per-command tty semantics mirror reconcile tty semantics

For hook commands with `io = "tty"`:

- execute attached to current terminal
- require interactive stdin/stdout/stderr tty
- do not capture or prefix stdout/stderr
- preserve terminal state on exit like reconcile tty path does

For hook commands with `io = "pipe"`:

- keep current piped streaming/capture behavior

If `io = "tty"` is selected in non-interactive context, execution fails fast with a clear error.

### 7. Guard semantics stay unchanged

Hook command objects do not change hook failure policy:

- command list stops on first non-zero exit
- `guard_*` exit `100` remains soft skip
- non-guard non-zero remains hard failure
- `post_*` remains success-only

TTY commands participate in the same semantics. Only I/O mode changes.

## Design notes

### Normalize hook commands explicitly

Current model stores hook commands as strings.

Introduce a normalized command spec, for example:

```py
@dataclass(frozen=True)
class HookCommandSpec:
    run: str
    io: str = "pipe"
```

Then update:

- `HookSpec.commands` from `tuple[str, ...]` to `tuple[HookCommandSpec, ...]`
- `HookPlan` to carry `io: str = "pipe"`

Planner already explodes one hook into one `HookPlan` per command, so this change fits the existing flow well.

### Keep planner/executor split clean

Manifest/repository layer should normalize command objects once.

Planner should then render one `HookPlan` per normalized command spec.

Executor should only consume final `HookPlan` fields like:

- `command`
- `cwd`
- `env`
- `io`

Do not leave raw manifest-shaped command payloads hanging around into execution.

### Reuse existing TTY execution path

`src/dotman/execution.py` already has terminal-native command execution for reconcile.

Prefer reusing that behavior rather than introducing a second ad hoc tty runner for hooks.

Likely end state:

- hook step calls `_run_command(... interactive=step.hook_plan.io == "tty")`
- `_run_command()` continues routing interactive commands to `_run_command_with_terminal()`
- tty requirement error stays explicit and consistent

### Payload/output shape

Need machine-readable output that preserves enough detail to explain what will happen.

At minimum:

- `HookPlan.to_dict()` should include `io`
- info/dry-run rendering should still show hook commands clearly
- if there is already hook command rendering shared between human and JSON output, extend it rather than forking display logic

Human output does not necessarily need a loud `[tty]` badge everywhere in this first pass, but the data model should make that possible.

## Implementation order

### 1) Tests first

Add or update tests for:

- package hook table-form `commands` parsing with mixed string and command-object entries
- repo hook table-form command-object parsing
- target hook table-form command-object parsing
- shorthand hook syntax still normalizing to `io = "pipe"`
- unsupported command-object keys failing fast
- invalid or empty `run` values failing fast
- invalid `io` values failing fast
- hook planning preserving per-command `io`
- hook execution using tty path only for commands with `io = "tty"`
- non-interactive execution failing cleanly for tty hook commands
- guard soft-skip semantics still working when guard command uses command-object form

### 2) Data model updates

Touch likely files:

- `src/dotman/models.py`
- `src/dotman/manifest.py`

Changes:

- add normalized hook-command model
- update `HookSpec.commands` type
- extend `HookPlan` with `io`
- update `to_dict()` helpers

Keep old external behavior stable for plain-string hook definitions.

### 3) Manifest/repository normalization

Touch likely files:

- `src/dotman/manifest.py`
- `src/dotman/repository.py`

Changes:

- extend hook parsing so `commands = [...]` items may be strings or command objects
- keep shorthand string/list support for simple hooks
- normalize all hook commands to command-spec objects with explicit defaults
- fail fast on unsupported command-object keys or invalid values

Do not broaden hook shorthand forms beyond what docs clearly describe.

### 4) Planning updates

Touch likely files:

- `src/dotman/planning.py`

Changes:

- render one `HookPlan` per normalized command spec
- copy command `io` into `HookPlan`
- keep existing repo/package/target hook env handling unchanged
- keep `run_noop` handling unchanged

### 5) Execution updates

Touch likely files:

- `src/dotman/execution.py`

Changes:

- hook execution chooses interactive path based on `hook_plan.io`
- enforce tty requirement consistently for tty hook commands
- keep hook privilege behavior unchanged (`privileged=False` unless future work explicitly changes policy)

Add concise comment where needed to explain why tty hooks share reconcile-style terminal handling.

### 6) Output/docs updates

Touch likely files:

- `docs/repository.md`
- possibly `docs/cli.md` or hook-rendering helpers if output changes need explanation

Docs must cover:

- shorthand vs table-form command-object syntax
- `io = "pipe" | "tty"`
- tty requirement
- hooks still do not auto-escalate; use explicit `sudo` inside command if needed

Do not put machine-specific paths in docs.

## Risks

### 1. Review/output under-describes tty commands

If dry-run/info output hides `io`, users may be surprised by interactive behavior during execution.

Mitigation:

- make JSON payload include `io`
- consider small human-readable annotation if existing output feels too implicit during implementation review

### 2. Over-scoping into privilege metadata

It will be tempting to add `privileged` once command objects exist.

Do not do that in this plan.

Need one clean feature landing:

- command objects
- per-command tty

Privilege policy is separate.

### 3. Parser complexity creep

Allowing command objects everywhere can make manifest grammar sloppy.

Mitigation:

- keep command objects table-form-only in this phase
- keep object schema tiny
- reject unknown keys hard

## Progress

- Added normalized `HookCommandSpec` and propagated per-command `io` through hook parsing, planning, JSON payloads, and execution.
- Added validation for table-form hook command objects: required non-empty `run`, optional `io`, hard failure on unknown keys and invalid values.
- Reused terminal-native execution path for hook commands with `io = "tty"` and added non-interactive tty failure coverage.
- Updated hook rendering/docs to surface tty-backed commands and documented explicit `sudo` requirement inside hook commands.
- Verified with full test suite: `uv run pytest -q`.

## Blockers

- None.

## Decisions

- Command objects are the right extension seam for hook execution metadata.
- This phase supports per-command `io` only.
- Dotman-managed privileged hooks stay out of scope.
- Manual `sudo` inside hook commands/scripts remains the recommended mixed-privilege pattern.
- Human hook rendering now annotates tty commands with `[tty]` so interactive behavior is visible in dry-run/info output.

## Scope changes

- None yet.
