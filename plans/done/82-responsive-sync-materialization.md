# Responsive Sync materialization

## Goal and intention
Keep the persistent Command Deck animated and cancellable during Capture, Merge,
and Render without changing frozen Proposal/Approval or Apply/Publication behavior.

## Scope and constraints
One serialized materialization thread with copied ContextVars. The Command Runtime
owns a cancellation latch and subprocess cleanup. Never cancel a Textual worker and
assume its thread stopped; drain owned work before aborting or releasing the lock.
No terminal suspension, generic jobs framework, engine async rewrite, or #40/#67/#70 expansion.

## Work plan
1. Add red real-PTY and headless regression tests at the CLI/deck seam.
2. Add runtime cancellation and process cleanup tests/implementation (runtime-cancel delegate).
3. Route every materializing deck action through one awaitable lane; gate competing
   input and scope OS SIGINT to the same abort path as Ctrl-C.
4. Preserve typed interruption and stop batches; update styles and user docs.
5. Run focused tests and appropriate final suite; semantic commits and review.

## Validation
Real PTY key Ctrl-C and OS SIGINT during slow Capture, Merge and Render; visible
busy progress, no later work, cleanup, terminal restoration and lock reacquisition.
Headless tests cover busy input gating and ContextVars. Runtime tests cover spawn
races and ignored signals. Existing Sync/UI/unattended tests protect semantics.

## Progress
- Read issue #82 (no comments), domain vocabulary and applicable skills. Existing
  interruption tests cover idle deck/unattended hooks, not slow materialization.
- Delegated non-overlapping runtime/capture and real-PTY test files; deck/session/docs owned here.

## Decisions and discoveries
- Observation stores close before lazy materialization; no SQLite connection moves threads.
- Session dispatch replaces a captured immutable view: abort must wait for dispatch,
  never mutate the session concurrently.
- Hypotheses: synchronous dispatch blocks UI (primary); subprocess signal ownership
  delays cleanup; Capture wrapping interruption turns abort into retryable failure.

## Progress checkpoints
- Red headless test: `uv run pytest tests/cli/test_sync_deck_textual.py -k materialization_keeps -q`
  failed at the gated Capture after two seconds: event loop could not release it.
- Five targeted real-PTY red probes reached slow providers but failed animated
  progress at the five-second readiness deadline (Capture, Merge, Render).
- Runtime cancellation, startup races, typed Capture interruption and shared test
  doubles implemented in 7129176, 7c15f32 and 9bad494; 33 runtime/capture tests pass.
- Deck uses one context-copied executor lane and an awaitable dispatch, with a busy
  gate, animated indicator, scoped SIGINT and drain-before-abort. Focused engine/
  command/Proposal tests: 82 passed. Headless busy/batch-drain tests pass.
- Existing tests that sent confirmation simultaneously with Approval now wait for
  readiness: ignoring competing input while busy is deliberate, not queued replay.

## Outcomes
- Final focused run: 142 passed in 30.46s (deck, command, session, Both convergence,
  runtime and Capture).
- Real PTY interruption file: 20 passed, including four completion-boundary cases
  and Render temporary-resource cleanup.
- Full suite: `uv run pytest -q` — **1553 passed in 73.57s**.
- Implementation complete. Command Deck remains visible and animated, all competing
  inputs are gated, both interruption paths drain before abort, and no Apply or
  Publication semantics were moved into the lane.
- Commits: 7129176, 3dad9c4, 7c15f32, 9bad494, 5ec6b3c, d8cbad7.

## Limits and retrospective
Owned-group cleanup excludes detached descendants;
completed provider effects cannot be rolled back. Cancellation is scoped to an
operation rather than the runtime instance; shared/default runtimes remain reusable.

## Operation cancellation scope correction
- Red regression reproduced cancellation poisoning a later operation on the same
  engine/runtime: the second `true` command raised `InterruptedError`.
- Command Runtime now exposes `CommandOperation` identity and lexical
  `command_operation()` activation. CLI establishes the outer operation; nested
  runtime commands inherit it. Standalone commands establish a one-command scope.
- Sync retains its opening operation and activates it at every dispatch; cancellation
  targets that retained identity even outside active dispatch. Context tokens never
  cross a session lifetime or asyncio-context boundary. No latch is cleared, so an
  old copied context cannot start more work when a later operation begins.
- Runtime/Capture/session/PTY/deck regression run: 128 passed in 55.07s. Additional
  DEFAULT-runtime reuse and late-cancel checks: final runtime/session run **66 passed
  in 5.62s**.
- No deck actions or task/cleanup code changed for this correction.

## Independent handoff validation
- Reviewed the serialized deck lane, busy input gate, interruption forwarding,
  drain-before-abort behavior, and corrected operation-scoped cancellation.
- Final integrated `uv run pytest -q`: **1557 passed in 74.68s** after the
  cancellation-scope correction. `git diff --check` passed.
- Conflicting both-policy capability statements in the CLI reference were corrected.
