# `dotman search` command plan

Date: 2026-04-20

## Goal

Add a selector-discovery command that helps users find packages or groups before they `track`, `push`, or `pull` them.

Primary user question:

- "Which package or group should I use?"

This is not a tracked-state command and not a live-filesystem discovery command.

## Why this command should exist

- Current CLI model already defines selector search behavior across repos in repo `order`.
- That behavior is currently implicit inside other commands.
- Users need a direct way to browse likely package/group candidates without mutating tracked state.
- `list` is wrong shape because it implies inventory/state output.
- `search` is correct shape because it implies filtered lookup and discovery.

Relevant current CLI direction:

- selectors search across configured repos
- exact matches take priority
- partial selector input is allowed
- packages and groups both participate in selector lookup

## Locked command contract

```bash
dotman search <query>
```

Examples:

```bash
dotman search git
dotman search nvim
dotman search arch
dotman search work/git
dotman search main:git
```

## Scope

### In scope for v1

- search packages across configured repos
- search groups across configured repos
- search repo-qualified and bare selector text
- search package/group descriptions
- human-readable output
- JSON output
- deterministic result ranking
- zero mutation

### Out of scope for v1

- tracking or installing as part of search
- filesystem scanning for unmanaged paths
- target-name search
- full-text manifest search across arbitrary fields
- tag systems or package keywords
- interactive result picking
- fuzzy typo-correction beyond normal substring matching

## Terminology

- use `search` for selector discovery
- use `tracked` / `untracked` only for tracked package-entry state
- use `managed` / `unmanaged` only for live-path ownership and adoption flows

This keeps `search` focused on repo-side selector discovery and avoids overloading `untracked`.

## Search universe

`dotman search <query>` should search all configured repos from manager config.

Repos should be searched in ascending configured `order`.

Search should include:

- package IDs
- group IDs
- package descriptions
- group descriptions

Search should not be limited to currently tracked packages.

Reason:

- user intent is discovery before tracking
- tracked-only lookup is already covered elsewhere by `info tracked`

## Match model

Each result should be one logical selector candidate.

Candidate kinds:

- `package`
- `group`

Each candidate should expose at least:

- `kind`
- `repo`
- `selector`
- `qualified_selector`
- `description`
- `match_reason`
- `rank`

## Ranking rules

Results should be stable and deterministic.

Priority order:

1. exact repo-qualified selector match
2. exact bare selector match
3. prefix match on selector
4. substring match on selector
5. substring match on description

Tie-break order:

1. repo `order`
2. kind with `package` before `group`
3. shorter selector before longer selector
4. lexical `qualified_selector`

## Query interpretation

`<query>` should be treated as user text, not as a strict selector parser.

v1 interpretation rules:

- if query contains `:` then try repo-qualified matching against `repo:selector`
- always also compare against bare selector text
- compare case-insensitively for search matching
- preserve canonical case from repo manifests in output
- trim leading/trailing whitespace from query
- empty query should fail fast

## Output requirements

### Human output

Human output should print one line per result.

Format:

```text
<kind>  <repo:selector>  <description>
```

Examples:

```text
package  example:git   Base Git configuration
group    example:core  Core CLI packages
package  work:nvim     Neovim setup
```

Human output should:

- use canonical `repo:selector` form
- avoid dumping irrelevant internal fields
- show a clear no-match message when nothing matches

### JSON output

JSON output should be shaped like:

```json
{
  "operation": "search",
  "query": "git",
  "matches": [
    {
      "kind": "package",
      "repo": "example",
      "selector": "git",
      "qualified_selector": "example:git",
      "description": "Base Git configuration",
      "match_reason": "exact_selector",
      "rank": 1
    }
  ]
}
```

If no results match, JSON should still return success with an empty `matches` list.

## Exit status

- `0` for successful search, including zero matches
- non-zero only for invalid usage or runtime/config errors

Reason:

- zero matches is normal search output, not command failure

## Relationship to existing commands

- `search` does not mutate tracked state
- `search` does not run planning or execution
- `search` does not look at live filesystem state
- `search` helps users decide what to pass into `track`, `push`, `pull`, or `info tracked`

Future help text should avoid the word `install` and prefer:

- "Search packages and groups"
- "Find a selector to track or inspect"

This matches dotman's actual workflow better than package-manager language.

## Suggested CLI help text

Top-level command:

- help: `Search packages and groups`
- description: `Search packages and groups across configured repos`

Argument:

- `<query>`: `Search text for package or group selectors`

## Implementation shape

### Parser

- add top-level `search` subcommand
- accept single required positional `<query>`

### Engine

Add a search helper that:

- iterates configured repos in repo order
- gathers package and group candidates
- computes match reason and ranking
- returns sorted search results

Suggested new model type:

- `SearchMatch`

Suggested fields:

- `kind: str`
- `repo: str`
- `selector: str`
- `qualified_selector: str`
- `description: str | None`
- `match_reason: str`
- `rank: int`

### CLI emit

- add human emitter for search result rows
- add JSON emitter for search payload

## Test plan

### Parser and help

- `dotman search -h` shows explicit `<query>` placeholder
- top-level help includes `search`

### Matching

- exact package selector match ranks above partial matches
- exact group selector match ranks above partial matches
- repo-qualified exact match ranks above bare exact match
- prefix match ranks above substring match
- description-only match appears after selector matches
- matching is case-insensitive

### Repo ordering and tie-breaks

- earlier repo `order` wins ties
- package sorts before group at same rank
- shorter selector sorts before longer selector at same rank

### Output

- human output uses canonical `repo:selector`
- JSON output includes `operation`, `query`, and ordered `matches`
- zero-match human output is readable
- zero-match JSON output returns empty list

### Scope guards

- search returns untracked and tracked candidates alike
- search does not depend on tracked package state
- search does not inspect live filesystem

## Open questions

### Should search include target names in v1?

Recommendation: no.

Reason:

- user asked for package/group discovery
- target-name search is a different UX problem
- target names are not top-level selectors

### Should search be interactive?

Recommendation: no for v1.

Reason:

- plain ordered output is enough
- interactive pickers can come later if search grows many fields or actions

### Should zero matches exit non-zero?

Recommendation: no.

Reason:

- search semantics usually treat zero matches as normal result, not error

## Implementation phases

### Phase 1: tests first

- parser help tests
- exact/prefix/substring/description ranking tests
- repo-order tie-break tests
- human output tests
- JSON output tests
- zero-match tests

### Phase 2: engine search model

- add result model
- add search helper on engine
- add deterministic sorting

### Phase 3: CLI integration

- parser wiring
- command dispatch
- emitters

### Phase 4: docs

- add `search` section to `docs/cli.md`
- update any help/overview docs that enumerate commands

## Progress

- [x] v1 command direction drafted
- [ ] implementation started

## Decisions

- command shape: `dotman search <query>`
- scope: package and group discovery only
- naming: `search`, not `list`
- terminology: do not use `untracked` for this feature
- no mutation, no planning, no live filesystem scan

## Blockers

- none
