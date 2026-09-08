# Evaluate operation guards during planning

Dotman evaluates `guard_push` and `guard_pull` as non-interactive eligibility rules while building an operation plan, after static ownership resolution and before host-state projection. Exit `0` admits the scope, exit `100` omits its work, and any other exit aborts planning; guards are not rerun during execution. Repo, package, and target guards gate their declared scopes, while directory path-rule guards run once per active rule and overlapping guards compose. This keeps selection and review faithful to executable work without making ownership depend on volatile guard state; state changes after planning remain execution failures.

Repo, package, target, and directory path-rule guards now follow this planning contract.

During Sync, both directional families advance in repository → package → target
order. Each exit-100 outcome removes only that family's capability; configured
Sync Policy and Base eligibility remain unchanged. One-sided file work without
a surviving route remains a non-approvable diagnostic. Independently retained
ancestor noop hooks survive lower-scope exclusions. Probe activity is evaluated
once after Guards and never creates a file Observation or Proposal.
