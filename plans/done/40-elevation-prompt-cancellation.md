# Validate elevation-prompt cancellation before redesigning the broker

## Goal and intention
Determine whether cancelling a broker-triggered password prompt currently violates Dotman's interruption and terminal-lifecycle contracts. Add end-to-end regression coverage and fix only failures demonstrated by that coverage. Re-scope GitHub issue #40 using the results instead of implementing its old main-thread-serving proposal unconditionally.

The user approved the proposed end-to-end broker-prompt cancellation test and evidence-led follow-up. The agreed test seam is the public CLI through a real PTY, real elevation broker/socket/shim, and production Command Runtime. Replace only the external `sudo` executable with a deterministic, unprivileged test executable; never authenticate against the user's real sudo or mutate user configuration.

## Scope and constraints
- Cover Ctrl-C and OS SIGINT after prompt readiness, process cleanup, exit/result semantics, no later work, and terminal restoration.
- Include the worker-owned Command Deck Editor path if needed to validate the architectural constraint behind re-scoping #40.
- Tests must synchronize on readiness and use bounded deadlines with failure-safe cleanup of every spawned process.
- Keep HOME/XDG state and managed paths isolated in pytest temporary directories.
- No main-thread broker rewrite, generic job abstraction, or unrelated cleanup.
- Preserve operation-scoped cancellation, Command Deck responsiveness, and Editor-attempt cancellation semantics.
- Change user-facing behavior or styling only if a demonstrated failure requires it.

## Current evidence
- The broker still starts a listener thread and per-connection threads with copied ContextVars.
- Runtime waits now poll a shared `CommandOperation` cancellation latch; #82 explicitly excluded #40.
- Command Deck materialization and TTY Editors run on a dedicated worker. Merely polling the broker from their command waits cannot provide main-thread serving, and the runtime signal guard remains necessary independently of the broker.
- Existing tests cover runtime propagation, worker-thread TTY commands, copied-context cancellation, and ordinary Editor cancellation, but not interruption during an active broker password prompt.

## Work plan
1. Add and run one end-to-end prompt-cancellation test slice before changing production code. Record a green baseline or a repeatable failure without guessing the cause.
2. If red, minimize the failure, rank falsifiable hypotheses, and implement the smallest durable correction. Add the worker-owned Editor slice before any fix affecting its cancellation contract.
3. Review coverage and implementation independently; run focused regressions and repeat the new PTY cases to detect timing-sensitive failures.
4. Document verified behavior and remaining limitations; re-scope #40 with durable evidence. Do not close the issue merely because the current workaround passes.
5. Commit task-owned changes at meaningful boundaries, archive this plan, and report hashes and checks.

## Validation
The new CLI/PTY test file plus existing elevation, Command Runtime, unattended elevation, and affected CLI interruption/Editor tests. Use `uv run pytest` with bounded command timeouts. Avoid the full suite for a tests-only or narrowly scoped runtime change unless the actual blast radius warrants it.

