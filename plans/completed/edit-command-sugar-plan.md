# `dotman edit <query>` sugar plan

Updated: 2026-04-17

## Goal

Add optional top-level sugar:

```bash
dotman edit <query>
```

without weakening strict explicit forms:

```bash
dotman edit package <package>
dotman edit target <target>
```

The shortcut should help humans at terminal. It must not turn scripting behavior into guesswork.

## Why this needs a plan

This is small-looking UX sugar. It can still rot command semantics fast.

Risk areas:

- package and target namespaces overlap by design
- existing package resolver already supports partial matching and interactive selection
- target resolver now has package-scoped identity and its own ambiguity rules
- non-interactive / JSON mode must stay deterministic
- help text can get muddy if shortcut acts like hidden magic

So: lock rules first. Then implement.

## Locked decisions

### 1. Explicit subcommands stay canonical

These remain primary documented forms:

- `dotman edit package <package>`
- `dotman edit target <target>`

`dotman edit <query>` is sugar, not replacement.

### 2. Sugar is human-oriented, not script-oriented

Bare `edit <query>` may resolve interactively when needed.

In non-interactive or JSON mode, it must never guess.

### 3. Cross-kind resolution is shallow and explainable

Do not invent a new fuzzy resolver.

Use existing package and target resolver data. Bare `edit <query>` is only a wrapper that:

- collects package candidates
- collects target candidates
- decides whether one result is safe to open directly
- otherwise prompts or errors

### 4. Repo-qualified target syntax keeps meaning

If query shape is explicitly target-shaped, like:

```bash
dotman edit beta:nvim.init.lua
```

then sugar should treat it as target-intent first, not run cross-kind magic.

Likewise, plain package-shaped queries should still allow package exact hits.

Need exact parsing rules below.

## Proposed behavior

## 1. Accepted forms

Sugar accepts one positional query:

```bash
dotman edit <query>
```

Supported query intent shapes:

- package-like: `[repo:]package`
- target-like: `[repo:]package.target`
- bare token: `name`

## 2. Resolution flow

### A. If query parses as explicit target shape

Meaning selector contains package + target separator in target position.

Examples:

- `nvim.init.lua`
- `beta:nvim.init.lua`

Then:

- run tracked-target resolver only
- do not also try package resolution
- same behavior as `dotman edit target <target>`

Reason: explicit shape should win. No mixed-kind surprise.

### B. Otherwise, evaluate both kinds

For bare token or package-like query without target shape:

- gather package exact + partial matches
- gather target exact + partial matches
- decide from combined candidate set

## 3. Auto-open rules

Bare sugar may auto-open only when result is clearly unique.

### Safe auto-open cases

Open directly when exactly one exact match exists across both kinds.

Examples:

- one exact package, zero exact targets
- one exact target, zero exact packages

### Not safe to auto-open

Do not auto-open when:

- package exact and target exact both exist
- multiple exact matches exist in one kind
- there are no exact matches and only partial matches remain

Reason: partials are discoverability sugar, not strong identity.

## 4. Interactive behavior

If interactive TTY and auto-open is not safe:

- show one combined selection prompt
- label candidate kind clearly: `package` or `target`
- show repo context using existing rendering style
- preserve current resolver-quality labels

Suggested prompt header:

```text
Select an edit target for '<query>':
```

Suggested labels:

- `package  repo:pkg`
- `target   repo:pkg.target`

The menu should prefer exact matches before partial matches.

If there are exact matches, show exact matches only.
Do not bury exact hits under partial noise.

If there are no exact matches, show partial matches from both kinds.

## 5. Non-interactive / JSON behavior

If auto-open is not safe:

- fail with exit `2`
- explain why
- print candidates in deterministic order
- never prompt

JSON mode should return a structured error through existing command error path. No menu.

## Parsing rules

## 1. First-dot split for explicit target syntax

For target intent, split package/target on first `.`.

This preserves quoted target names such as `init.lua` under package `nvim`.

Examples:

- `nvim.init.lua` => package `nvim`, target `init.lua`
- `repo:nvim.init.lua` => repo `repo`, package `nvim`, target `init.lua`

