# Close the remaining Sync implementation gaps

## Goal and scope

Finish the two known implementation gaps left outside #83: exact live chmod drift for both-policy file units, and permanent Push of a typed Missing file source. Preserve existing policy, consent, symlink, frozen-effect, checkpoint, and partial-failure rules. No unrelated refactor or new CLI surface.

The accepted #56 test seams are one-shot SyncSession behavior and existing public CLI/engine facades, using temporary filesystem resources. Each fix must have a failing regression before implementation.

## Work plan

1. Implement and test both-policy file chmod observation/completion.
2. Implement and test permanent Push missing-source outcomes, including preview and checkpoint semantics.
3. Reconcile current user documentation, independently review the fixes and focused acceptance evidence, and run bounded validation.
4. Commit task-owned changes at meaningful boundaries. Close #56 only if acceptance review supports it; otherwise record concrete remaining blockers.
5. Close superseded planning tickets #28, #37, #38, and #39 with successor references.

## Validation

Focused tests during each red/green slice. Independent review challenges policy narrowing, content-versus-mode completion, destructive Missing publication, symlink behavior, and checkpoint timing. At final integration run the full suite once (300-second timeout) because changes cross Sync and permanent Push boundaries. Review clean diff and test isolation before running; never use real user configuration or state as a fixture.

## Progress

- Current GitHub state confirms all implementation subissues #57–#78 are closed; the older active implementation plan is historical and stale, not a list of unfinished subissues.
- Both-policy file chmod fix: two regressions failed before the fix; focused mode/session/directory tests passed (83 tests).
- Permanent Push Missing fix: all six initial engine cases failed before the fix; focused Push, checkpoint, planning, and CLI tests passed (125 tests). Absent custom-Render sources now remain typed Missing rather than generating bytes; updated the superseded test expectation.
- Updated lifecycle comparison rules, Push reference, and explicit target-kind guidance. Existing delete/mode rendering is reused; no new UI style or command surface.
- Closed #28, #37, #38, and #39 as superseded, with successor references.
- Initial integrated suite passed: 1790 tests in 58.97s. Independent review then found an uncovered blocker: patch Capture's source-existence validation rejected Missing Push before the new branch.
- Fixed patch validation test-first: Push retains static patch configuration validation but does not require a source for unused Capture. Added missing patch-source, invalid configuration, snapshot restoration, and followed-link deletion regressions. Focused 132 tests passed; independent re-review passed 19 targeted tests and found no further blocker within the reviewed scope.
- Final full suite: `timeout 300 uv run pytest -q` — **1797 passed in 59.41s**. HOME and XDG paths are isolated by autouse test fixtures. `git diff --check` is clean.
- Committed the mode fix and lifecycle docs as `22b8374` (`fix(sync): detect both-policy file permission drift`).
- Committed Missing Push, patch validation, regression tests, and command/config documentation as `ce24d19` (`fix(push): publish missing file sources as typed absence`).

## Decisions and outcomes

The two known final implementation gaps are resolved and independently reviewed. All #57–#78 implementation subissues and #83 are complete; the old implementation plan is archived alongside this completion plan. The final full suite passes. Review was focused on these fixes and prior completion evidence, not a new exhaustive audit of every historical requirement.

A missing source is not a generator invocation: raw, command, and Jinja/Patch configurations all preserve typed absence during Push. Patch Capture's materialization precondition must not block an operation that never Captures; its static configuration constraints remain enforced. Existing deletion/mode styles, consent, snapshot, and checkpoint paths are reused rather than duplicated.

The independent review exposed a configuration-specific path that the first full green suite did not cover. Keep the patch-Capture regression: total test count alone is not closure evidence without checking meaningful configuration intersections.

#83's checkpoint storage cutover was already completed and was not repeated. Validation evidence is retained under `~/.agents/artifacts/outputs/dotman/2026-09-23/issue-56-final-gaps/`.
