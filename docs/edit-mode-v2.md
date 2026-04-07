# Edit Mode V2

This document captures the future design for bringing `edit` back to the interactive diff menu.

The current v1 diff menu is intentionally read-only. `edit` was removed because the review menu is built from a plan snapshot, and any edit makes that snapshot stale immediately.

## Reintroduction Rule

- Do not reintroduce `e <number>` until dotman can replan after edits.
- Replanning should happen for the current command scope, not just the edited target.
- The preferred flow is to defer replanning until `continue`, not after every edit.
- Existing exclusions should be reapplied to the refreshed plan by stable item identity when possible.

## Scope

- V2 edit mode is primarily for `pull`.
- `push` review should stay read-only unless dotman grows a clearly separate "edit live before deploy" workflow.
- `pull` edit mode should support both plain copied files and transformed targets with `reconcile`.
- Live-side editing should also be supported.

## Editing Targets

- Repo-side editing should remain supported.
- For two-sided reconciliation, dotman should allow editing both repo and live files in one workflow.
- Review content may still use planning projections, but editable buffers must point at the real files unless the workflow is explicitly scratch-only.

## Suggested Modes

- `edit repo`
  Open the real repo-side source files for manual reconciliation.
- `edit live`
  Open the real live file when the user needs to normalize or salvage machine-local changes before deciding what to keep.
- `edit both`
  Open both sides for direct merge work when the editor supports multi-file workflows well.
- `reconcile`
  Run the target-defined reconcile command when the package provides one.

The UI does not need all of these as separate top-level review commands immediately. A single `edit` entry can dispatch to the best mode for the target, but the underlying capability should cover both sides.

## Planning Contract

- Diff preview should still be based on planning views.
- Edit mode should never silently mark an item as resolved.
- After the first edit in a review session, dotman should consider the current review menu stale.
- Once stale, the review menu should not pretend the remaining diff list is still current.
- The preferred v2 behavior is:
  1. allow edit or reconcile to run
  2. return to a stale review state that permits `continue`, `abort`, or an explicit refresh
  3. on `continue`, rebuild the plan for the current invocation scope
  4. reapply exclusions
  5. continue from the refreshed state
- If dotman later adds an explicit `refresh` action, it should use the same full-scope replan behavior.

This is mandatory because an edit may:

- fully resolve drift
- partially resolve drift
- change the action type
- affect multiple source files
- affect generated or captured planning views

## Safety Guardrails

- Temporary review artifacts should stay read-only scratch files.
- Editable paths must be explicit in the UI so the user can tell whether they are changing repo files, live files, or both.
- Live-side editing should be opt-in and only available when the file exists and is writable.
- Non-zero editor or reconcile exit codes should not be treated as success, but dotman should still replan because files may have changed anyway.
- After any edit, dotman should clearly mark the review session as stale so the user does not trust old diffs.

## Open Questions

- Whether `push` should ever allow live-file editing, or whether that is a separate command entirely.
- Whether `edit repo`, `edit live`, and `edit both` need distinct review-menu commands, or whether one command should branch inside the editor helper.
- How directory targets should expose edit mode without turning the review menu into a file browser.
