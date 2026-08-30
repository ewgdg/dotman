# Dotman Domain

Dotman manages selected package content between canonical repositories and live filesystem locations.

## Language

**Sync Unit**:
The smallest file payload that can be independently included in or omitted from a sync operation. A file target is one sync unit; each child file of a directory target is its own sync unit.
_Avoid_: Package, directory target

**Verification Record**:
A local observation that a target or Sync Unit satisfied one operation's planning contract at a successful verification boundary. It may be reused while the relevant state and inputs remain unchanged.
_Avoid_: Last sync timestamp, sync timestamp

**Signature-Verifiable Sync Unit**:
A sync unit whose relevant repo and live state is completely represented by deterministic filesystem signatures and effective configuration. It does not require a Freshness Window.
_Avoid_: Simple target, raw target

**Path Rule**:
A named policy for matching child Sync Units of a directory target. Matching rules compose by priority.
_Avoid_: Path-rule index, ordered rule

**Sync Policy**:
The effective rule that constrains automatic state flow for a Sync Unit to repository-to-live, live-to-repository, both directions, or repository-directed live deletion. It does not prohibit deliberate repository changes made through the Proposal Editor. Package, target, and Path Rule configuration establish the policy; directional Guards may only narrow it.
_Avoid_: Ignore, Guard, file ownership

**Freshness Window**:
The bounded period during which verification of a target with opaque dependencies may be reused. Once it expires, the target requires full verification.
_Avoid_: Cooldown, probe duration

**Planning Strategy**:
The policy that determines whether sync planning fully verifies selected work or may reuse valid Verification Records. The supported strategies are full and fast.
_Avoid_: Mode, execution mode

**Input Fingerprint**:
The identity of the effective Dotman-controlled inputs that determine how one operation interprets a target or Sync Unit. Opaque external dependencies are excluded and bounded by a Freshness Window.
_Avoid_: Configuration fingerprint, derivation hash

**Guard**:
A directional eligibility rule evaluated before planning work for its repository, package, target, or Path Rule scope. During Sync, `guard_push` and `guard_pull` narrow repository-to-live and live-to-repository capabilities respectively without widening the configured Sync Policy.
_Avoid_: Pre-hook, execution safety check, Sync Policy

**Effective Work**:
Sync actions or noop-eligible pre/post hooks still belonging to a scope after earlier eligibility decisions and exclusions.
_Avoid_: Diff, guard execution

**Potential Work**:
Statically selected operation-eligible targets, probes, or noop-eligible hooks that may produce effective work after planning.
_Avoid_: Planned action, confirmed diff

**Probe Work**:
An active probe target presented by Sync as selectable auxiliary work that may activate applicable hooks. It has no repository/live payload, Resolution Intent, Proposal, Sync Base acknowledgment, or Converged result.
_Avoid_: Sync Unit, Proposal

**Structured Transform**:
Format-aware partitioning and recomposition of document content.
_Avoid_: Render, capture, repository transform

**Transform Framework**:
Shared contract that coordinates structured transforms independently of any particular file format or repository policy.
_Avoid_: Structured transform, format transformer, repository transform

**Format Transformer**:
Reusable structured-file transformation behavior for one data format, such as JSON, TOML, plist, or XML.
_Avoid_: Transform framework, repository transform

**Repository Transform**:
Repository-owned transformation policy or behavior tied to one package, application, or repository convention.
_Avoid_: Format transformer

**Rewrite**:
Order-preserving substitution of textual content without interpreting document structure.
_Avoid_: Text transform, structured transform

**Home Path Rewrite**:
Reversible rewriting between the active home directory's absolute path and `~` when either appears as a complete path fragment in text.
_Avoid_: Home normalization, home path transform

**Render**:
Forward projection from repository representation toward live representation.
_Avoid_: Push, transform

**Sync Base**:
A committed repository representation Dotman established through successful Sync convergence, Push, or directly verified no-change Pull as known shared ancestry between one Sync Unit's repository and live histories. It is the base for later three-way Reconciliation and may be older than the working-tree representation Push materialized.
_Avoid_: Current Git HEAD, last deployed file, snapshot

**Observation**:
The frozen classification of a selected, policy-eligible Sync Unit as Directly InSync, Drifted, or Observation Failed from its policy-defined repository and live comparison states. Typed missing endpoints are evidence within the classification, not separate workflow states.
_Avoid_: Proposal, Reconciliation

**Directly InSync**:
An Observation result for a Sync Unit whose policy-defined repository and live comparison states agree before Dotman materializes a drift resolution. A drifted unit with a no-write resolution is not Directly InSync.
_Avoid_: Noop, unchanged

