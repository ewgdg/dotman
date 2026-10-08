# Commit Work: pre-approved git commit after Sync and Pull

## Goal and intention

Let a user decide, before execution, that the repository changes a Sync or Pull
writes are committed to git right after the run. Usage is occasional, so the
choice is per run and off by default.

## Scope and constraints

- One **Commit Work** option per dotman repo that has drift and lives in a git
  work tree, held on the session view (`commit_options`), not as a workset row.
  Sync and Pull only; Push writes no repository source outside deliberate edits.
- Chosen on the confirmation screen: a scrollable per-repo toggle list
  (Textual `SelectionList`) listing repos the selection writes. `x`/Space/click
  toggle one; `g` turns all on if any is off, else all off. `--commit`
  pre-selects all. Workset batch selection never touches it.
- One commit per dotman repo, even when two share a git work tree.
- Execution: after all other work, one commit per repo with the selected row.
  Skipped when any earlier work failed. Commits only paths dotman wrote
  (approved Primary Source Changes that converged plus applied Additional Source
  Changes): `git add -A -- <paths>`, then `git commit -- <paths>`, so unrelated
  staged or unstaged work stays out.
- Message from `[git] commit_message` (default
  `chore(dotman): {summary}\n\n{packages}`). Placeholders: `{operation}`,
  `{count}`, `{summary}`, `{packages}`, `{repo}`. `{summary}` names the
  narrowest scope: one target `pull zsh.zshrc`; one package
  `pull zsh (3 targets)`; else `pull 5 targets in 3 packages`. No repo prefix:
  the commit already lives in that repo.
- No git push, no default-on config.

## Work plan

1. Tests first (engine): row presence, default off, `commit=True`, batch
   immunity, commit of written paths only, failure skip, no-changes skip,
   message rendering.
2. `sync_commit.py`: git work-tree probe, message rendering, commit execution.
3. Session wiring: row creation at open, Pull opt-out exclusion, execution after
   publish, step events, `SyncResult.commits`.
4. Config `[git] commit_message` with fail-fast placeholder validation.
5. CLI `--commit` on sync/pull; JSON `commit_work`; Deck label/detail
   (branch + message preview); timeline step.
6. Docs (`cli.md`, `sync.md`, `config.md`), skill, README screenshot check.

## Validation

Real git repos in tmp dirs; assert commit contents (`git show --name-only`),
messages, and that unrelated changes stay uncommitted. Targeted test files only;
full suite once at the end.

## Progress

- Design agreed with user (message format, default off, no push).
- Engine + config done test-first (`tests/engine/test_sync_commit.py`).
- Switched from workset row to confirmation toggle after an HTML prototype
  (`prototype/commit-work-deck.prototype.html`, captured on a throwaway branch).

## Decisions

- Confirmation toggle (score 4.25) over workset row (3.30) and pinned line
  (2.70). The row needed special cases in batch select, Pull opt-out, JSON
  scope and repo-hook outcome matching, and broke ~130 tests that read
  `view.rows`; a pinned line truncates with several repos and scrolls out of
  view on long worksets. Flag-only and default-on config rejected earlier.
- `{targets}` placeholder rejected: duplicates `git show --stat`, bloats large
  pulls.
- Pathspec commit over `git add -A`: never sweeps unrelated work. Known gap:
  files a pull hook writes are not committed.

## Surprises & Discoveries

## Outcomes & Retrospective
