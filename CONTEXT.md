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
