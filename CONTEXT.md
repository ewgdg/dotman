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
An eligibility rule evaluated before planning work for its repo, package, target, or path-rule scope. An ineligible scope contributes no sync work.
_Avoid_: Pre-hook, execution safety check

**Effective Work**:
Sync actions or noop-eligible pre/post hooks still belonging to a scope after earlier eligibility decisions and exclusions.
_Avoid_: Diff, guard execution

**Potential Work**:
Statically selected operation-eligible targets, probes, or noop-eligible hooks that may produce effective work after planning.
_Avoid_: Planned action, confirmed diff

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
The repository representation Dotman has most recently acknowledged as shared ancestry between a Sync Unit's repository and live histories. It is the common ancestor for later three-way Reconciliation.
_Avoid_: Git HEAD, last deployed file, snapshot

**Capture**:
Reverse projection of live file state into repository representation before Reconciliation.
_Avoid_: Pull, transform

**Pull View**:
A side-specific projection used to compare repository and live representations during Pull planning and review.
_Avoid_: Write preview, Pull Candidate

**Reconciliation**:
Resolution of current repository state and a Capture result by replacement or, using a Sync Base, three-way merge. It produces a Proposal or an explicit conflict.
_Avoid_: Capture, raw overwrite

**Proposal**:
A session-local transactional repository write set produced by Pull Reconciliation, then reviewed, optionally edited, and approved before Apply.
_Avoid_: Pull View, repository working tree

**Proposal Editor**:
A configured or built-in action that edits a Proposal's transactional repository sources without directly mutating the repository working tree.
_Avoid_: Outcome handler, repository editor

**Apply**:
Atomic mutation of the repository working tree from an approved Proposal.
_Avoid_: Commit, Capture

**Command Runtime**:
The internal typed boundary that launches commands and owns environment construction, pipe or terminal I/O, elevation, streaming, and interruption normalization. Callers retain the meaning of command exit statuses.
_Avoid_: Hook runner, subprocess helper
