# Two-sided Sync convergence

## Goal

Implement GitHub issue #56 and its subissues #57–#78 as one dependency-ordered feature branch while keeping every checkpoint executable and reviewable.

## Intention

Add a one-shot `SyncSession` that observes selected repository and live state once, exposes policy-constrained proposals through immutable views and semantic commands, freezes approved work, and executes repository changes before live publication. Preserve explicit one-sided Push and Pull behavior while moving their shared mechanics behind focused modules.

## Scope and constraints

- The parent issue is the authoritative behavioral contract.
- Hard-cut configuration and command contracts; do not retain aliases or compatibility branches.
- Grow the feature through working vertical slices rather than horizontal scaffolding.
- Tests exercise public seams: resolved plans and emitted configuration for the initial hard cut, then `SyncSession` commands/views/results, CLI behavior, and real temporary Git/filesystem/SQLite resources.
- Keep the existing command runtime as the process-execution seam.
- Keep CLI and engine facades thin.
- Update executable tests and durable user documentation in the same checkpoint as behavior.

## Work plan

1. **Configuration foundations (#57–#59)**
   - Hard-cut Render, Capture, comparison, Editor, preset, Additional Source, policy, named Path Rule, exclusion, scope, and canonical identity contracts.
   - First tracer: manifest → resolved target/path rule → existing Push/Pull planning and structured output, with command-backed projection execution.
2. **Base foundations (#60–#61)**
   - Add the secure fixed-epoch SQLite object store and the policy-aware Base lifecycle module.
3. **File SyncSession tracer (#62–#66)**
   - Add frozen Observation and the one-shot session interface.
   - Converge push-only, pull-only, both/Merge, deletion-only, Missing, and no-write file outcomes.
4. **Capability, authoring, and approval (#67–#70)**
   - Add directional Guards and auxiliary work, transactional Proposal editing, canonical Additional Source approval, and the Command Deck adapter.
5. **Frozen execution and directories (#71–#75)**
   - Execute Repository Apply before Live Publication with fail-fast partial results.
   - Add child census, independent convergence, topology/root effects, and symlink modes.
6. **Operations and handoff (#76–#78)**
   - Add Base list/info/reset/doctor behavior.
   - Move permanent Pull onto shared convergence modules.
   - Finalize exact CLI, unattended, JSON, exits, documentation, and full regression coverage.

## Validation

- Run the smallest targeted test file after every red/green slice.
- Run affected engine and CLI suites at each issue checkpoint.
- Run the full suite before marking the plan done and before making the PR ready for review.
- Inspect `git diff --check`, generated CLI help, structured output, and durable documentation links.

## Progress

- [x] Created `feat/issue-56-sync-convergence` from the finalized domain-contract baseline.
- [x] Confirmed the parent-specified test seams and mapped the current implementation.
- [x] Complete #57 hard-cut projection and Editor configuration (1,053 tests passing; independent review accepted).
- [x] Complete #58 policy, named Path Rule, and unified exclusion hard cut (1,058 tests passing; independent review accepted).
- [x] Complete #59 canonical Sync scope and identity resolution (1,067 tests passing; independent review accepted).
- [x] Complete #60 secure fixed-epoch Sync Base storage (1,162 tests passing; independent security and portability review accepted; native macOS unverified).
- [x] Complete #61 Base applicability and lifecycle (1,261 tests passing; independent review accepted).
- [x] Run final validation for the #57–#61 foundations checkpoint.
- [ ] Open one combined foundations PR; keep #56 open.
- Remaining #62–#78 work is paused by explicit user direction. Do not start it without new authorization.

## Decisions

- Submit the existing #57–#61 work in one PR; the user declined splitting this checkpoint. Stop implementation after #61.
- Keep commits bounded by subissue/checkpoint even though the PR is aggregated.
- Use the public `SyncSession` seam required by #56 rather than exposing private plans or persistence handles.

## Surprises and discoveries

- The current code still exposes the superseded `reconcile` and `pull_view_*` configuration throughout models, planning, execution, and structured info output. #57 therefore requires a real vertical hard cut rather than an additive parser change.
- Existing tracking state and live snapshots are not suitable Sync Base storage; the Base object store must remain separate.

## Outcomes and retrospective

Pending.

## #60 security correction

- Replace the uncommitted WAL/lifetime-writer-lock design with a fixed rollback-journal store and transaction-scoped nonblocking locks.
- First reproduce schema-filter bypass, read-side mutation, orphan sidecars, index-only corruption, forged payloads, and reader contention.
- Validate untrusted database bytes only in memory before any writable SQLite open; preserve all pre-existing recovery evidence rather than opening it for recovery.
- Pin managed directories, database and lock to validated descriptors; recheck inode bindings at transaction boundaries. Record the remaining same-user/native SQLite pathname race boundary explicitly.
- Check security before commit, never raise a post-commit validation failure. Add adversarial filesystem and rollback tests, then targeted/full tests, compileall and whitespace validation.

### Security-correction validation

- Red tests reproduced schema-filter bypass, rejected-open lock creation, sidecar consumption/orphan acceptance, missing full-index validation, forged payload acceptance, and reader lifetime-lock contention.
- Additional red tests reproduced post-mkdir permission repair of a substituted directory and lack of rejection for a SQLite build forcing disk temp storage; both corrected.
- Focused store suite: 84 passed.
- Full suite: 1,151 passed in 4.99s.
- `uv run python -m compileall -q src tests`, targeted Ruff checks/format and `git diff --check` passed.
- Read-only snapshots are freshly copied into SQLite memory under shared transaction-scoped locking; no manager lock is taken.
- Architecture review must explicitly consider optional deserialization support, O(database size) memory/full scans, and the documented same-UID/native-VFS race boundary. Owner accepted that same-UID/root isolation is outside the private-tree contract, not blanket approval of the implementation.
- No commit or push made.

### #60 portable runtime correction

- Scope: only portable writable connections and pre-creation runtime capability
  rejection; preserve preflight/integrity costs, sidecar rejection and inode checks.
- Red tests reproduced descriptor-filesystem dependence, missing-deserialize
  acceptance/late failure, and absent pre-creation platform/SQLite checks.
- Writable SQLite opens now use the validated ordinary file URI with `mode=rw`
  under the existing transaction lock; no platform branches or descriptor paths.
- Capability checks run before layout creation and raise a typed unsupported-runtime
  error for missing POSIX primitives, SQLite STRICT support, or deserialization.
- Kept the existing adversarial tests; made the post-mkdir substitution test itself
  portable by using its known fixture paths rather than a descriptor filesystem.
- Validation: 95 store tests passed (all 84 prior cases plus 11 regressions);
  full suite 1,162 passed in 4.97s. Compileall, targeted Ruff check/format and
  tracked/untracked whitespace checks passed.
- Real-resource fault injection runs on Linux, not native macOS. No same-UID/root
  isolation or preflight redesign added. No commit or push made.


## #61 implementation checkpoint — review pending

- Read the exact #61 scope, full parent #56, domain context, current scope/store
  contracts, and current Git plumbing documentation before implementation.
- Added one shared Base lifecycle seam for configured-policy eligibility,
  canonical file/child identities, frozen Git facts and profile/input
  fingerprints, read-only applicability, direct acknowledgment, approved
  unit-owned completion, and pre-Guard/pre-review policy deletion.
- Extended the unshipped fixed-epoch record schema with required commit OID,
  object format, fingerprint, and provenance. No migration or compatibility
  path was added; envelope and payload replacement remain one transaction.
- Real Git checks cover SHA-1/SHA-256, committed checkout conversion, unchanged
  real index, literal paths, one batched Primary-path status observation,
  ignored/untracked/staged provenance, Missing, executable children, ancestry,
  and ambient Git/replace-ref isolation. Git infrastructure failure is not
  silently treated as a missing commit.
- Lifecycle tests cover all configured policies, each approved intent,
  no-write results, direct sharpening, skipped/reused/preview work, changed
  Pull non-acknowledgment, early selected-policy maintenance, hidden unavailable
  metadata, record/payload corruption, and failed real SQLite replacement
  preserving both the prior unit record and an earlier committed unit.
- TDD began with the missing public lifecycle seam. Additional red regressions
  reproduced fatal Git failures being labeled missing commits, acceptance of
  a Primary Source of dot, and an untyped missing Git executable failure.
  A final red regression showed a bad committed shape discarded successful peers;
  checkout now retains unit-local typed failures while sharing the one frozen
  status observation. Real required-filter failure isolation is also covered.
  Each regression was fixed and rerun before broad validation.
- Validation: 192 focused lifecycle/store tests passed in 4.60s; full suite
  1,259 passed in 9.54s. Compileall, focused Ruff check/format, and tracked
  whitespace validation passed. Native macOS execution remains unverified.
- Shared lifecycle docs, storage documentation, contributor module orientation,
  and the documentation index are current. No user-facing command or UI changed.
- Scope boundaries remain explicit: no SyncSession/Observation orchestration,
  Base CLI, manager-lock adapter, or directory census/orphan reclamation.
  Operation adapters must pass configured (not Guard-narrowed) policy, resolved
  JSON-compatible profile/variable context, and truthful final unit-owned effect
  outcomes, then invoke lifecycle boundaries in the documented order.
- Await independent review; not accepted or committed. No push performed.


### #61 read-only Git and real-ancestry correction — review pending

- Reproduced both review blockers with new tests before editing production code.
  A real `file://` single-branch promisor clone downloaded an absent remote Base
  commit during read-only inspection and added four pack-related files. A real
  `info/grafts` entry made an unrelated root commit appear to be a usable Base.
- Base Git commands now require `--no-lazy-fetch` and bind `GIT_GRAFT_FILE` to
  the platform null device, alongside the existing disabled replace refs and
  isolated ambient Git selectors. There is no fallback that can enable fetching.
- The partial-clone test verifies the commit exists remotely but not locally,
  snapshots all Git file contents, and checks Git Trace2 for absence of fetch or
  upload-pack child execution. Inspection now reports `commit_missing` without
  Git object-store changes. The graft test first proves ordinary Git accepts the
  forged edge, then proves Base inspection reports `history_changed` and leaves
  the graft input untouched.
- Consulted the current Git manual for local-only object access and current
  upstream environment/setup/commit implementation for graft-input control.
  Updated lifecycle and contributor docs with local availability and actual
  committed-parent semantics.
- Validation: 194 focused lifecycle/store tests passed in 4.90s; full suite
  1,261 passed in 9.50s. Compileall, focused Ruff check/format, and whitespace
  checks passed. All regression subprocesses use Command Runtime.
- Still bounded to #61, pending review; no later ticket, commit, or push.
