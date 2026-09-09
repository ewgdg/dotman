# Sync lifecycle

A file target is one **Sync Unit**. Each regular-file child of a directory target
is an independent Sync Unit; a directory root has no aggregate Base.

## Frozen Observation

A SyncSession observes its resolved file targets, directory children and auxiliary
work once. Guards run in repository → package → target order across both
directional families before endpoint reads and comparison. After directory
census and child policy resolution, active named Path Rule Guards run by priority
then name, once per rule and directional family.
Exit 0 retains capability, 100 removes that direction within the Guard's scope,
and other non-zero exits abort planning. Review and execution never rerun Guards.
Configured Sync Policy remains the upper bound; narrowing never changes Base
eligibility (configured `pull-only` or `both`). It retains the resolved scope order, effective projections, typed
endpoint bytes and live mode/link evidence. File targets also retain Git facts
and applicable Base evidence.

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

## Directory census and child capability

A directory target is a discovery scope, never an aggregate Sync Unit. One
census inspects both trees, combines skip-marker subtrees found on either side,
and applies repository-source Git ignore controls and repository/package/target
exclusions symmetrically. Live `.gitignore` content does not define policy.
Control files, directory nodes and empty directories are not payloads. Ignored
paths receive no Observation or Base maintenance. Git ignore matching preserves
[excluded-parent semantics](https://git-scm.com/docs/gitignore): a nested negation
cannot re-include content beneath an excluded directory.

The surviving regular-file union has canonical identities
`repo:package.target/<relative/child>` (including package-instance forms).
Target and child scopes form a de-duplicated union; exact child selection does
not observe unrelated siblings. The census remains control-aware for partial
selection, and an ancestor discovery failure remains visible for affected
selected children rather than implying absence.

Each child resolves its own named Path Rules, configured policy, Render, Capture,
comparison, Editor and exact mode policy, even with a Missing endpoint. Exact
child scopes activate only their resolved configured directions at ancestor and
target Guards; unrelated child rules cannot add a direction. Full-target discovery
retains all potentially configured child directions. Path Rule Guards only narrow
the child's configured capabilities. Executable state matters in child comparison;
other repository permission bits do not. Exact child chmod also participates when
push capability survives (`push-only` or `both`), but is inactive under `pull-only`.
Under `both`, exact chmod does not replace executable-state comparison. Target
chmod belongs to the root.

Repository symlinks and unsupported nodes yield typed path-specific Observation
failures unless excluded. Repository directory links are never descended or used
as ancestors for child endpoint reads. Live directory links fail locally in
`fail` mode; `follow` mode retains lexical child identities and detects loops.
Rejected traversal or unknown controls/discovery at an ancestor block every
affected descendant before either payload endpoint is read, including children
already found on the opposite side and exact child selections. Failures do not
discard unrelated child evidence.

Each child supports the same policy-constrained Resolution Intents, lazy Proposal,
standing opt-in Approval, transactional Editor and independent completion as a
file target. Its repository representation is `Missing` or
`Present(bytes, executable)`. Merge reconciles bytes and executable state
independently: agreement wins; otherwise the side changed from the Base wins.
A conflict remains blocked without changing intent.

Exact Path Rule chmod derives only the approved live outcome with surviving push
capability. Equal bytes and `Use live` can therefore produce chmod-only
Publication Effects. It never changes the repository outcome or the committed
executable state stored as ancestry. Other permission bits remain outside Merge.

Each child path is its exclusive Primary Source. Transform and Editor dependencies
use canonical independently approved Additional Sources, including shared
dependencies across children. There is no directory source bundle. A rename is a
deletion and an addition with separate Approval and Base identities.

Child Git facts are batched with file targets. Direct eligible agreement can
acknowledge immediately in a real session; drifted eligible children acknowledge
independently after their own required effects. Ineligible children complete
without a Base. Both stages keep one enclosing target hook scope and report each
child's exact identity. Structural parent directories are created only for selected child writes and
pruned after child deletion, stopping at the target root. Empty targets are never
created merely because they are selected. Existing root mode drift is separate,
selectable **Directory Root Work**: it runs within push hooks before every child
Publication Effect and never produces a Proposal, Converged result, or Base.
A child creating a missing live root applies the configured target mode as its
own prerequisite.

A file/directory transition requires explicit Approval of every managed ancestor
or descendant deletion that blocks a writer, including siblings omitted by an
exact-child selector. Dotman never auto-selects blockers or clears the writer.
Confirmation is unavailable until this structural closure holds; unattended
execution rejects it before mutation. An ignored, control, or otherwise unmanaged
blocker produces a typed structural conflict instead. Ready child work uses
normalized relative-path ordering, with prerequisite deletions before dependent
writers. A completed deletion remains Converged, with its Base committed, if a
later writer fails.

Obsolete child Bases are considered only from a later complete unrestricted
target census. Reclamation occurs after successful real execution, not while
previewing or aborting review, and does not reclaim newly acknowledged deletions
in that session. Partial selection, ignores, marker restrictions, Guard narrowing,
Path Rule Guards, interactive exclusions, or failed discovery retain records.
A restricted census is deliberately not used as absence proof.

## Auxiliary work

An active Probe becomes **Probe Work** only when at least one configured
capability survives Guards. Its command runs once: exit 0 keeps the row, 100
omits inactive work, and any other non-zero exit aborts planning. A Probe cannot
use `push-only-delete`; it has no live endpoint to delete.

Probe Work starts unselected and has no file Observation, payload, comparison,
Resolution Intent, Editor, Proposal, Approval, Base or Converged result. Selection
activates normal hooks only in its surviving families: pull hooks during
Repository Apply, then push hooks during Live Publication.

Independently noop-eligible hooks remain selectable **Hook Work**, identified
only by canonical repository, package-instance or target scope plus
`(pull-hooks)` or `(push-hooks)`. Manifest command/hook `run_noop` is retained;
`sync --run-noop` makes both surviving families noop-eligible. A child Guard does
not remove an ancestor's independently retained noop hooks. Selection coalesces
nested hooks with Probe and file work so each hook runs once. Auxiliary-only
execution writes no payload and creates no snapshot or Base acknowledgment.
Preview freezes Guard and Probe results but executes no hooks.

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

The persistent Textual Command Deck shows drift, auxiliary work and diagnostics,
not directly agreeing units. Its aligned table uses canonical target identities
and a **Selection** column. Selection authorizes Proposals and canonical Additional Source Changes through
independent Approval and directly includes auxiliary work. Unsupported resolution capability is labeled separately
from Observation and Proposal failures; focused diagnostic details explain the
current row. Directory child rows use the same opt-in Approval as file targets. The deck and
output adapter also recognize semantic **Directory Root Work** as direct Auxiliary
inclusion, without Proposal or Approval. Root work uses the same selection styling
and canonical target identity as other auxiliary work.
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
Execution confirmation is available only when every approved participating Proposal
has a valid materialization. It summarizes approved units, independently approved
Additional writes, auxiliary inclusion and live effects. Cancelling confirmation
preserves Approval, inclusion, focus and review state. Confirming closes the deck
and executes exactly those already-materialized outcomes; it does not trigger
Capture, Render, re-observation or another planning pass.

During Capture, Merge, or Render, the deck remains visible with an animated
**Materializing Proposal** indicator. Review, Approval, batch actions, Resolution
changes, and retries share one serialized execution lane. While it is busy,
competing keyboard and mouse actions are ignored rather than queued. **Ctrl-C**
(or OS SIGINT) cancels the operation, including the remainder of a batch, and exits
with status 130. Repeated Ctrl-C is safe. Dotman waits for materialization and
owned subprocess cleanup before discarding temporary files or releasing its lock.
The Command Runtime interrupts an owned process group and escalates to termination
if it does not stop promptly. Cancellation cannot undo completed provider effects
or control deliberately detached descendants. Apply and Publication still consume
frozen approved outcomes; this does not make those stages background work.

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

### Transactional Proposal Editor

Press **E** from the workset or Proposal Review to deliberately edit a drifted
file unit with a surviving route. One-sided policies permit this explicit
repository edit, including deletion-only units; they still prohibit automatic
flow in the forbidden direction. Observation failures and Guard-blocked units
have no Editor. Dotman never launches an Editor automatically or in unattended
Sync.

The Editor starts from the current Proposal's repository outcome. If no Proposal
exists, including after a Capture failure or conflict, it starts from frozen
repository sources without requiring Capture. The configured/default Editor
receives isolated Primary and permitted Additional Source copies and read-only
review evidence, not writable tracked paths. Cancelling discards the attempted
transaction and preserves the previous Proposal and Approval. Saving produces
an **Edited** generation and derives policy-appropriate effects; tracked sources
and live endpoints remain unchanged until execution. Deliberate Primary writes
activate Repository Apply pull hooks even when automatic live-to-repository flow
is forbidden by policy or Guards. Guards are not rerun; no-write editing does not
activate these hooks. Byte-identical saves retain
valid projection results rather than repeating provider work.

### Additional Source Approval

Each changed Additional Source has one canonical repository-relative row, shared
by every referencing Proposal. Source Change Review shows the frozen-preimage
diff and reverse references. Proposal Review lists those references but does not
duplicate their Approval control. Primary Sources remain exclusive to their
own Sync Units and cannot also be Additional Sources.

Additional Approval independently authorizes the repository write, even with no
approved referencing Proposal. New paths start unapproved. Unapproved candidate
bytes remain available to review and later Editor attempts, but dependent
Proposals use the frozen repository preimage instead. Approval and unapproval
rematerialize approved references immediately; unapproved references discard
stale previews and rematerialize only on demand. Editing an unapproved candidate
alone does not invalidate unchanged authorized inputs or repeat their providers.
Batch unselection retains unresolved materialization diagnostics; only successful
rematerialization clears them. It does not turn failed work into healthy work.
Successful rematerialization
preserves Proposal Approval; failure clears only the affected Proposal.

Returning a path to its frozen preimage removes its changed row, not its
session-local Approval. Recreating the change restores that Approval. Batch
selection establishes all final Proposal and Additional states before any
dependent materialization; batch unselection discards invalid previews without
eagerly regenerating them.

Repository Apply writes approved Additional changes once, in stable normalized
path order before Primary changes. Additional-only work activates no directional
hooks. Additional results are reported independently and are not requirements
of a referencing Proposal's Converged result or Base acknowledgment.

Successful rematerialization preserves standing Approval. Failed materialization
clears only the affected Approval and leaves a typed diagnostic with local retry.
Conflict, Capture failure, command failure and Editor cancellation remain
distinct; none silently chooses another Resolution Intent. **T** retries failed
materialization; **E** can retry an Editor attempt. During an Editor attempt, **Ctrl-C** cancels that attempt rather than aborting
the session. Terminal Editors temporarily own the terminal and return to the same focused workset or review when finished.

### Frozen execution and partial failure

Execution traverses repositories in configured order, dependency-first package
plans, then stable target-plan order. The complete operation runs Repository
Apply before any Live Publication. Approved Additional Source Changes are written
once in repository order and normalized-path order, before Primary Source work;
Additional-only work owns no package/target hook scope.

Primary changes use nested pull hooks, including deliberate Editor changes under
one-sided policy. Publication uses nested push hooks and consumes the exact
approved effects without reading sources again, Capture, Render, Observation or
replanning. Selected Probe and retained noop-hook work activate only their
surviving directional families.

Every approved no-write Proposal has its normal Repository Apply completion
position, even when Base-ineligible. Repository-only units complete there;
units requiring live effects wait for their own publication. Eligible completion
commits the unit's Base before target post-hooks. Post-hook success is not a
condition of convergence.

The first failed write, safety check, hook, acknowledgment or interruption stops
later work. Cancellation is checked at each effect, hook and completion boundary.
Atomic replacement protects each file, not the whole operation: earlier writes,
Converged units and committed Bases remain in place. A write followed by failed
chmod retains the bytes but does not acknowledge or converge that unit.

Results retain the exact failed step and ordered successful and unattempted
steps, including required completion boundaries that were never reached. A unit
with no attempted work is `skipped`; one whose repository work succeeded but
whose live work was not attempted is `not-converged`. A failed unit is
`execution-failed` (or `interrupted`), while earlier completed units remain
`converged`. Additional Source results remain independent.

One normal live-only snapshot is created after successful Repository Apply,
after push pre-hooks and safety checks, immediately before the first live
mutation. It covers only the frozen publication set and survives partial live
failure. Preview, repository-only, no-write and hook-only operations create no
snapshot. Restore never restores repository sources.

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

File-target and directory-child sessions support frozen Observation, push-only, pull-only and both-policy
Proposals, Approval, review, preview, Repository Apply and Live Publication.
Sessions also support auxiliary work and explicit Proposal editing.
Unapproved or excluded healthy work remains untouched. Abort does not undo Base
maintenance already committed while opening.
Deliberately unapproved supported drift remains `pending` without failing the
operation. Included unsupported drift makes the result `incomplete`; diagnostics
and execution failures remain failures. Unattended Sync rejects a blocked or
unsupported workset before mutation.

### Symlink interpretation

Prompt file-symlink mode observes a regular referent. A write or mode-only
publication replaces the declared leaf link with the frozen regular-file
outcome, but requires the explicit `AuthorizeSymlinkReplacement` semantic
command first (**L** in the deck). Authorization and Proposal Approval are
separate; unattended execution rejects required authorization. Deletion unlinks
the declared link without replacing or deleting its referent. Pull-only work
never replaces a live link.

Follow file-symlink mode preserves the leaf link. A missing referent is typed
`Missing`; creation writes its then-current referent, and deletion removes
that referent while retaining the link. External referent parents are not pruned.

Directory-symlink fail mode rejects unexcluded links at or below the declared
directory root, including links introduced after Observation. Follow mode
traverses them under lexical child identities. Repository links are unsupported
and never payload inputs; unsafe shapes yield typed diagnostics.

Execution dereferences the current chain for each effect and checks shape,
declared endpoint confinement and selected policy without re-observing payloads,
rerendering, or comparing content. Retargeting alone does not invalidate frozen
work. Enabled snapshots still read live preimages before mutation. Link chains
and resolved referent paths are not identity or Input Fingerprint inputs;
interpretation modes are.

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

See [endpoint convergence](sync-endpoints.md) for typed Missing, deletion-only,
unsupported endpoint evidence, and approved no-write completion.