## 2. Bare token is ambiguous by policy, not by parser

Example:

- `git`

This stays syntactically neutral.
Resolver decides whether it maps to:

- exact package
- exact target
- both
- neither

## 3. No binding syntax here

Bare sugar should not accept profile binding syntax as a third mixed mode.

If user wants profile-sensitive package identity, explicit package subcommand remains clearer.

This keeps v1 sugar narrow.

## Selection and error UX

## Prompt text

Use one combined prompt:

```text
Select an edit target for '<query>':
```

"edit target" here means thing to open, not CLI `target` noun.
That wording needs a code comment if used, because local context can look misleading later.

## Candidate formatting

Combined candidate rows should include:

- kind (`package` / `target`)
- rendered repo/package label
- target suffix for target entries

Possible display:

```text
1. package  fixture:git
2. target   fixture:git.gitconfig
```

Keep formatting aligned but simple.

## Error text

Need distinct errors for:

- exact cross-kind ambiguity
- same-kind ambiguity
- no matches

Examples:

- `edit query 'git' is ambiguous: package fixture:git, target fixture:git.gitconfig`
- `edit query 'cfg' is ambiguous: target fixture:git.gitconfig, target fixture:altgit.gitconfig`
- `edit query 'missing' did not match any tracked package or target`

## Non-goals

- no fuzzy ranking beyond current exact/partial semantics
- no filesystem fallback to arbitrary local paths
- no live-package resolution outside tracked state
- no profile/binding sugar in bare mode
- no hidden preference like "package always wins" when target exact also exists

## Implementation phases

## Phase 1: tests first

Add focused tests for bare `edit <query>`:

### Exact unique cases

- unique tracked package opens package root
- unique tracked target opens repo source path
- explicit target-shape query routes to target resolver only

### Ambiguity cases

- exact package + exact target same query => interactive prompt
- two exact targets same bare query => interactive prompt
- same cases fail in non-interactive mode
- same cases fail in JSON mode

### Partial-only cases

- one partial package + zero exacts => interactive choose or error, no silent open
- mixed partial package/target => interactive combined chooser
- partial-only non-interactive => error

### Help cases

- `dotman edit [-h] [<query>]` or equivalent usage text is clear
- `edit package` and `edit target` help remain explicit

## Phase 2: parser and command wiring

- add optional bare query slot under `edit`
- keep subparsers intact
- route bare form to new wrapper handler
- keep backward compatibility for explicit forms

Need care here: argparse shape can get ugly fast when command has both optional positional sugar and explicit subcommands.
If parser gets messy, prefer a tiny dispatch shim over clever parser tricks.

## Phase 3: combined resolver wrapper

Implement a small helper that:

- gathers package candidates without interactive selection
- gathers target candidates without interactive selection
- classifies exact vs partial
- returns one safe match or combined candidates

Do not duplicate package/target match-field logic.
Reuse installed/target match helpers already added.

## Phase 4: selection UI

- add combined candidate rendering
- ensure fzf path and plain prompt path both show kind clearly
- preserve deterministic ordering

Suggested ordering:

1. exact packages
2. exact targets
3. partial packages
4. partial targets

Or, if UX testing shows better clarity:

1. all exact by display label
2. all partial by display label

Pick one and lock it in tests.

## Phase 5: docs

Update `docs/cli.md` and help text:

- explicit forms are canonical
- bare `edit <query>` is convenience sugar
- ambiguous queries may prompt interactively
- non-interactive / JSON mode require unambiguous input

## Done criteria

- bare `dotman edit <query>` works for unique exact package and target matches
- explicit target-shaped queries route directly to target resolver behavior
- ambiguous cross-kind cases prompt interactively and fail elsewhere
- partial-only cases never silently auto-open
- help text stays understandable
- explicit `package` / `target` forms still behave exactly as before
- tests cover mixed package/target ambiguity matrix

## Recommendation before coding

Do this only if we still want shortcut after using strict commands a bit.

Strict form already good. Sugar useful, but only if implemented with hard limits.
If we feel any urge to add hidden priority rules, stop and cut scope.
