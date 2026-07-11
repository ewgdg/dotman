# Dotman Domain

Dotman manages selected package content between canonical repositories and live filesystem locations.

## Language

**Sync Unit**:
The smallest file payload that can be independently included in or omitted from a sync operation. A file target is one sync unit; each child file of a directory target is its own sync unit.
_Avoid_: Package, directory target

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

**Render**:
Forward projection from repository representation toward live representation.
_Avoid_: Push, transform

**Capture**:
Reverse projection from live representation toward repository representation.
_Avoid_: Pull, transform
