# Sync checkpoints independent of Git

## Goal and intention

Implement the accepted protocol in GitHub issue #83: retain the repository-space
outcome of qualifying synchronization without requiring Git commits, and make
optional checkpoint failures distinct from successful managed effects.

## Scope and constraints

- Preserve configured Base eligibility, identity, interpretation applicability,
  Source Change ownership, directional hook ordering, and frozen reviewed effects.
- Publication qualifies after required effects; repository-only work reuses proof
  or freezes forward validation before Apply. Direct agreement remains sufficient.
- Base-only failures warn and continue. Required operation failures remain
  fail-fast without rollback. Explicit Merge still requires a usable Base.
- Replace SQLite with private atomic self-contained per-unit file records.
  Preserve integrity, locking, typed Missing, and child executable state.
- Remove old protocol paths, Git provenance, and SQLite-specific tests/docs.
  Any existing-state cleanup is separate and one-off, never runtime compatibility.
- Do not fix unrelated outstanding issue #56 work.

## Work plan

1. Implement storage/lifecycle and Push, Sync, Pull integration with focused
   failing regressions before fixes. Update human/JSON inspection and warnings.
2. Update glossary, lifecycle, storage, CLI, and contributor documentation to
   describe the implemented protocol, not issue history.
3. Independently review protocol compliance and security-sensitive storage;
   address real defects without expanding scope.
4. Run targeted tests throughout and the full suite once ready to finish.
   Commit task-owned changes at meaningful boundaries and record results.

## Validation

Use existing public Base-store/lifecycle, session, engine, and CLI seams with
real temporary filesystem resources. Cover dirty Push/reset and independent
edits, non-Git operation, interpretation changes, direct/no-write outcomes,
raw/transformed/patch/Editor Pull, frozen qualification, child modes/Missing,
storage warnings versus actual failures, partial completion, inspection, locks,
security, atomic replacement, and read-only previews. Avoid internal SQLite tests
or incidental implementation coupling. Record exact commands and outcomes below.

## Progress

- Accepted design confirmed; implementation explicitly authorized by the user.
- Initial worktree clean. Existing issue #56 plan is outside this plan's ownership.
- Storage implementation committed as `7c32954` and `d7f64b7`; integration and
  documentation remained unverified at the previous handoff.
- Continuation established that the old worker is unavailable in this Workflow
  and no other worker is active in the checkout. Existing task changes preserved.
- Astra integration committed as `3841451`: frozen qualifying Push/Pull/Sync
  outcomes, warning/acknowledgment separation, inspection, CLI styling/output, and
  removal of deferred Push Render. Parent owns documentation, final full-suite
  verification, and issue publication.
- Independent read-only protocol review completed; 113 focused tests and a real
  dirty-Push/revert probe passed. Its Render-deferral finding was already fixed;
  final high-value reuse/history regressions committed as `cb910f4` (actual Git
  reset/checkout, unchanged Editor retaining volatile Render qualification).
- Glossary, lifecycle, CLI, exact storage format/layout, and contributor guides
  reconciled with implementation and the explicit post-commit durability warning.
- Focused issue #56 replacement reviewed against the original remote snapshot
  and published after validation; unrelated requirements preserved and #56 remains
  open. Issue #83 now explicitly records the post-commit durability boundary.
  Both remote bodies were read back and verified.

## Verification checkpoints

- Independent storage review: `timeout 60 uv run pytest -q
  tests/engine/test_sync_base_store.py` — 20 passed in 0.11s. Real temporary-file
  probes also confirmed three gaps: post-rename flush ambiguity, corrupt-record
  enumeration blocking healthy records, and missing payload-corruption distinction.
  Focused regressions and fixes assigned; the passing baseline did not cover them.
- Storage review fixes committed as `7a82eaf` (corruption isolation/classification)
  and `346397e` (truthful post-commit durability outcome). Red-first regressions
  reproduced each gap. `timeout 30 uv run pytest -q
  tests/engine/test_sync_base_store.py` — 28 passed in 0.05s.
