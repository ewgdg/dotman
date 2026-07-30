# Non-sync command-local runners

## Goal

Implement GitHub issue #24 by moving every non-sync CLI workflow out of the
global callback bag and into focused command-family runners composed by the
root entrypoint. Commands construct only the configuration and engine state
their own workflow requires.

## Intention

The parsed command should select one focused runtime boundary. Standalone
commands run without a `DotmanEngine`; inspection commands receive read-only
operations and state-changing commands receive the approved track, untrack,
add, and edit resolution interfaces. Push, pull, and restore retain their
existing execution dispatch until issue #25; their callback interface becomes
sync-only.

## Scope & Constraints

- Preserve all public CLI text/JSON output, color, interruption, and exit-code
  behavior.
- Test only the issue-approved seam: calls to the public root `dotman` CLI.
  Runner internals and callback invocation order are not test seams.
- Keep configuration loading lazy: configuration-independent commands must not
  load manager configuration or construct an engine; configuration-only
  commands must not construct an engine.
- Root composition owns terminal interaction, UI scoping, engine construction,
  and top-level `ValueError`/`KeyboardInterrupt` normalization.
- Track, untrack, add, and edit use the focused resolution interfaces delivered
  by issue #23. Do not restore generic resolution policy or private engine
  access.
- Do not retain forwarding wrappers, no-op defaults, or compatibility fields
  for migrated callback consumers.
- Update user-facing style dependencies whenever command output moves.
- Fixed point for final review: `ca02663`.

## Green Checkpoints

1. Characterize public root-dispatch behavior for configuration timing, UI
   scope, errors, exit codes, and ambient `NO_COLOR`; add root composition that
   can be exercised without patching global CLI functions.
2. Move configuration-independent and configuration-only standalone workflows
   into a focused standalone runner. Remove their callback fields and dispatch
   branches from the global bag while keeping focused root tests green.
3. Move list, info, search, and doctor into focused inspection runners with
   lazy engine/configuration construction and explicit UI scope. Remove their
   callback fields, wrappers, and implementation-coupled dispatch tests.
4. Move track, untrack, add, and edit into focused state-changing runners using
   `TrackResolver`, `UntrackResolver`, `AddResolver`, and `EditResolver`.
   Remove their callback fields and keep existing public command behavior green.
5. Narrow the remaining callback interface and wiring to push, pull, and restore
   only, update code-structure docs, and run focused type/test validation. Full
   sync command-runner migration and callback-interface removal belong to #25.
6. Run the complete suite once, perform independent Standards and Spec reviews
   against `ca02663`, resolve all findings, finalize this plan, and commit with
   a semantic message closing #24.

## Validation

- Red/green slices use root CLI test files for each migrated command family.
- Focused tests run after every slice; the affected command file runs before a
  wider root CLI selection.
- Run `uvx pyright` on changed source/test modules and
  `uv run python -m compileall -q src tests` regularly.
- Source scans confirm migrated callback names, no-op defaults, and global
  monkeypatch seams are gone.
- Run `uv run pytest -q` once after all focused checks are green, with color
  tests explicitly controlling `NO_COLOR` themselves.

## Progress

- [x] Issue #24, blocker #23, repository guidance, domain vocabulary, current
  callback architecture, and approved public test seam inspected.
- [x] Focused baseline green (`340 passed`) after explicit color isolation.
- [x] Standalone runner checkpoint green (`48 passed`).
- [x] Inspection runner checkpoint green (`62 passed`).
- [x] State-changing runner checkpoint green (`68 passed`).
- [x] Global callback bag narrowed to sync/restore only; focused CLI validation
  green (`338 passed`); targeted Pyright and compileall green.
- [x] Full validation and independent Standards/Spec review green (`989 passed`
  with ambient `NO_COLOR=1`; final reviews reported no findings).
- [x] Plan finalized for the semantic issue-closing commit.

## Decisions

- Issue #24 explicitly pre-agrees public root command behavior as the test seam.
- Command-family runners are the composition units: standalone, inspection,
  state-changing, and sync/restore. Individual workflows remain command-shaped
  methods within the focused family rather than fields in one cross-command
  callback structure.
- Engine creation stays lazy inside only the runners whose selected command
  needs the engine. Root composition supplies factories, not eagerly built
  engines.
- Issue #25 owns push, pull, restore, and final removal of the remaining
  callback dispatch interface. Issue #24 must not pre-implement that ticket.

## Surprises & Discoveries

- Issue #23 intentionally left all workflow control in `cli_commands.py`; its
  plan names issue #24 as the follow-up that should move those complete flows.
- Configuration-only commands already avoid engine construction, but they still
  depend on the same callback bag as sync execution and inspection commands.
- Issue #25 explicitly reserves push, pull, restore, and the final global
  callback-record deletion, so this plan leaves a narrowly named sync runtime.
- The inherited `NO_COLOR=1` environment exposed three ANSI assertions that did
  not control their color precondition. The tests now explicitly remove the
  variable only where they assert colored output.
- Config-only interactive edit commands loaded `ui.menus` but ran outside its
  context, so terminal menus silently used the default bottom-up order. The
  state runner now scopes those commands with the loaded manager UI config and
  resets the scope on return.

## Outcomes & Retrospective

- Standalone, inspection, and state-changing workflows now each expose one
  command-family runner interface and own their complete configuration,
  resolution, mutation, and rendering flow.
- Root composition declares command-family ownership, selects exactly one
  non-sync runner, and fails fast for an unhandled command. Push, pull, and
  restore remain behind the required sync-only runtime for issue #25.
- The global callback record fell from 46 unrelated fields to 14 required sync
  fields. All migrated default no-ops, root forwarding emitters, embedded
  standalone workflows, and callback-heavy dispatch tests were deleted.
- Public root coverage now explicitly protects configuration independence and
  config-only UI scoping. Color-on tests isolate `NO_COLOR` and pass in the
  inherited `NO_COLOR=1` environment.
- Initial Standards review found runner selection in the wrong module, a silent
  success fallback, and repeated cross-family command probing. Root-owned
  declared selection and fail-fast runner interfaces resolved all findings;
  the final Standards and Spec reviews were clean.
- Final validation: `989 passed`, focused CLI `338 passed`, targeted Pyright
  `0 errors`, compileall green, and `git diff --check` green.