**Converged**:
A completion result for a drifted Sync Unit whose own final-preview Primary and live effects succeeded and whose Sync Base acknowledgment persisted. Independently executed Additional Source Changes do not gate this result.
_Avoid_: Applied, Directly InSync

**Capture**:
Reverse projection of live file state into repository representation before Reconciliation.
_Avoid_: Pull, transform

**Pull View**:
A side-specific projection configured under `compare` and used to compare repository and live representations during Pull or live-to-repository-capable Sync observation and review.
_Avoid_: Write preview, Pull Candidate

**Reconciliation**:
Resolution of current repository state and a Capture result by replacement or, using a Sync Base, three-way merge. It produces a Proposal or an explicit conflict.
_Avoid_: Capture, raw overwrite

**Primary Source**:
The exclusive repository source whose representation one Sync Unit reconciles. A repository path may be the Primary Source of at most one Pull Sync Unit.
_Avoid_: Additional Source

**Additional Source**:
A declared or dependency-discovered repository component staged with a Proposal because its Primary Source depends on it. It may be shared by multiple Sync Units but cannot also be a Primary Source.
_Avoid_: Primary Source, extra file

**Source Change**:
A session-local staged mutation of one normalized repository-relative source path that exists only while staged state differs from its frozen preimage. It may be shared by referencing Editors; an Additional Source Change has one canonical Command Deck row and is written at most once by Apply. Approval behavior belongs to the active command workflow rather than Source Change identity.
_Avoid_: Proposal, Sync Unit, editable copy

**Resolution Intent**:
The session-local choice of `Use repository`, `Use live`, or `Merge` for one drifted Sync Unit before Dotman materializes a Proposal. Changing it discards the prior Proposal for rematerialization but preserves Approval; returning to an earlier intent does not revive its prior frozen outcome.
_Avoid_: Proposal, applied resolution

**Proposal**:
The session-local candidate for resolving one drifted Sync Unit. Pull materializes its fixed live-to-repository outcome directly. Sync begins with a Resolution Intent and lazily freezes the repository outcome, policy-derived live outcome, and planned effects. A Proposal's change and effect sets may be empty.
_Avoid_: Pull View, Source Change

**Approval**:
The Command Deck state that authorizes a Proposal or Additional Source Change for execution. For Sync, Proposal Approval is standing authorization to execute the current successfully materialized unit outcome, including its exclusive Primary Source Change, policy-derived live effects, and Sync Base acknowledgment. Additional Source Change Approval independently authorizes its repository write, even when no approved Proposal references it. Its execution result never gates a Proposal's Converged result or Sync Base acknowledgment, even when that Proposal was materialized using the candidate bytes. An unapproved Additional Source Change remains staged for review, but its candidate bytes are excluded from referencing Proposal inputs; approved referencing Proposals rematerialize immediately from the frozen repository preimage, while unapproved ones discard stale previews and rematerialize lazily. Successful rematerialization preserves Proposal Approval; failure clears only the affected Proposal Approval. Approval is independent of Proposal generation: changing Resolution Intent or editing rematerializes effects without changing the existing state; an explicit toggle or materialization failure changes it. Approval for an Additional Source path survives while no Source Change exists and returns if the row reappears. Batch actions establish every row's final Approval state before Proposals materialize against that state. Sync is opt-in, including for one-sided and no-write Proposals; Pull is opt-out.
_Avoid_: Inspection status, execution result

**Command Deck**:
An operation's persistent workset view. It combines row-level actions, selection, and focused review while preserving the user's place in the workset. Pull and Sync may use different row states and actions; the name does not imply Proposals or Approval.
_Avoid_: Standalone selection menu, Proposal list

**Proposal Review**:
The focused full-screen view for one Proposal that shows its Pull Views, Sync Base provenance, Capture result, reconciliation evidence, Primary Source Change approval, and referenced Additional Source Changes without mutating repository sources.
_Avoid_: Proposal window, Apply screen

**Source Change Review**:
The focused full-screen view for one Additional Source Change that shows its frozen-preimage diff, referencing Proposals, and approval action.
_Avoid_: Proposal Review, repository editor

**Proposal Editor**:
A configured or default action that edits the current Proposal through its transactional repository sources without directly mutating the repository working tree. If materialization produced no Proposal, it starts from the current repository sources as `Use repository` would; saving produces an `Edited` Proposal.
_Avoid_: Outcome handler, repository editor

**Apply**:
Mutation of the repository working tree from independently approved Source Changes. Each file is replaced atomically, but a multi-file Apply may partially complete.
_Avoid_: Commit, Capture

**Command Runtime**:
The internal typed boundary that launches commands and owns environment construction, pipe or terminal I/O, elevation, streaming, and interruption normalization. Callers retain the meaning of command exit statuses.
_Avoid_: Hook runner, subprocess helper
