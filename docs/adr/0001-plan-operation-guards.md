# Evaluate operation guards during planning

Dotman evaluates `guard_push` and `guard_pull` as non-interactive eligibility rules while building an operation plan, after static ownership resolution and before host-state projection. Exit `0` retains the Guard's directional capability, exit `100` removes that capability within its scope, and any other non-zero exit aborts planning; guards are not rerun during execution. Repo, package, and target guards gate their declared scopes, while directory path-rule guards run once per active rule and overlapping guards compose. This keeps selection and review faithful to executable work without making ownership depend on volatile guard state; state changes after planning remain execution failures.

Repo, package, target, and directory path-rule guards now follow this planning contract.

During Sync, both directional families advance in repository → package → target
order, followed by active named Path Rule Guards over policy-resolved
directory children. A Path Rule Guard runs once per directional family, including
when a matching child has a Missing endpoint. Exact child selection activates
only the configured directions resolved for selected child identities at the
ancestor and target scopes; full-target discovery retains potential child
capabilities from its Path Rules. Each exit-100 outcome removes only that family's capability; configured
Sync Policy and Base eligibility remain unchanged. Configured one-sided Sync file work without
a surviving route remains a non-approvable diagnostic. In the explicit one-sided
Push and Pull commands, denial by the operation's Guard instead omits its scoped
work. Independently retained
ancestor noop hooks survive lower-scope exclusions. Probe activity is evaluated
once after Guards and never creates a file Observation or Proposal.
