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

## Decisions
Keep push/pull orchestration unchanged; add a dedicated Sync CLI adapter rather than extending their plan workflow. Use existing prompt-toolkit dependency for the initial persistent Deck.

## Outcomes
Implementation remains unfinished. Agent coordination failed globally with
`invariant_violation: Message has duplicate Deliveries`, preventing clarification,
completion delivery and cancellation. No completion claim or issue closure.

## Resume checkpoint
- Uncommitted core changes add immutable Proposals, cached materialization,
  Approval, Proposal Review and Preview in `sync_session.py`, with frozen paths
  in `sync_observation.py` and tests in `tests/engine/test_sync_convergence.py`.
- Real publication still calls a missing `_publish_effects` integration. Bridge
  frozen session `_inputs` (identity to package input/target metadata) to existing
  execution mechanics without re-projecting. Preserve per-unit completion even
  when an enclosing post-hook fails, and expose operation failure separately.
- Healthy deliberately unapproved supported rows must finish successfully rather
  than making preview/execution incomplete. Unsupported/blocking work still fails.
- CLI uses global `--unattended` for Sync only, with no implicit nonterminal or
  JSON consent. Existing Push/Pull confirmation flags remain outside this slice.
  JSON now omits payload bytes; stage outcomes still need real public result facts.
- Delegate-reported validation: existing session tests and seven nonpublication
  core tests pass; 50 CLI parser/composition/runner tests pass. Deck tests expose
  missing publication and healthy-unselected exit status. Final integration and
  broader validation have not run.
- Durable CLI/lifecycle/architecture docs were restored to the established
  boundary rather than advertise incomplete behavior. Update them after the
  implementation passes. The draft is available in commit `2ac99b8`.
- Restore agent coordination before resuming outstanding delegated work, or use
  a fresh workflow with explicit takeover of this checkpoint. Do not launch
  overlapping writers while the current worker requests remain unresolved.
