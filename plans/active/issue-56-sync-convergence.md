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
- [ ] Complete #61–#78 in dependency order.
- [ ] Run final validation and open the PR.

## Decisions

- Use one branch and PR because the user explicitly requested the umbrella implementation together.
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
