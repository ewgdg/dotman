# Sync lifecycle

A file target is one **Sync Unit**. Each regular-file child of a directory target
is an independent Sync Unit; a directory root has no aggregate Base.

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
