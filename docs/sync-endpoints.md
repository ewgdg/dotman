# Sync endpoint convergence

Sync freezes each file endpoint as `Missing` or `Present(bytes)`.
A missing file is not an empty file, and absence is evidence—not a separate
Observation state. Each unit is Directly InSync, Drifted, or Observation Failed.

## Comparison and outcomes

- **push-only:** compare the repository-derived live outcome with frozen live
  state. A Missing repository produces a Missing live outcome.
- **pull-only / both:** use the configured repository/live comparison.
  Missing endpoints remain Missing rather than invoking a transform on invented
  empty bytes. Use live can therefore create an empty repository file or delete
  a repository file, according to the actual frozen endpoint.
- **push-only-delete:** compare desired Missing against live presence. The only
  Resolution Intent is **Use repository**. Approval deletes live presence and
  retains the repository source; Render, Capture, and configured comparison
  commands do not determine this deletion. Already-missing live state is
  Directly InSync.

Review materializes exact typed outcomes without approving them. An empty file
creation and a deletion remain distinct effects throughout review and execution.
Base acknowledgment stores committed repository ancestry, not Proposal bytes;
a valid Missing Base is usable ancestry.

## No-write Proposals

A drifted Proposal with no required filesystem effects still needs Approval.
It never becomes Directly InSync merely because materialization produces no
writes.

For configured `pull-only` or `both`, Converged requires a successful per-unit
Base acknowledgment. Failed acknowledgment leaves the unit not Converged.
For configured `push-only` or `push-only-delete`, approved no-write work completes
without a Base or substitute receipt. Transient Guard narrowing does not change
configured-policy Base eligibility.

No-write work does not create a live snapshot or activate directional hooks by
itself. Unapproved work remains pending and receives no acknowledgment.

## Unsupported endpoints

Directories, FIFOs, sockets, and other non-regular endpoints produce typed
Observation failures instead of being interpreted as Missing or read as files.
Repository symlinks are unsupported; live symlinks follow the configured
file-symlink policy.

Failed units stay visible and non-approvable. They do not prevent unrelated
approved interactive units from executing; the final result still reports the
failed units honestly. Unattended operation must reject Observation failures
before mutation.

Executable endpoint contracts: `tests/engine/test_sync_endpoint_convergence.py`.
See [Sync](sync.md) for the session and policy workflow.
