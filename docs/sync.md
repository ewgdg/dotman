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

## Resolution Intents and the Command Deck

Drifted push-only files offer **Use repository**; pull-only files offer only
**Use live**. Both-policy files offer **Use repository**, **Use live**, and
**Merge** when a usable Sync Base exists. Their default is Merge with a usable
Base; otherwise Use live is an explicit fallback, with the Base reason shown in
the focused detail, review, and command output. Proposal Approval starts off;
opening a review does not approve it. Review or Approval materializes the
Proposal from frozen Observation, retaining the repository representation,
policy-derived live outcome, exclusive Primary Source Change, and exact
Publication Effects. Execution consumes that outcome rather than rendering or
observing again. Inclusion is separate from Approval.

The persistent Textual Command Deck shows drift and diagnostics, not directly
agreeing units. Its aligned table uses canonical target identities and an
**Approval** column. Unsupported resolution capability is labeled separately
from Observation and Proposal failures; focused diagnostic details explain the
current row. Directory scopes are outside the file session's current capability.
Focused review shows
the frozen repository/live Pull Views separately from repository-effect and
publication previews. A no-write Proposal still shows the observed drift even
when Capture returns the unchanged repository representation. Returning preserves
the workset.
Press **R** or click a Resolution cell to open the focused row's policy-allowed
choices. Use arrows and Enter, or click a choice; Escape dismisses the menu.
Changing Resolution Intent preserves Approval, discards the prior Proposal, and
rematerializes approved work. **T** explicitly retries failed materialization.
Review exposes the frozen Capture result and Reconciliation evidence separately
from both repository and live effect previews.
Confirmation authorizes the selected, already-materialized outcomes.

For pull-only files, review or Approval lazily Captures frozen live evidence into
the repository outcome. Capture does not read live again. Review shows the
Primary Source Change (write, deletion, or none) authorized by Proposal Approval;
live remains unchanged. Confirmation counts repository changes separately from
live effects. JSON reports `primary_source_change` metadata separately from
Publication `effects`, without exposing payload bytes.

Real execution applies approved repository outcomes through pull hooks before
publishing approved live outcomes through push hooks. Pull-only work creates no
live snapshot and cannot mutate live. A pull-only unit becomes **Converged** only
after its required repository effect and Base acknowledgment succeed. Even a
drifted Capture result needing no repository write requires Approval and Base
acknowledgment; it is not **Directly InSync**. A drifted push-only unit
becomes **Converged** when its required effects succeed, without creating a Base. An approved drifted Proposal with no writes still needs
this completion boundary; it is not **Directly InSync**. Failed publication
does not claim convergence or roll back earlier successful units.
No-write completion runs no hooks and creates no snapshot. An enclosing
post-hook failure fails the operation without undoing a unit's convergence.

### Both-policy reconciliation

Merge lazily reconciles the usable Base payload, frozen repository representation,
and frozen Capture result. Equal sides agree; when one side still equals the Base,
the other side wins. Otherwise present file contents use Git's three-way
`merge-file` through Command Runtime. Conflicts and provider failures remain
distinct typed, blocked, retryable diagnostics. They clear the affected Approval,
not its Resolution Intent; Dotman never selects another intent or opens an Editor
automatically. Successful Capture and Render results are reused for unchanged
frozen inputs.

Use repository never Captures. Use live and Merge derive live publication by
Rendering their repository outcome under policy, so Use live can require live
writes or mode changes as well as a Primary Source Change. Both stages consume
only approved frozen effects. An eligible unit acknowledges after its own last
required effect, before later units or enclosing post-hooks; failure preserves
the prior Base and does not undo earlier committed acknowledgments.

## Session lifetime and current engine boundary

File-target sessions support frozen Observation, push-only, pull-only and both-policy
Proposals, Approval, review, preview, Repository Apply and Live Publication.
Directory children, auxiliary work and Proposal editing are not yet supported.
Unapproved or excluded healthy work remains untouched. Abort does not undo Base
maintenance already committed while opening.
Deliberately unapproved supported drift remains `pending` without failing the
operation. Included unsupported drift makes the result `incomplete`; diagnostics
and execution failures remain failures. Unattended Sync rejects a blocked or
unsupported workset before mutation.

Live publication requiring prompt-mode symlink replacement remains a typed
blocker; Approval does not implicitly authorize replacing it. Pull-only work
does not replace live symlinks.

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
Comparison-owned Capture views may run during Observation. Pull-only Proposal
materialization reuses that frozen Capture result when the configured live
comparison already required it, rather than running Capture again. Otherwise,
Capture is delayed until review or Approval. Push-only Proposal materialization
needs no Capture or three-way reconciliation.

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
