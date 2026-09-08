# Push-only file convergence

## Goal and intention
Deliver issue #63: inspect, opt in, preview, and converge push-only file targets through the public one-shot SyncSession and an initial Command Deck. Build on #62 frozen Observation without implementing the remaining #56 roadmap.

## Scope and constraints
- Only configured push-only file proposals and Use repository.
- Immutable materialized repository/live outcomes and exact Publication Effects; no execution-time projection or re-observation.
- Initial Approval is false; inclusion is independent. Directly InSync is distinct from approved drifted no-write convergence.
- Preview performs no mutation, hooks, snapshots, Base acknowledgment, or cleanup.
- Real sessions preserve operation-lock lifetime; push-only convergence creates no Base.
- No Editor, Additional Sources, directory census, alternate intents, or eligible drift convergence.

## Work plan
1. Read #63, applicable #56 contracts, CONTEXT and ADRs; inspect #62 foundation.
2. Write core and CLI behavioral tests before implementation.
3. Add materialization, Approval/review/preview and frozen publication using existing mechanics where applicable.
4. Add CLI adapter and persistent initial Command Deck using public session commands only.
5. Update affected CLI/lifecycle/architecture documentation and validate targeted plus suitable broader tests.

## Validation
Core tests cover opt-in Approval, immutable exact outcomes, preview isolation, real publication/no Base, no-write classification, typed rejection/failure and lock lifetime. CLI tests cover unattended explicit approval, dry-run JSON and interactive Deck behavior. Run affected engine and CLI suites before completion.

## Progress
- Read issue #63 (no comments), parent #56 applicable contracts, CONTEXT and ADRs 0001–0003.
- Core implementation and engine tests delegated to a bounded implementator; CLI, documentation and plan remain with the coordinator.
- Resumed unfinished work: existing CLI imports depended on an absent core API.
  Partitioned completion into core/session tests and CLI/Deck tests, with lifecycle,
  CLI and architecture documentation maintained separately.
- Checked the parent contract: unattended authorization is explicit; JSON and a
  missing terminal never imply consent. Structured output must omit content bytes.
- Resumed original core, CLI and read-only review agents after verifying their
  outstanding assignments and restored message delivery. Restored documentation
  draft; final claims remain gated on integration validation.

## Decisions
Keep push/pull orchestration unchanged; add a dedicated Sync CLI adapter rather than extending their plan workflow. Use existing prompt-toolkit dependency for the initial persistent Deck.

## Outcomes
Push-only convergence and the initial Command Deck are complete. Final follow-up
fixes reject unsupported unattended worksets before publication and preserve exact
canonical execution scope identities. Independent review reproduced both original
failures against the fixes and confirmed resolution with no remaining findings.
Final full-suite validation: **1384 passed in 12.83s**; `git diff --check` is clean.

## Implementation checkpoints
- Commits `1a5c463`, `b1949e6`, `361eaed` implement frozen publication and core
  session commands; `523ca96` implements CLI/Deck. Reported targeted validation:
  72 engine tests and 70 CLI tests pass.
- First review fixes preserve materialization diagnostics on unapproval and
  interruption outcomes. Enclosing post-hook failures preserve converged units.
- Final review fixes in `e9dd722` and `6422537` validate every endpoint before
  snapshot capture (including later selected FIFOs), catch InterruptedError before
  OSError, and expose immutable semantic results instead of execution plans.
  CLI adaptation is committed in `a192336`. Targeted validation: 75 engine tests
  and 70 CLI tests pass.
- Existing Push/Pull flags remain unchanged; Sync alone uses explicit
  `--unattended`. Remaining #56 capabilities stay outside this slice.
- Follow-up commits `55b67e9` and `c2e7675` add canonical semantic scope identity
  and preflight rejection of unsupported unattended drift. Regression tests cover
  both/pull-only mixed worksets in real and preview modes and failed instance-target
  hooks with preserved unit convergence. Independent review ran 47 relevant tests
  and separate mixed-policy probes; both findings are verified resolved.
