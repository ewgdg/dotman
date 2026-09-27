# Command Deck search and workset filter

## Goal

`/` searches in the Command Deck:

- Workset: `/` filters the table to matching targets.
- Reader (Proposal/Source Change Review and Full View): `/` highlights matches;
  `n`/`N` step through them.

## Intention

Large worksets and long Full View files need a way to reach the part that
matters. Filtering the workset also enables "approve everything matching
`zsh`" through the existing `A`/`U` keys.

## Decisions

- Workset filters rather than jumps (weighted matrix, 2026-09-26). The main
  reason is narrowing a group and acting on it. Filtering is the familiar
  behaviour for tables (lazygit, k9s, fzf).
- Safety of a filtered workset:
  - `A`/`U` act only on visible rows.
  - While a filter hides approved rows, the title shows how many, e.g.
    `12/80 shown · 3 approved hidden`, so `X` never runs rows the user
    forgot about.
- Reader `n`/`N` depend on context (matrix 4.35 vs 4.15 for moving change
  blocks to `]`/`[`):
  - With a search active, `n`/`N` step through matches.
  - Otherwise they step through change blocks as before.
  - The active search is always visible in the hint line
    (`/foo 3/12 · n/N match · Esc clear`), so the mode is never hidden.
- A search or filter with no matches shows `no match` and is not kept, so `n`
  never becomes a dead key.
- Esc steps back one layer at a time:
  - Workset: review → filtered workset → full workset.
  - Reader: active search → view.
  - `q` still aborts only from the idle workset. A filtered workset is not
    idle, so Esc clears the filter first.
- Workset filter lifetime: the filter survives opening a review and returning.
  A reader search is scoped to its view and cleared when leaving it.
- Matching: case-insensitive substring. Workset matches against the plain
  canonical identity (`repo:package.target`). Reader matches against the plain
  text of each review leaf (subject, fact values, notes, diff and conflict
  lines).
- Search box: a Textual `Input` docked above the help line.
  - All deck bindings are `priority=True`, so while the Input has focus,
    `App.check_action` disables every action except `back` (cancel) and
    `abort` (Ctrl+C).
  - Textual's `run_action` returns False for a disabled action, so the key
    falls through to the focused Input. Verified against Textual 8.2.8
    `App._check_bindings` / `run_action`.
  - This reuses Input's editing and paste support instead of hand-rolling a
    line editor in `on_event`.
- Reader highlighting happens before layout: each Rich `Text` leaf is styled
  with a reverse style carrying `{review_match: <match index>}` metadata.
  Wrapped matches still highlight on both rows, and `ReviewBody.render`
  records each match index's first row. This mirrors the existing
  `REVIEW_CHANGE_KEY` mechanism.
- Scoped bulk selection: `BatchSetApproval` gains a required
  `row_ids: tuple[str, ...]`. The unfiltered `A` passes every row. No
  compatibility path for the old session-wide form.

## Scope & Constraints

- Code: `src/dotman/sync_deck.py`, `src/dotman/sync_deck_command.py`,
  `src/dotman/sync_session.py` (`BatchSetApproval` only).
- Tests: `tests/cli/test_sync_deck_textual.py`; session tests for the
  `BatchSetApproval` contract.
- Docs: `docs/cli.md` sync keyboard section. Hint line styles follow the
  existing `render_key_hints`.
- Out of scope: regex/smartcase, a distinct style for the current match,
  jump search in the workset, filtering by column other than Target.

## Work Plan

1. Reader search (layer 1, works end to end on its own):
   1. Red: Textual tests for `/` in review and Full View (typing does not
      trigger deck keys; matches highlighted; `n`/`N` step matches with a
      query and change blocks without one; hint shows `3/12`; Esc clears then
      leaves; no match leaves no query).
   2. Green: search Input, `check_action` gate, match styling in
      `ReviewDocument.renderable`, match rows in `ReviewBody`, `n`/`N`
      dispatch, hints.
   3. Update `docs/cli.md`; commit.
2. Workset filter (layer 2):
   1. Red: session test for scoped `BatchSetApproval`; Textual tests for
      filtered rows, `A`/`U` scoped to visible rows, hidden-approval title,
      focus/click mapping under filter, filter kept across review, Esc
      layering, `q` behaviour.
   2. Green: `CommandDeck.filter_query` and `visible_rows`, with `focus`
      indexing visible rows; rebuild/click/update paths use visible rows;
      scoped `BatchSetApproval`.
   3. Update `docs/cli.md`; commit.

## Validation

- Targeted: `uv run pytest tests/cli/test_sync_deck_textual.py` plus the
  session tests touching `BatchSetApproval`.
- Before handoff: the full `tests/cli` and `tests/engine` sync suites.

## Progress

- 2026-09-26: design settled with the user; plan written.
- 2026-09-26: layer 1 (reader search) done. Red on the missing `#search` and
  `n` not stepping matches; green with `tests/cli` at 583 passed. The search
  line gets a `/` prompt (Horizontal of Static `/` + compact Input).

## Surprises & Discoveries

- `App.check_action` returning False makes `_check_bindings` skip a priority
  binding and forward the key to the focused widget, so no `on_event` key
  interception is needed.
- Stepping matches by scroll anchor gets stuck when the last matches sit below
  the furthest reachable scroll position, so `n`/`N` keep a match index and
  wrap around.
- A key batched with Enter can arrive before the first render counts matches;
  `n`/`N` ignore that gap instead of dividing by zero.

## Outcomes & Retrospective

(pending)
