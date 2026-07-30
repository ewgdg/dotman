# Interactive command resolution

## Goal

Implement GitHub issue #23 by giving track, untrack, add, and edit focused command-shaped resolution interfaces. Each interface owns its ranking, exact and partial matching, ambiguity errors, profile and package-instance rules, labels, and confirmation policy. A small interaction boundary supplies terminal and deterministic scripted adapters.

## Intention

Resolution should describe command behavior directly instead of configuring one wide generic CLI helper with command-specific strings and callbacks. The root CLI remains a composition layer for now; issue #24 will move the complete command workflows into command-local runners using the interfaces introduced here.

## Scope & Constraints

- Preserve candidate ordering, exact/partial behavior, profile and package-instance semantics, error text, fzf fallback, escape handling, prompts, and rendered labels.
- Test only the issue-approved seams: public command-family resolvers, production/scripted interaction behavior, and public CLI behavior.
- The shared interaction protocol is limited to genuine input operations: `choose`, `confirm`, and raw text entry for the add command's new package ID. Matching, validation, retry policy, and message construction stay in command resolution modules.
- Keep low-level field matching and ranking primitives in `resolver.py`. The four target command resolvers must not depend on the wide `resolve_candidate_match` policy helper; unrelated info, sync, and snapshot consumers remain outside this issue.
- Do not access private engine contexts or construct engines inside resolution modules. Use stable public engine/config/repository operations supplied by CLI composition.
- Do not retain compatibility aliases or forwarding wrappers solely for old tests.
- Fixed point for final review: `b5a204d`.

## Green Checkpoints

1. Add typed choice/confirmation/text requests plus terminal and scripted interaction adapters; characterize prompt selection, fzf fallback, escape, and confirmation behavior through the adapter seam.
2. Extract track resolution and confirmation policy into `TrackResolver`; migrate track tests to scripted interactions and remove track resolution/global interaction functions from `cli.py`.
3. Extract untrack resolution, including group/profile/package-owner behavior, into `UntrackResolver`; migrate observable tests and remove tracked-state forwarding usage from CLI resolution.
4. Extract add and edit resolution into `AddResolver` and `EditResolver`; preserve creation menus, validation loops, package/target ambiguity, repo selection, and exact non-interactive behavior.
5. Remove the target commands' dependency on the wide generic resolver and delete their obsolete callback/global seams, update code-structure documentation, run focused validation and typechecking, then run the full suite once.
6. Perform independent Standards and Spec reviews against `b5a204d`, resolve all findings, finalize this plan, and commit with a semantic message closing #23.

## Validation

- Red/green vertical slices use individual interaction and command-resolution test files.
- Focused command coverage: `uv run pytest -q tests/cli/test_track.py tests/cli/test_untrack.py tests/cli/test_add.py tests/cli/test_edit.py tests/cli/test_selection_ui.py` plus new resolver tests.
- Type validation after each command-family checkpoint: `uvx pyright` on changed modules where available and `uv run python -m compileall -q src tests`.
- Source scans confirm command resolution does not use private engine members, direct prompt monkeypatches, or the wide generic candidate resolver.
- Run `uv run pytest -q` once after all focused checks are green.

## Progress

- [x] Issue #23, blocker #21, repository guidance, domain vocabulary, current architecture, and approved test seams inspected.
- [x] Focused baseline green (`130 passed`).
- [x] Interaction checkpoint green.
- [x] Track checkpoint green.
- [x] Untrack checkpoint green.
- [x] Add/edit checkpoint green.
- [x] Combined focused command/resolution suite green (`155 passed`); targeted Pyright and compileall green.
- [x] Full validation and independent Standards/Spec review green (`991 passed`; targeted Pyright, compileall, and diff checks green).
- [x] Plan finalized for the semantic issue-closing commit.

## Decisions

- The issue itself pre-agrees observable command resolution and interaction as the test seams.
- `resolver.py` may rank already-defined options, but it must not receive command-specific headers, errors, or confirmation modes.
- Production and scripted adapters consume the same typed choice and confirmation requests so tests can assert what the user would observe without replacing global CLI functions.
- Resolution modules receive the already-composed public `DotmanEngine` facade. They do not construct engines or access its private planning/tracked-state contexts.
- `--yes` is confirmation policy, not menu policy: it prints replacement and override summaries, skips only confirmation input, and never chooses an ambiguous candidate.

## Surprises & Discoveries

- The focused baseline contains 130 tests, while resolution and menu policy currently occupy a large section of the 3,721-line `cli.py` facade.
- Issue #21 deliberately left public tracked-state read methods on the engine facade. The focused resolvers may consume those public operations, while private tracked-state and planning contexts remain behind the facade.
- Add creation needs raw package-ID entry in addition to choose/confirm. The interaction protocol exposes only raw text acquisition; `AddResolver` retains validation, retry, and error policy.
- The wide generic resolver still serves unrelated info, sync, and snapshot workflows. Removing those consumers would exceed issue #23; only track, untrack, add, and edit were detached from it.
- Review caught an exact-group/partial-persisted-entry overlap in untrack precedence. Interactive untrack now preserves the persisted-entry choice before falling through to exact group resolution; non-interactive exact-group behavior remains unchanged.
- `--yes` belongs to `TrackResolver`, not the generic interaction request. Human mode emits the replacement summary without reading confirmation input, while JSON mode applies approval without contaminating stdout.
- Interactive add validation retries only when CLI composition supplies an error sink; direct resolver use fails fast instead of silently hiding invalid input.

## Outcomes & Retrospective

- Track, untrack, add, and edit now resolve through focused command modules with one typed terminal/scripted interaction seam.
- Target-command policy and obsolete global callback seams were removed from `cli.py`, reducing it from 3,721 to 2,571 lines without pulling unrelated info/sync/snapshot resolution into scope.
- Public-seam regression coverage includes ordering, exact/partial ambiguity, profiles, package instances, fzf/prompt behavior, interruption, add creation/validation, untrack overlap precedence, and human/JSON `--yes` policy.
- Independent Standards and Spec reviews found six actionable issues across their initial and closure passes. All were fixed, and both final audits reported no remaining findings.
- Final validation: `991 passed`, targeted Pyright `0 errors`, compileall green, and `git diff --check` green. The full suite was run with inherited `NO_COLOR` unset because its ANSI rendering tests intentionally require color output.
