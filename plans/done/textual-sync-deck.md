# Textual Sync Command Deck

## Goal and constraints
Replace the misaligned prompt_toolkit Deck with a Textual DataTable and focused scrollable review. Keep ordinary prompts, public SyncSession ownership, opt-in Approval, frozen effects and command-runner lifecycle. No both-policy or directory convergence.

## Work plan
1. Reproduce header/variable identity alignment on the real existing renderer.
2. Replace Deck UI with DataTable and scrollable focused review; distinguish unavailable resolution capability from observation/materialization failures.
3. Exercise rendered layout and keyboard/mouse contracts using Textual Pilot and a real PTY; update docs and commit.

## Validation seams
Task request explicitly selects actual renderer/Pilot interactions and public SyncSession as the test seams. Existing engine and command runner tests protect frozen execution and locks. All fixtures use temporary repositories and state.

## Progress
- Read CONTEXT, guard ADR, diagnosing-bugs and TDD skills, existing Deck/session/adapter.
- Consulted official current Textual DataTable, testing, screens and RichLog docs (https://textual.textualize.io/). Installed Textual 8.2.8 with uv; prompt_toolkit remains for ordinary prompts.

## Decisions and discoveries
- Directory scopes currently fail session opening, before the Deck. Both-policy file drift is visible but has no supported proposal commands. UI capability messages must follow public commands, not invent engine rejection paths.
- Ranked alignment hypotheses: (1) header and rows use independent spacing; (2) ANSI escape width handling; (3) terminal wrapping. Test with color off and short unwrapped rows to isolate (1).

## Completed checkpoints
- RED: actual prompt_toolkit Application renderer with pipe input, color disabled,
  two short unwrapped variable-length identities. Policy starts at columns 19 and
  31 while header starts at 25. This isolates independent row/header spacing,
  not ANSI width or terminal wrapping. Captured failing assertion and screen.
- Replaced the old renderer entirely with Textual DataTable, native horizontal
  and vertical scrolling, RichLog frozen review and explicit confirmation.
  Kept the small Deck controller and public command adapter, not a renderer shim.
- Approval column and semantic styles distinguish Unsupported, Observation
  failed, and Proposal failed. Canonical identity styling remains shared.
  The existing command runner still owns session lifetime, locks and execution.
- Pilot coverage exercises actual rendered cell positions at 100 columns,
  resizing to 42, mouse Approval hit testing, keyboard review and per-target
  scroll retention, batch Approval, immutable confirmation/cancellation, empty
  worksets, 25-row scrolling, unsupported versus failed observation, and frozen
  pull evidence after an external live edit.
- Real 110x24 PTY validation captured aligned colored workset, focused review,
  confirmation, and successful return with only the second fixture row approved.
  Temporary live fixture bytes remained unchanged.
- PTY testing caught a rapid Down+Enter ordering issue: app-priority Enter could
  outrun widget navigation. Navigation now shares the app action queue and
  reads the widget cursor at action boundaries. The same rapid PTY sequence
  passed after correction.
- Updated CLI/domain/code structure docs; ordinary prompt_toolkit prompts remain.

## Validation and evidence
- `uv run pytest -q tests/cli/test_sync_deck_textual.py tests/cli/test_sync_deck_command.py tests/cli/test_sync_pull_ui.py tests/engine/test_sync_session.py tests/engine/test_sync_convergence.py tests/engine/test_sync_pull_convergence.py tests/test_terminal.py`: 114 passed in 8.85s before adding the long-workset regression.
- Final focused suite: **115 passed in 10.56s**; `git diff --check` clean.
- Latest Pilot-only run: 7 passed in 6.09s (each test has a five-second coroutine timeout).
- `uv run --with pyte python /tmp/validate_deck_pty.py`: passed; pyte used only for disposable terminal evidence, not a project dependency.
- Durable evidence: `~/.agents/artifacts/outputs/dotman/2026-09-07/textual-sync-deck/`
  contains RED output, prior rendered screen, PTY ANSI capture, decoded workset,
  review and confirmation screens, passing result, and PTY probe sources.
- No real user config/state writes; fixture repositories and state were under /tmp.
- Independent review ran the full repository suite before the input-ordering
  correction: **1458 passed in 21.60s**. After the correction and added regression
  matrix, the full repository suite passed again: **1478 passed in 33.77s**.

## Outcomes and limitations
The alignment defect is fixed with real table cells, not manual padding.
One-sided file functionality and frozen session ownership remain unchanged.
Both-policy drift is explicitly unsupported. Directory scopes still fail
existing file-session opening before a Deck can be constructed; no directory
convergence or engine migration-rejection path was added.

## Review correction: serialize native cursor navigation
- Independent review reproduced PageDown followed immediately by Space in an
  80x12 PTY: cursor moved to row 7 but Approval authorized row 0. The earlier
  Up/Down-only routing did not cover all native DataTable cursor bindings.
- Added RED regressions at the terminal-event seam: post navigation and Approval
  or Review without yielding between key events. PageDown+Space failed with
  `main:app.unit_00` approved instead of `main:app.unit_07`; PageDown+Enter
  reviewed the old row.
- All DataTable cursor-changing keys now run their native widget actions through
  the same priority application queue as Approval and Review: four arrows,
  PageUp/PageDown, Home/End (column navigation), and Ctrl+Home/Ctrl+End (row
  endpoints). RichLog retains corresponding native scrolling; confirmation
  consumes navigation without changing frozen state. No duplicated page-size or
  endpoint arithmetic. Highlight notifications read current cursor state instead
  of overwriting it with potentially queued coordinates.
- Regression matrix covers all ten navigation keys followed by either Space or
  Enter in one event batch, asserting exact row authority and horizontal cursor
  behavior. Existing review/resize/confirmation tests remain green.
- Focused Deck/command/Pull UI run: **52 passed in 17.79s** before the last four
  horizontal-navigation matrix cases. Final full run: **1478 passed in 33.77s**.
- Re-ran the independent real-PTY probe with `b'\\x1b[6~ '` in one write:
  `REVIEW_RESULT False FOCUS 7 APPROVED [(7, 'main:app.unit_07')]`.
  Evidence and probe sources are in the existing artifact directory as
  `review-pagedown-pty.txt`, `dotman-review-pty.py`, and
  `dotman-review-pty-fixture.py`.
