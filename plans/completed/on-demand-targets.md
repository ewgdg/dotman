# On-demand targets

Date: 2026-10-02

## Goal

Add a target-level manifest field `on_demand = true`. An on-demand target runs
only when the user names it exactly (`repo:package.target`). Package selectors
and selector-less `push`/`pull` skip it.

## Intention

Some targets are expensive or update-only, such as a probe that asks a network
remote whether a custom Git build is behind upstream. Today every plan runs
every tracked target, so such a check slows every `dotman push`. Callers worked
around this with environment switches read inside the probe, which hides the
behavior from the manifest. On-demand targets make "run this only when asked"
a declared property, selected by name from callers such as Topgrade:

```toml
[targets.niri_custom_git_update]
sync_policy = "push-only"
on_demand = true
probe = 'bash "$DOTMAN_PACKAGE_ROOT/scripts/needs_update.sh" --check-upstream'
```

```bash
dotman --unattended push niri-custom-git.niri_custom_git_update
```

## Scope & Constraints

- Target-level only. No package-level default and no `path_rules` support
  until a real use needs them; a package default can later follow the
  `sync_policy` precedent (package default, target override) without breaking
  anything.
- Boolean, default `false`. Existing manifests behave exactly as before.
- Applies to every target kind (file, directory, probe) and to both operations.
  `sync_policy` still gates by operation on top of this.
- Package inheritance/override merges like `disabled` (an override may set it).
- Naming: `explicit` already means a tracked root package (vs `implicit`
  dependency) and `Selection` is the Command Deck checkbox term in
  `CONTEXT.md`, so neither is used. The term is **On-demand Target**.
- Static ownership and collision validation keep running over the full tracked
  graph, including on-demand targets, so selecting one by name never meets a
  conflict that a plain plan would not report.

## Behavior

1. `dotman push` / `dotman pull` with no selector: on-demand targets are not in
   scope.
2. Package selector (`niri-custom-git`, including packages reached through the
   selector's dependency closure): on-demand targets are not in scope.
3. Exact target selector (`niri-custom-git.niri_custom_git_update`): the target
   is in scope as usual.
4. Not silent: when a package selector skips on-demand targets of a package the
   user selected, the human output shows one notice naming each skipped target
   and saying to select it by name, e.g.
   `on-demand target skipped: main:niri-custom-git.niri_custom_git_update (select it by name to run it)`.
   JSON output includes the skipped identities. Selector-less runs stay quiet
   (skipping is the declared default there).
5. Hooks follow selected target work as today, so a package whose only selected
   work is an on-demand target runs its hooks only when that target is selected.

## Work Plan

1. Tests first (see Validation); see them fail for the expected reason.
2. Manifest: add `on_demand` to `TARGET_MANIFEST_KEYS`, the target model, the
   parser (`bool`, like `disabled`), and override merging.
3. Scope: in `sync_scope.resolve_sync_scope`, skip on-demand targets in the
   package-selector and selector-less branches; keep them in the exact-target
   branch. Record skipped ones from package selectors for the notice.
4. Output: surface the notice in human output and the identities in JSON. Prefer
   an existing notice/diagnostic path; keep it to one line per skipped target.
5. Docs: `docs/repository.md` (target fields), `docs/cli.md` or `docs/sync.md`
   where selector semantics are described, `CONTEXT.md` glossary entry for
   On-demand Target, and the agent skill in `skills/dotman/`.

## Validation

- Selector-less push/pull dry run does not plan an on-demand probe or file
  target.
- Package selector does not plan it and emits the notice (human) and the
  identity (JSON).
- Exact target selector plans it and runs its probe.
- `on_demand` absent or `false` keeps current behavior.
- Unknown-key validation still rejects typos; `on_demand` must be a boolean.
- Full suite: `uv run pytest -q` (about two minutes).

## Progress

- [x] Tests written and failing (unknown manifest key `on_demand`)
- [x] Manifest field
- [x] Scope filtering and notice
- [x] Docs, glossary, skill
- [x] Full suite green

## Surprises & Discoveries

- Probe targets could not be selected by name from `push`/`pull`/`sync` at
  all: CLI inputs go through the tracked identity resolver, which listed only
  path-owning targets, so `main:app.check` failed with "did not match any
  tracked package or target". Fixed first as its own commit; edit queries still
  never resolve to probes.
- `list sync-bases` and `doctor` resolve the selector-less scope. Filtering
  on-demand targets there would hide their Bases and count them as orphaned, so
  inspection asks for every tracked target (`include_on_demand=True`).
- The plan says "of a package the user selected": the notice covers only the
  selector's root package. Dependencies' on-demand targets skip quietly, like a
  selector-less run, so habitual meta-package pushes stay quiet.

## Decisions

- Target-level over package-level: the gated unit is one action inside a
  package (install stays automatic, the upstream check waits). Package-level
  would split one package into two sharing a PKGBUILD and raise dependency
  questions (is an on-demand package pulled in by `depends`?). Scored 4.45 vs
  2.9 for package-only and 4.2 for both.
- Field name `on_demand` over `manual`, `opt_in` (clashes with "Sync is
  opt-in"), and `selection = "explicit"` (clashes with two glossary terms).

- Notice reuses the planning-skip lines (`[skipped] <identity> (...)`, dimmed
  like Guard skips): `[skipped] main:app.check (on-demand: select it by name to
  run it)`. It prints in the preview log, above the execution timeline (also
  after the Deck, which has no row for it), and in `--report`. JSON adds
  `on_demand_skips: [{"identity": ...}]`, shaped like `guard_skips`.

## Outcomes & Retrospective

- Shipped as planned, target-level only. An exact selector next to its package
  selector (`push app app.check`) runs the target and does not report it.
- Follow-up candidate: an executed exact probe run logs `[pending]
  main:app.check / Probe Work` because probe work has no execution stage; this
  predates the feature.
