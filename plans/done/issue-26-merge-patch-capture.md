# Merge-based patch Capture (#26)

## Goal

Replace positional patch transfer in `capture = "patch"` with a three-way text merge, so reviewed live edits land on raw template source without flattening template syntax, and anything uncertain fails as an ordinary Capture failure.

## Intention

- Today `apply_review_patch` (`src/dotman/capture.py`) copies changed rendered lines onto the same line numbers in raw source. It rejects most templates with block tags (line-count mismatch) and silently replaces expressions on mixed literal/expression lines.
- The merge treats raw source as "ours", its reviewed Render as the ancestor, and reviewed live as "theirs". Edits that touch rendered-only text conflict instead of overwriting template syntax.
- Safety comes from acceptance checks, not from the merge alone. A failed check is a `CaptureError`; the existing failure path already unapproves the row, so Pull default approval and bulk Approval cannot pass it.

## Scope & Constraints

- Applies to both callers of the shared transfer: Sync/Pull Capture (`src/dotman/sync_capture.py`) and the `dotman capture patch` CLI helper (`capture_patch`).
- Capture-local only. Do not touch Sync Base Reconciliation semantics in `src/dotman/sync_reconciliation.py`; only extract its `git merge-file` invocation into a shared helper.
- No new row/Proposal state, approval rule, or UI surface. The Editor keeps seeding from frozen repository source (`src/dotman/sync_editor.py:75`); never write conflict markers into a candidate.
- Custom render commands keep `capture = "patch"`; they get every check except the Jinja syntax check.
- Remove the superseded positional algorithm and its "same line count" rule; no compatibility path.

## Accepted design

For raw source S, reviewed Render B, reviewed live L:

1. `B == L` → return S unchanged (keep existing verification behavior).
2. Line endings: if S and B use different line-ending styles, convert S to B's style, merge, and convert the candidate back to S's style. If S mixes styles, fail with a clear reason. (Jinja always renders LF, so CRLF templates otherwise always conflict.)
3. `git merge-file` with current = S, base = B, other = L. Non-zero conflict exit → `CaptureError` ("live edits overlap template-generated text"; no file payloads or staging paths in the message).
4. Exact forward verification `Render(C) == L` with the same frozen inputs (existing check, unchanged).
5. Built-in Jinja Render only: the Jinja token sequence of C, excluding literal `data`/`whitespace` tokens, must equal that of S. Mismatch → `CaptureError` ("Capture would change template syntax"). Lex with the same environment options used by file Render. This catches the one unsafe clean merge found: a source line that renders byte-identically to itself.

Rationale, 31-case probe corpus, and rejected alternatives: `~/.agents/artifacts/outputs/dotman/2026-09-23/issue-26-three-way-verdict/verdict.md` and the earlier assessment in `.../issue-26-patch-capture/assessment.md`.

Known accepted costs:
- A literal edit on a line directly next to a tag/expression line conflicts (git treats adjacent hunks as conflicts) and goes to the Editor.
- Custom renderers have no syntax check; the self-render coincidence stays unguarded for them, as today.

## Work Plan

Test-first for each step; assert observable candidate bytes, error reasons, and absence of writes, not diff opcodes.

1. Extract a shared merge helper from `sync_reconciliation.py:48-71` (labels as parameters; conflict vs failure vs interruption kept distinct). Reconciliation keeps its current behavior and tests.
2. Rewrite `apply_review_patch` as the merge-based transfer (steps 1–3 above), taking whatever the helper needs to run git (command runtime) and a flag for the Jinja syntax check. Keep projection/verification with the callers as today.
3. Add the Jinja syntax check (step 5) next to the Jinja environment in `src/dotman/templates.py`; wire the flag from `metadata.render_command == "jinja"` in `sync_capture.py` and `--render jinja` in the CLI path.
4. Update tests that encode positional behavior:
   - `tests/test_capture.py` (line-count rejection, expression-replacement-as-success expectation).
   - `tests/engine/test_pull_projection.py` (sed and `jinja-patch` cases), `tests/engine/test_pull_session.py:119`, `tests/engine/test_sync_pull_convergence.py:188`, `tests/engine/test_repository_checkpoints.py:69`.
   - `tests/cli/test_execute.py:799`, `:833`; `tests/cli/test_help.py:206` if help text changes.
5. New regression cases (from the probe corpus): block-tag template with no edit; literal edit away from syntax; insert/delete/EOF append; two hunks; expression value edit → failure; mixed-line edit → failure with expression intact; one safe + one unsafe hunk → failure, no partial result; loop/include edit → failure; CRLF template literal edit → CRLF preserved; mixed line endings → failure; self-render coincidence → syntax-check failure; custom renderer literal edit → success.
6. Docs: update `docs/templates.md` "Built-In `capture = \"patch\"`" (merge behavior, what fails, Editor as the path for failures) and any `docs/sync.md` wording that implies positional transfer.

## Validation

- Focused: `uv run pytest -q tests/test_capture.py tests/engine/test_pull_projection.py tests/engine/test_pull_session.py tests/engine/test_sync_pull_convergence.py tests/engine/test_repository_checkpoints.py tests/cli/test_execute.py tests/engine/test_sync_both_convergence.py tests/engine/test_sync_directory_convergence.py` (bounded timeout).
- Once at the end: `timeout 300 uv run pytest -q` (change crosses Capture, Pull, Sync, CLI).
- `git diff --check`.

## Progress

- [x] Assessment and probe corpus (2026-09-23).
- [x] Design decisions confirmed by user: custom renderers keep patch; failures reuse existing Capture failure path; line-ending normalization.
- [x] Extracted `src/dotman/text_merge.py` from Reconciliation; Reconciliation suites unchanged and green (41).
- [x] Test-first: rewrote `tests/test_capture.py` for the merge contract (15 tests; red on the new API, green after).
- [x] Merge-based `apply_review_patch`, line-ending normalization, Jinja syntax check; wired Sync Capture and CLI.
- [x] Updated positional-era fixtures: edited literal now separated from the template line by one literal line (adjacent edits conflict by design). CLI tests now assert expression preservation instead of flattening.
- [x] Docs: `docs/templates.md` patch section, `docs/code-structure.md`.
- [x] Focused suites 186 passed; full suite `timeout 300 uv run pytest -q` — 1813 passed in 72s; `git diff --check` clean.

## Surprises & Discoveries

- File templates render with `trim_blocks`/`lstrip_blocks`, so block-tag lines vanish from Render; positional transfer rejects such templates even with no live edit when the identity shortcut is not taken.
- Jinja normalizes template newlines to LF in output.
- The CLI `capture patch --render jinja` projector renders with the string environment (no `trim_blocks`/`lstrip_blocks`), while Sync file Render uses the file environment. Pre-existing and out of scope; follow-up candidate.
- `git merge-file` is fast enough (~3 ms per merge) that no caching is warranted.
- No existing "draft"/needs-review state exists; making uncertainty a `CaptureError` avoids adding one.

## Decisions

- No draft state: uncertain = failure (reuses `sync_session.py:826-829`, `:955-960`).
- Conflict → Editor seeded from unchanged frozen source, consistent with other Capture failures.
- Line endings normalized generically (not Jinja-specific), guarded by exact verification.

## Outcomes & Retrospective

- Patch Capture now preserves template syntax: every probe case that flattened an expression now fails as a Capture error instead.
- No new approval state was needed; uncertainty reuses the existing Capture failure path.
- Follow-up: align the CLI Jinja projector with the file-template environment.
