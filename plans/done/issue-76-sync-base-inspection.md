# Sync Base inspection and maintenance

## Goal and intention
Expose usable ancestry without observing drift or disclosing stale metadata,
allow deliberate exact reset, and safely maintain record-level state during
real operations.

## Scope and constraints
Issue #76 and parent #56 section 6: exact list/info/reset commands, aggregate
doctor diagnostics, shared applicability, secure storage, and real Sync/Push
maintenance. Read-only commands never execute Guards, projections, Observation,
or Verification Record access, acquire the manager lock, or clean up records.
Reset is immediate, idempotent, and manager-locked. Store-level failures must be
reported, never repaired.

## Work plan
1. Reuse static scope/metadata resolution and lifecycle inspection.
2. Add public engine/CLI workflows with metadata-only rendering.
3. Add aggregate doctor checks and executable contract tests.
4. Enforce store mode rejection and atomic record-level corruption cleanup.
5. Wire safe real-operation maintenance before review and applicable Guards.
6. Update durable documentation and validate affected workflows together.

## Completed implementation
- Metadata-only service, engine facades, exact parser/runner commands, shared
  Sync term styling, and aggregate numeric doctor counts.
- Usable Missing/Present detail, stable unavailable reason codes, redacted stale
  metadata, exact file/child/instance identities, and locked idempotent reset.
- Doctor suppresses child-orphan proof under repository, package, target, or
  Path Rule Guards without evaluating them. Exclusions, markers, incomplete or
  failed census also prevent absence proof.
- Store security rejects insecure modes rather than changing permissions.
  Security tests failed before removal of chmod repair.
- `discard_corrupt` revalidates within one write transaction and deletes the
  corrupt record or all references to a corrupt shared payload atomically.
  Tests failed before the API existed and cover rollback, shared references,
  unrelated-record preservation, healthy/absent records, and read-only denial.
- Shared lifecycle maintenance deletes proven selected stale/individually corrupt
  records during real Sync, preserving previews and unrelated partial selections.
  Configured-policy deletion precedes file Guards and child Path Rule Guards.
  Existing safe complete-directory reclamation remains in place.
- Real Push opts into configured-ineligibility cleanup after successful static
  ownership/conflict resolution, while holding the manager lock, before Guards
  and review. Selected unexcluded discovered children use effective Path Rules;
  excluded, absent or failed children and unrelated/eligible units are retained.
  Missing Base stores are not created. A failing-first CLI test reproduced the
  missing pre-Guard cleanup.
- Updated CLI, Sync, code structure, and storage documentation.

## Decisions and discoveries
- Reuse existing static Sync scope and metadata helpers without Observation.
- An absent record requires no Git HEAD or ancestry proof.
- Info/list preserve inspectability of typed Missing children. Only complete,
  unrestricted, unguarded census can support doctor child-orphan counts.
- Ordinary engine planning and dry-run remain read-only.
- Push acknowledgment remains an existing integration gap; this task adds only
  Push maintenance. Full Pull convergence/Base wiring belongs to issue #77.

## Validation
- Store suite: 121 passed (0.39s).
- Maintenance-focused operation coverage: 198 passed, plus selected-stale peer
  retention regression.
- Broad relevant Sync engine/CLI plus doctor/help/composition/emitter run:
  781 passed; two doctor Guard tests used the prior imported module while its
  final fix was being completed. The entire affected inspection/doctor/help/
  composition/emitter group then passed: 91 tests (2.58s), including both cases
  and new ancestor-Guard coverage.
- Nine Push maintenance regressions cover ineligible policies, preview, failing
  Guards, partial selection, eligible peers, invalid resolution, missing store,
  child policies/exclusions, and lock contention.
- Final Push/execute/composition plus planning/ownership/all directional Guard
  groups: 223 passed (1.97s).
- Final combined store/lifecycle/Sync session/inspection/Push-maintenance run:
  319 passed (14.66s).
- `git diff --check` passes. Full repository suite was not run. No commits made.

## Outcome
Issue #76 workflows, storage safety, shared lifecycle maintenance, real Sync
maintenance and real Push ineligibility cleanup are implemented and documented.
No remaining observed test failures. No ancestry acknowledgment was added to
Push, and no Pull convergence infrastructure was introduced.

## Contract review corrections
- Exact reset opens storage with creation disabled. A missing database or lock in
  existing store artifacts fails without changing surviving files or recreating
  missing files. Both regressions failed before the fix.
- Directory census and selected configured child policies resolve before
  repository/package/target Guards. Real Sync deletes proven ineligible child
  Bases before a later Guard failure; preview retains them. The frozen census is
  reused for Observation. Twelve failing-first cases cover all ancestor Guard
  scopes, exact/full selection, and real/preview behavior.
- Info and doctor share non-Git record/identity shape validation. Both file-shaped
  child records and child-shaped file records count as aggregate corruption
  without identities, Git ancestry work, or mutation. Both regressions failed
  before the fix.
- Final combined store/lifecycle/Sync session/inspection/Push maintenance tests:
  335 passed (11.81s). Directory-related engine group: 143 passed (9.06s).
  `git diff --check` passed. No commits created.
- Independent follow-up review confirmed all three blockers resolved without
  new blockers in the fix scope; its targeted run passed 350 tests.
- Parent final validation passed 390 store/lifecycle/session/inspection/Push
  maintenance/doctor/help/composition/emitter tests in 13.03s. Full repository
  suite was not run.