The PTY must be the child session's controlling terminal with a foreground process group; `isatty()` or an open slave alone is insufficient to exercise terminal-generated SIGINT. Python's [PTY documentation](https://docs.python.org/3/library/pty.html) distinguishes `fork()`/`spawn()` controlling-terminal setup from `openpty()`. Its [signal documentation](https://docs.python.org/3/library/signal.html#signals-and-threads) confirms that Python handlers and handler installation belong to the main interpreter thread.

## Progress
- Confirmed clean checkout at `4bdd76d` and read the relevant test isolation, terminal interruption, and runtime/broker test conventions.
- Test scope is the accepted end-to-end CLI seam, not assertions about thread identities or private runtime methods.
- Initial prompt-ready CLI PTY tests reproduced cancellation tracebacks twice (`2 failed`, about three seconds per run). One initial assertion conflated a following shell statement with a subsequent Dotman hook; narrowed it to a distinct post-hook marker before treating it as a product failure. Corrected PID evidence to track the actual shim rather than the prompt's parent (Dotman).
- A cancellation-only protocol catch removed the unhandled broker `KeyboardInterrupt` but exposed two additional traceback paths: the shim's interrupted socket read and a broker reply to the disconnected client. Targeted protocol normalization and narrow disconnected-peer handling address those paths.
- With the protocol corrections, both CLI cases and the Editor Ctrl-C case passed; Editor OS SIGINT reproduced a late terminal-restoration race. The deck displays cancellation but receives `x` in canonical mode rather than handling it. The next slice must drain broker-owned prompt cleanup before Textual resumes. No main-thread rewrite is planned.
- Independent ownership review recommended one broker per elevated runtime command. With safety weighted 45%, locality/simplicity 35%, and churn 20%, command scope scored 4.25/5 versus 2.40/5 for retaining the global broker and extending its drain. The global broker also retains the first Editor attempt's cancellation context.
- Implemented command-owned broker scope while preserving the existing elevation `prepare` interface. Removed superseded broad session wrappers, global singleton/depth, and atexit cleanup. Nested `sudo -v` commands do not own or close a broker.
- Broker shutdown stops admission, unblocks accepted socket reads, cancels unfinished authentication, and drains tracked handlers before deleting resources. Runtime teardown defers repeated interrupts. The concrete design challenge was a partial request plus a queued request during authentication; shutdown must unblock both rather than time out and return terminal ownership early.
- Existing elevation/runtime/unattended tests passed on the first lifecycle run. The repeated-Editor extension exposed stale test readiness (matching the first cancellation notice); fixed it by requiring a new alternate-screen entry and cancellation notice after the second prompt, without sleeps.
- Six public CLI/PTY cancellation cases passed, then five consecutive runs passed all 30 cases. Coverage includes terminal Ctrl-C, process SIGINT, prompt-only SIGINT through both protocol clients, and two Editor attempts in one session.
- Independent production review found no must-fix issues. A bounded scratch probe demonstrated that simultaneous active-authentication, queued, and partial-read clients all drain on close; 13 targeted existing tests also passed in review.
- Focused regression run: 141 passed in 28.44 seconds across elevation/runtime, unattended handling, planning Guards, Push, configured Editors, Sync terminal lifecycle, and process interruption. Full suite not run because the focused set covers this change's affected contracts.
- Documented the supported command ownership and prompt cancellation behavior in `docs/code-structure.md` and `docs/cli.md`. No new command or rendering style was introduced: existing interruption and Editor-cancel output is reused.
- Final test refinement consolidated duplicate CLI fixtures and added successful authentication controls for both broker and intercept. Eight final PTY cases passed in three consecutive runs (24 passes). Readiness checks require complete PID markers and fresh terminal-resume output.
- Final integrated focused validation: **149 passed in 39.82 seconds**. `git diff --check` passed.

## Outcomes and retrospective
The work found and fixed real cancellation failures without moving the broker onto the main thread. Interruption previously escaped both the socket-waiting shim and the broker handler; replying to an interrupted client could itself raise `BrokenPipeError`. The broker also outlived an Editor attempt, allowing late terminal restoration and retention of the cancelled attempt's execution context.

The Command Runtime now owns a fresh broker for each broker/intercept command and drains all admitted handler work before returning to its caller. Protocol clients preserve interruption as exit 130. Existing threaded execution, ContextVar propagation, and main-thread signal guards remain intentional. Broad operation-level broker scopes were removed rather than retained as compatibility behavior.

New tests exercise a real controlling PTY and the real CLI/socket/shim/runtime with a fake external sudo executable; no actual password or privileged operation was used. Coverage includes successful authentication, both cancellation signal paths, interrupted replies, absence of subsequent hooks, process termination, terminal restoration, and repeated Editor attempts. These tests do not claim to validate a host's real sudo authentication/lease policy or control arbitrary detached descendants.

The original main-thread proposal in #40 is superseded by the demonstrated command-ownership fix. Re-scope its description to this verified behavior and leave it open for review; this task does not authorize pushing commits or closing the issue.
