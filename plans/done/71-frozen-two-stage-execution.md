# Frozen two-stage execution (#71)

## Goal and intention
Execute the approved frozen set in operation-wide Repository Apply then Live
Publication, keeping exact ordered partial outcomes rather than implying rollback.
Normative contract: GitHub #56 section 7, with sections 4 and 6 for Approval and
per-unit Base completion.

## Scope and constraints
Reuse current file-target, Additional Source, auxiliary hook, runtime, atomic IO,
and snapshot machinery. Directory discovery/topology remain #72 onward; this
change does not introduce aggregate directory execution or new CLI interactions.
Do not re-observe, materialize, or project during execution. Parent reviews and
commits the completed changes.

## Work plan
1. Add regressions for missing completion failure evidence, partially applied
   units skipped by publication, ordered no-write completion, and unattempted work.
2. Freeze stage steps and journal success/failure/unattempted boundaries, retain
   nested hook order, check cancellation at each boundary, and preserve Bases.
3. Update affected result rendering style and durable execution docs.
4. Run focused engine and CLI coverage and report remaining risks.

## Validation
Tests first, then focused Sync stage/session/convergence/auxiliary/Additional and
CLI result tests. Verify lazy snapshot, chmod/interruption, stage separation and
unchanged frozen payloads using observable filesystem and result assertions.

## Progress
- Read issue #71, normative #56, domain glossary and Guard ADR.
- Existing stages already freeze payloads, nest hooks, and snapshot lazily.
  Gaps: publication acknowledgment failures lack a failed step; repository-success
  units can be mislabeled skipped; ineligible no-write units bypass their ordered
  Repository Apply position; cancellation is not checked between frozen effects;
  later steps have no explicit unattempted evidence.

## Decisions
- Keep current file-only scope; directory child support belongs to subsequent issues.
- Retain fail-fast non-transactional execution and the established Command Runtime.

## Completed checkpoints
- Added failing regressions first: publication acknowledgment lacked an exact
  failure step, repository success was overwritten as skipped after push pre-hook
  failure, and cancellation was not checked between content and chmod.
- Reused one shared stage-order builder for both destination stages. The frozen
  step sequence now drives execution and ordered unattempted-tail reporting;
  successful completion remains represented by per-unit outcomes rather than
  redundant success steps. Failed/unattempted completion boundaries are explicit.
- All approved units traverse Repository Apply, including ineligible no-write
  outcomes. Units awaiting publication retain not-converged repository progress.
  Additional failures retain their result and leave both stage tails unattempted.
- Snapshot creation remains lazy, after Repository Apply and push pre-hooks;
  snapshot failure explicitly leaves the intended mutation unattempted.
- Added observable regressions for repository/package/target order, operation-wide
  separation, frozen bytes despite source-changing hooks, partial repository
  failure, no-write order, and chmod/interruption preserving authoritative Bases.
- Updated shared result styles, structured result tests, CLI, Sync execution and
  code-structure documentation. Updated old attempted-only assertions to include
  the new unattempted evidence. No-write tests now open real sessions so they have
  captured execution metadata rather than bypassing the session-open boundary.

## Validation and outcomes
Final focused command:

```sh
uv run pytest -q tests/engine/test_sync_{failure_honesty,repository_apply,publication,pull_convergence,both_convergence,auxiliary_execution,additional,session,convergence,endpoint_convergence,auxiliary,selection_contract}.py tests/cli/test_sync_{failure_results,command_runner,interrupt,deck_command,auxiliary_ui,additional_ui,pull_ui}.py
```

267 passed in 42.42s, including real process interruption and CLI reporting.
`git diff --check` passed. The full unrelated repository suite was not run.

## Remaining scope and handoff
Implementation and independent read-only correctness review complete; no concrete
regressions found in stage ordering, failure retention, cancellation or completion.
Parent verification reran stage/failure and CLI rendering coverage: 44 passed in
2.97s; `git diff --check` passed.
Directory census, child topology and directory-root publication remain the
subsequent directory issues, not new functionality in this file-session change.
No dependencies, configuration schema, one-sided Push/Pull orchestration or
storage formats changed.
