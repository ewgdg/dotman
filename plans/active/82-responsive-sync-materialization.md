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
Final combined validation pending. Owned-group cleanup excludes detached descendants;
completed provider effects cannot be rolled back. Runtime cancellation is sticky for
its operation lifetime; embedders must use a fresh runtime after cancellation.
