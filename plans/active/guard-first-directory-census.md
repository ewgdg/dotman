# Guard-first directory census and target-grouped Sync Bases

## Goal

Target Guards run before any directory scanning, including `ignore.command`.
A guarded-out target can never abort a session through its scan or its
command. Sync Base storage supports cheap reads scoped to a target.

## Intention

- `ignore.command` (plans/done/ignore-command.md) ran inside a census that
  runs before Guards. A target a Guard skips could still fail the whole
  session when its command failed.
- The census ran first only so configured-ineligibility Base cleanup (#76)
  could find children. That cleanup depends on configuration (child path plus
  Path Rules), not on the filesystem, so it can read the Base store instead.
- That exposed a storage flaw: records are flat, hashed files, so any scoped
  question means reading every record, including its content.

## Scope and constraints

Step 1, guards first:

- Configured-ineligibility cleanup for directory children reads the selected
  target's records from the store. It still runs before Guards, under the
  manager lock, never in preview, and never creates storage.
- Approved behavior change: ignored and absent children with an ineligible
  configured policy now lose their Bases too. Previously only discovered,
  unexcluded children did. Keeping a stale Base is the hazard #76 prevents.
- The census and `ignore.command` run after target Guards, and only for
  directory targets admitted in at least one direction. A target denied in
  every direction shows only its Guard skip rows; its children are not
  listed. Push and Pull already omitted them as no-route.
- The docs claim "target guards run before directory scanning" becomes true.

Step 2, grouped layout (internal to `SyncBaseStore`):

- `bases/<sha256(target identity)>/<sha256(unit identity)>.json`. Hashes stay,
  to avoid name length, case-folding and file/directory clashes.
- Exact reads compute their path; `target_records` lists one group; `scan`
  walks every group.
- Existing flat records move once, losslessly, in a separate migration module
  under the exclusive storage lock.

## Work plan

1. Tests first:
   - A guarded-out directory target with a failing `ignore.command` still
     opens in Push, Pull and Sync.
   - Child cleanup uses configuration, not discovery: ignored and absent
     push-only children lose their Bases, eligible ones keep them.
   - `target_records` returns only that target's records; it never matches a
     sibling target that shares a name prefix.
2. Add `SyncBaseStore.target_records(target)`. Move child cleanup to it.
   Move the census and the command after `evaluate_directional_guards`.
3. Docs: `docs/repository.md` Guard order, `docs/sync-base-storage.md`
   maintenance boundary, `docs/sync.md` census.
4. Commit step 1.
5. Step 2 tests first: migration preserves every record and identity; reads,
   replace, delete, scan and target listing work on the grouped layout;
   corruption in one group doesn't hide another group.
6. Implement the grouped layout and migration. Update storage docs. Commit.

## Validation

- Focused: sync session, push Base maintenance, Base store/lifecycle,
  inspection, ignore and directory tests.
- Full suite at the end of each step.

## Progress

- [x] Step 1 tests (5 failed first for the expected reasons)
- [x] Step 1 implementation, docs, commit (full suite: 2043 passed)
- [ ] Step 2 tests
- [ ] Step 2 implementation, docs, commit

## Surprises and discoveries

## Decisions

- Group by target, not package: cleanup, census and orphan checks all work per
  target, and a package is the union of its targets' groups.
- Interface first (`target_records` over the flat layout), then the layout
  change hidden behind it, so each step ships working.

## Outcomes and retrospective
