# Target `.identifier` and hook summary UI plan

Date: 2026-04-18

Updated: 2026-04-18

## Goal

Redesign human-facing target identifiers from `repo:package (target)` to `repo:package.target`.

Use the freed parenthesis form for hook summaries where useful, especially synthetic hook-only selection rows.

This is a **UI grammar refactor**. It should make package, target, package-hook, and future target-hook rows fit one coherent naming system.

## Scope

In scope:

- human-facing identifier redesign for targets
- hook summary rendering for package hook-only rows
- docs, tests, and help text updates for the new identifier grammar
- separator and reserved-character decisions needed to keep the grammar unambiguous

Out of scope:

- hook execution semantics
- target-level hook execution model
- parser changes for tracked binding input unless required by the redesign
- JSON schema changes unless a field currently stores rendered human labels instead of structured data

## Locked behavior

### New identifier grammar

Human-facing identifier forms should become:

- package: `repo:package`
- package instance: `repo:package<instance>`
- target: `repo:package.target`
- instance target: `repo:package<instance>.target`
- package hook-only bucket: `[hooks] repo:package`
- package-instance hook-only bucket: `[hooks] repo:package<instance>`
- future target hook bucket: `[hooks] repo:package.target`
- future package-instance target hook bucket: `[hooks] repo:package<instance>.target`

If a hook summary is shown, it is annotation, not identity:

- `[hooks] repo:package (guard_push, pre_push, post_push)`
- `[hooks] repo:package<instance> (guard_push, pre_push, post_push)`
- `[hooks] repo:package.target (pre_push)`
- `[hooks] repo:package<instance>.target (pre_push)`

### Parentheses meaning

After this change, parentheses should no longer mean target identity.

New rule:

- identifier text before parentheses is canonical user-facing identity
- parentheses are optional compact summary or annotation only

### Color semantics

Because `.target` becomes part of canonical identity, target styling should not reuse annotation styling.

Color roles should be:

- repo prefix: low-emphasis context
- `:` and `.` separators: hint-style punctuation
- package segment: primary identity emphasis
- target segment: secondary identity emphasis, distinct from annotation
- parentheses and hook summaries: annotation only

Chosen target style for this plan:

- target segment uses `("2", "36")`
- `.` separator continues to use hint style, not target style

This keeps `repo:package.target` visually segmented while preserving the rule that punctuation and summaries remain annotation-like.

### Summary usage

Do not force hook summaries everywhere.

Default rule:

- synthetic hook-only rows should include the hook summary by default

Possible optional use later:

- compact dry-run summaries if needed

Do not append redundant summaries in views where hook names are already listed line-by-line below the package header.

### Character safety

`.` is now a reserved human-facing package/target separator for this plan.

Locked rule:

- use `.` as package/target separator
- do not allow `.` inside package IDs
- do not allow `.` inside target names

Implementation must enforce or validate this consistently wherever package IDs and target names are defined or edited.

## Why this change

Current shape:

- `repo:package (target)`

Problems:

- parentheses are overloaded visual syntax
- package hook summaries also want a compact annotation form
- future target hooks need a cleaner shared identity grammar
- `repo:package.target` scales more naturally across package/target/hook rows

Desired outcome:

- one compact identity system
- one separate annotation system
- no punctuation overloaded for two unrelated meanings

## Design notes

### Keep machine data structured

Do not replace structured fields with flattened strings in JSON or internal state.

This plan is about human rendering. Internal data should stay split by:

- repo
- package_id
- target_name
- hook_names

### Centralize rendering

Avoid scattered string formatting.

Likely touchpoints:

- `src/dotman/cli_style.py`
- `src/dotman/cli.py`
- `src/dotman/cli_emit.py`

Prefer introducing one canonical target-label helper and one hook-summary helper, then migrate callers.

Also update `AGENTS.md` so repo guidance matches the new canonical user-facing identifier grammar.

### Migration principle

Do not mix old and new label grammars across commands.

When implemented, migrate all major human-facing surfaces together so users do not see both:

- `repo:package (target)`
- `repo:package.target`

in the same release.

## Surfaces to audit

At minimum, audit:

- selection menu rows
- dry-run human output
- execution human output
- diff review banners
- help output and examples
- tracked info output where target labels appear
- any warnings or errors that print package-target labels

Check tests that assert exact rendered labels, color formatting, or compact-path output.

Also check that separator styling remains hint-style while target styling uses its own style.

## Implementation order

### 1) Audit and separator decision first

Add a focused inventory of every target label renderer and every exact string assertion that depends on `repo:package (target)`.

Also inventory validation and edit flows that accept package IDs or target names so `.` can be reserved consistently.

### 2) Tests first

Add or update tests for:

- target label rendering in plain output
- target label rendering in colored output
- package-instance target rendering uses `repo:package<instance>.target`
- `.` separator stays hint-styled in colored output
- target segment uses `("2", "36")`, not generic hint styling
- selection menu rows using `.target`
- diff/review banners using `.target`
- synthetic hook-only row rendering uses hook summaries in parentheses by default
- no accidental mixing of old and new label forms in the same command output
- validation or edit flows reject `.` in package IDs and target names

### 3) Rendering helpers refactor

Refactor label generation into centralized helpers.

Likely files:

- `src/dotman/cli_style.py`
- `src/dotman/cli.py`

Introduce helpers for:

- package identity
- target identity with `.target`
- hook summary annotation

Keep separator styling separate from target styling so `.` can remain hint-style while target text uses the target style.

### 4) Selection UI migration

Update selection menu rows to use new target identity grammar.

Package hook-only synthetic rows should become one of:

- `[hooks] repo:package`
- `[hooks] repo:package (guard_push, pre_push)`

Use the summary form by default for synthetic hook-only rows.

### 5) Payload and execution human output migration

Update dry-run and execution output to use new target identifiers consistently.

Do not add redundant summaries in sections that already list hook names below.

### 6) Docs and examples

Update:

- `docs/cli.md`
- `docs/repository.md`
- `AGENTS.md`
- any examples or tests that document target labels directly

### 7) Full test sweep

Run full suite after migration.

## Test matrix

### Target identifiers

- selection rows render `repo:package.target`
- selection rows render `repo:package<instance>.target` for package instances
- dry-run target items render `repo:package.target`
- execution output renders new identifier consistently
- diff/review headers or banners do not keep old paren target style

### Hook summary rows

- package hook-only selection row renders with summary by default
- when summary is shown, it renders in parentheses as annotation, not identity
- hook summary order stays stable and matches hook execution order

### Consistency

- no command emits both `repo:package (target)` and `repo:package.target`
- colored output still styles repo/package/target segments correctly
- colored output keeps `.` on hint style while target text uses target style
- compact path output remains unchanged except for label grammar
- package IDs and target names reject `.`

## Decisions

- Proposed target identity shape: `repo:package.target`
- Proposed package-instance target identity shape: `repo:package<instance>.target`
- Proposed annotation shape: parentheses reserved for optional summaries
- Synthetic hook-only rows should show hook summaries by default
- Target color style: `("2", "36")`
- `.` separator remains hint-styled
- `.` is reserved and not allowed in package IDs or target names

## Progress

- 2026-04-18: Plan created.

## Blockers

- None currently. Separator decision is locked: reserve `.` and reject it in package IDs and target names.

## Scope changes

- None yet.

## Non-goals

- no hook semantic changes
- no target-hook execution in this plan
- no hidden dual-format compatibility layer in human output unless migration risk proves it necessary
