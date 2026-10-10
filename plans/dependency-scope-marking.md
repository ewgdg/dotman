---
status: done
---

# Dependency scope marking and `--no-deps`

## Goal

Make package dependency inclusion in `sync`, `pull` and `push` visible and optional:

1. Mark rows that entered scope only through a package dependency closure.
2. Add `--no-deps` so package inputs can skip their dependency closure.
3. Remove the redundant `Fallback: <reason>` line and carry the guess on the Resolution instead.

## Intention

A package input such as `dotman pull claude` also syncs the drifted targets of
packages `claude` depends on. Those rows are sorted in with the requested
rows and nothing explains why they appear. Including dependencies stays the
default because `depends` declares hard requirements; the change is to make it
legible and give a deliberate escape hatch.

The Fallback line repeats the Sync Base status (`Sync Base: unavailable` beside
`Fallback: absent`; the review even shows `Base reason: absent` too). Its only
unique job is telling NO_COLOR users that a Resolution is guessed.

## Scope & Constraints

- Default scope semantics stay unchanged; `--no-deps` is opt-in.
- `--no-deps` affects package inputs only. Target inputs never expanded a closure.
  Selector-less runs cover the whole tracked state, so the flag has no effect there.
- Ownership and collision validation keep covering the full tracked graph.
- A target is "included via" a package only when it is not itself requested: a
  target input, or a target of a package input's own package, counts as requested.
- Dependency marking:
  - Workset: the Target label of a dependency row is dimmed (no width cost).
  - Detail panel: an `Included via:` fact listing the requesting package inputs.
- Fallback rework:
  - Detail panel: drop the Fallback fact; the warning-colored Resolution column and the
    Sync Base line already say it.
  - Review and command output: drop the `Fallback:` line; annotate the Resolution as
    `Use repository (guessed)` so the guess survives NO_COLOR.
  - JSON keeps `fallback_reason` and `resolution_guessed` unchanged.
- Out of scope: JSON exposure of included-via, review-page dependency marking.

## Work Plan

1. Scope provenance: `ResolvedSyncScope.included_via` maps a target key
   `(repo, package_id, bound_profile, target_name)` to the requesting package
   identities. `resolve_sync_scope(..., include_dependencies=True)` controls closure.
2. Session rows: `SessionRow.included_via` filled from the scope at session open.
3. Deck: dim dependency Target labels; add the `Included via:` detail fact.
4. CLI: `--no-deps` on `sync`, `pull`, `push`, threaded to scope resolution.
5. Fallback rework in detail panel, review and command output.
6. Docs (`docs/cli.md`, `docs/sync.md`), skill, README deck screenshot if it changes.

Commits: feature 1–3, feature 4, cleanup 5; docs travel with their change.

## Validation

- `tests/engine/test_sync_scope.py`: included-via provenance; `--no-deps` drops the closure.
- `tests/cli/test_sync_deck_textual.py`: dependency detail fact; Fallback fact gone.
- `tests/cli/test_sync_deck_command.py`: guessed annotation replaces the Fallback line.
- Targeted test files, then the full suite once at the end.

## Progress

- [x] Scope provenance and deck marking (aa9f66c)
- [x] `--no-deps` (de97d1a)
- [x] Fallback rework (b56c0a6)
- [x] Docs and skill; the README screenshot is unaffected (selector-less run with usable Bases)

## Surprises & Discoveries

- The review repeated the cause three times (`Sync Base`, `Base reason`, `Fallback`);
  only the guess itself was unique information.
- Dependency rows sort before their requesting package because scope order follows the
  closure order; marking makes that ordering legible without changing it.

## Decisions

- Mark by dimming plus a detail fact instead of folding dependency rows: folding hides
  unreviewed changes behind one keypress and duplicates `--no-deps`. Demo:
  `~/.agents/artifacts/outputs/dotman/2026-10-03/dep-rows-demo/index.html`.
- `--no-deps` follows pip / docker compose naming.
- Target inputs never expand a closure. Before, they carried their package's dependency
  selections, so `push main:app.own` ran `base`'s `run_noop` hooks; nothing documented or
  tested that, and naming one target is the narrowest scope. `--no-deps` therefore only
  matters for package inputs.
- Requesting one directory child counts as requesting its whole target, so its sibling
  children are not marked as dependency rows. Rare overlap; not worth child-level provenance.

## Outcomes & Retrospective

- Default scope semantics unchanged; dependency rows are dimmed with an `Included via:`
  detail fact; `--no-deps` keeps a package input to its own targets.
- The unused `Fallback` style term was removed with the line it styled.
- Dependency Probe and hook rows are marked like units: provenance is keyed by canonical
  package and target scope label, so auxiliary rows resolve it from their scope.
- Follow-up candidates, not done: expose included-via in JSON `sync_units`; mark
  dependency units in the review page.
