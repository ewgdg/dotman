# Sync lifecycle

A file target is one **Sync Unit**. Each regular-file child of a directory target
is an independent Sync Unit; a directory root has no aggregate Base.

## Frozen file Observation

A SyncSession observes its resolved file-target scope once. For each direction,
repository, package and target Guards narrow capability before endpoint reads
and comparison. It retains the resolved scope order, effective projections, typed
endpoint bytes, live mode/link evidence, Git facts and applicable Base evidence.

| Effective policy | Direct comparison |
| --- | --- |
| `push-only` | Repository-derived live bytes and configured file mode against live |
| `pull-only`, `both` | Configured repository and live comparison projections |
| `push-only-delete` | Desired `Missing` against live presence |

`Missing` differs from a present empty file. Each unit is exactly **Directly
InSync**, **Drifted**, or **Observation Failed**. Direct agreement has no drift
row; drift has one stable canonical row, initially unapproved. Failed
Observation and Base acknowledgment diagnostics stay visible and non-approvable.
A Guard-removed route is a visible diagnostic, not permission to use the opposite
direction. A unit-local failure does not discard unrelated evidence.

Base availability describes frozen pre-acknowledgment evidence; a separate
acknowledgment flag records successful opening-time maintenance.

External changes never refresh an open session. Start another session to see
new filesystem, configuration or Git state.

## Session lifetime and current engine boundary

The engine currently opens file-target sessions. It supports immutable views,
whole-unit inclusion/exclusion, semantic abort and a terminal observation-only
Execute result. Inclusion is not Approval. This boundary does not yet materialize
or approve Proposals, edit sources, publish changes, run hooks, or discover
directory children or auxiliary work; it adds no `sync` CLI command.

Execute never claims drift is Converged: included drift is `pending` and makes
the result `incomplete`; deliberately excluded healthy drift is `excluded`.
Observation or acknowledgment failures make the result `failed`. A result with
only direct agreement or deliberately excluded healthy rows is `completed`.
Abort returns `aborted`; it does not undo Base maintenance already committed
while opening.

A real session owns the manager's non-blocking operation lock from opening until
execute or abort. Real Push and Pull command workflows take the same lock before
planning and retain it during review and execution. A conflicting operation
fails immediately. Use a session context manager or explicitly abort an
abandoned session; session state is neither persisted nor resumable.

Preview cannot execute and does not take or create the manager lock, write
managed repository/live/Git/state files, acknowledge or clean up Bases, run
hooks, or create snapshots. Cleaned-up private scratch for projections and
isolated Git checkout is permitted. Configured projection providers remain
trusted side-effect-free stdout producers, not sandboxed arbitrary programs.
Comparison-owned Capture views may run during Observation; no Proposal Capture
or reconciliation is performed.

## Sync Bases

A Sync Base is committed repository ancestry, not the latest working-tree,
Capture, Editor, Render, or live output. File payloads are `Missing` or
`Present(bytes)`; directory children additionally preserve Git executable state.
Exact live permission policy is not a Base payload.

| Configured Sync Policy | Base-Eligible |
| --- | --- |
| `pull-only`, `both` | Yes |
| `push-only`, `push-only-delete` | No |

Guards narrow the available route for an operation, not Base eligibility.
Changing between eligible policies preserves ancestry.

### Applicability and provenance

A usable Base has a valid identity and envelope, intact payload, matching
effective interpretation inputs, and an available real commit provably ancestral
to the operation's frozen current HEAD using actual committed parents, not
repository ancestry overrides. Inspection never fetches missing objects from a
promisor remote: availability means locally available. `Missing` is a valid
usable payload.

Interpretation includes the Primary Source, effective Render and Capture,
profile context, applicable Path Rules, and symlink interpretation modes.
Configured policy, Guard outcomes, Pull Views, exact chmod, and the live link
chain or resolved referent are not fingerprint inputs.

The recorded representation comes from an isolated checkout of the frozen real
commit, including checkout conversion. Provenance is **exact** only when the
frozen path-scoped Git status says the Primary Source is clean and the final
Proposal has no Primary Source Change. Otherwise it is **conservative**.
Direct agreement has no Proposal, so only frozen status determines provenance.
Additional Sources and byte comparisons never sharpen that classification.

### Acknowledgment and completion

| Boundary | Base behavior |
| --- | --- |
| Fresh direct agreement in real Push, Pull, or Sync | Eligible participating units may acknowledge immediately, before review or hooks |
| Approved drift resolution in Sync | Acknowledge only after every unit-owned Primary and live effect succeeds |
| Successful eligible Push publication | Acknowledge at the unit's completion boundary |
| Drifted Pull replacement | Never acknowledge or claim Sync convergence |
| Approved no-write resolution | Still needs Approval; eligible units also need acknowledgment |
| Ineligible completion, including no-write | Complete without a Base or substitute receipt |

Direct agreement is **Directly InSync**, not an approved drift resolution.
A drifted unit is **Converged** only after its required effects and, when eligible,
its independent acknowledgment transaction commit. Completion occurs at the
unit's earliest ordered position, before its enclosing target post-hook.
Independently approved Additional Source Changes and hook success do not gate
that unit's convergence.

An acknowledgment describes the policy-authorized live fact, not unconditional
raw-byte equality:

- **Use repository**: the successfully published repository-derived outcome.
- **Use live**: the frozen live state used by Capture.
- **Merge**: the frozen merged live outcome.
- **Editor**: the rematerialized policy-derived outcome.
- **No-write**: the frozen approved policy-authorized outcome.

Preview, reused Verification Records, excluded or unselected units, and units
removed by Guards never acknowledge. Failed materialization, pending or failed
required effects, and failed acknowledgment preserve an eligible unit's prior
authoritative Base. Failed acknowledgment leaves it not Converged; earlier
committed unit acknowledgments remain durable.

### Policy maintenance

After static configuration successfully resolves a selected unit as ineligible,
real Push or Sync deletes its old Base **before Guards and review**, without
waiting for drift, Approval, effects, or convergence. Later interactive exclusion
does not undo or prevent that maintenance. Pull, preview, read-only inspection,
incomplete resolution, and unrelated partial selection do not perform it.
Returning a deleted identity to an eligible policy requires fresh establishment.

Storage security, transactions, and inspection locking are documented in
[Sync Base storage](sync-base-storage.md).