- Integration: `uv run pytest -q tests/engine/test_sync*.py
  tests/engine/test_pull*.py tests/engine/test_push_checkpoints.py
  tests/engine/test_repository_checkpoints.py tests/engine/test_checkpoint_observation.py
  tests/engine/test_execution.py tests/engine/test_projection_configuration.py
  tests/cli/test_sync*.py tests/cli/test_push*.py tests/cli/test_pull*.py
  tests/cli/test_checkpoint_warnings.py tests/cli/test_execution_rendering.py
  tests/test_operation_runner.py tests/test_diff_review.py` — 809 passed in 51.04s.
  Follow-up obsolete-storage assertion change: `uv run pytest -q
  tests/engine/test_sync_auxiliary.py tests/engine/test_push_checkpoints.py` —
  31 passed in 0.41s.
- Final history/Editor regression files: `uv run pytest -q
  tests/engine/test_push_checkpoints.py tests/engine/test_repository_checkpoints.py`
  — 25 passed in 0.34s.
- First full suite: `timeout 300 uv run pytest -q` — 3 failed, 1773 passed in
  59.12s. Failures were old Push CLI action/timeline expectations in
  `tests/cli/test_execute.py` that omitted the checkpoint step. Healthy fixture
  state directories also needed private modes. Corrected in `e95c8b6`;
  `uv run pytest -q tests/cli/test_execute.py` — 31 passed in 0.50s.
- Final full suite: `timeout 300 uv run pytest -q` — **1776 passed in 58.97s**.
- Documentation: `git diff --check` passed after reconciliation. Confirmed shared
  test fixtures isolate HOME and XDG config/data/state before final test execution.

## Decisions and discoveries

- Additional writes already precede primary writes and publication; their failure
  blocks later execution. No new acknowledgment dependency mechanism is needed.
- Normal Base writes are per-unit; shared-payload deduplication was the source of
  special cross-record SQLite maintenance. Self-contained files remove that need.
- Concrete durability failure case: directory `fsync` can fail after atomic rename
  has already replaced the record. No single-path protocol can honestly promise
  preservation of the previous record at that point; rollback is another fallible
  write, and adding a manifest/journal retains the same final-flush uncertainty.
  Keep rename as logical commit: pre-commit failure preserves the prior record;
  post-commit flush failure reports acknowledgment plus explicit uncertain crash
  durability. This filesystem-boundary clarification must appear in docs and issue
  evidence, not be hidden as a successful durable save or ordinary failed save.
- Two review observations predate this task (confirmed against baseline `2387253`):
  Push requires an existing explicit file source, and both-policy file Observation
  omits exact chmod from its direct-agreement test. They are outside the checkpoint
  change and remain under unrelated issue #56 scope; no opportunistic fixes added.

## Outcomes

Implementation, independent storage/protocol review, regression fixes, full-suite
validation, and human/structured-output documentation are complete. Storage and
integration source/tests are committed at the boundaries recorded above.

The result checkpoints the final qualifying repository outcome independently of
Git and treats optional Base problems separately from required operation failures.
Corrupt records remain isolated; committed-but-unflushed records are reported
truthfully rather than mistaken for preserved prior state.

No real user checkpoint state was migrated or deleted. Existing commit-backed
state requires a separate one-off cutover; production contains no compatibility
backend or automatic repair. Unrelated issue #56 gaps remain outside this task.

Validation logs and reviewed issue snapshots are retained under
`~/.agents/artifacts/outputs/dotman/2026-09-23/issue-83-checkpoints/`.

### Retrospective

Passing focused tests did not cover every contract boundary: real post-rename
flush injection exposed an impossible blanket failure-preservation promise, and
malformed identity enumeration showed why corrupt-count inventory is distinct
from direct record lookup. Keep both regressions at the storage seam and keep
completion, acknowledgment, and durability warnings separate at operation seams.
