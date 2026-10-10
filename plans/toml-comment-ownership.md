---
status: done
---

# TOML comment ownership

## Goal

Replace the TOML engine's layered comment workarounds with one ownership rule,
applied once right after parsing, so selection and merge just move whole items.

## Intention

tomlkit stores a comment by parse position, not by meaning: a comment above
`[b]` lands in the previous table's body. The engine patched this in several
places (tail detaching, attached-comment removal, top-level trivia
restoration, separator collapsing), and the patches disagree. One known bug:

```toml
[a]
x = 1
# about b
[b]
```

Removing `b` orphans `# about b`, removing `a` deletes it, and a merge that
re-adds `b` from the overlay duplicates it.

## The rule

A comment block is a run of comment lines with no blank line between them.

1. A block directly above an item (no blank line between) is **attached** to
   that item. It moves and is deleted with the item.
2. A block at the end of a table that directly follows the table's content
   (no blank line between) and is not attached to a following item is
   **owned by that table**.
3. Every other block is **independent**. It stays where it is when items
   around it are deleted.

Inline (end-of-line) comments already belong to their line's item.

### Storage after the ownership pass

- Attached blocks are prepended to the item's header indent (for a table
  without a header, the first header rendered inside it; for an array of
  tables, the first element).
- Table-owned blocks stay at the end of the table's textual body.
- Independent blocks are standalone body entries in the container that holds
  the following item, or the tail of the document.
- Known tomlkit limits: a standalone comment inside an implicit parent (a
  header-less table like `b` in `[b.k]`) would make tomlkit print `[b]`, so an
  independent block there stays above the next child's header. Between
  array-of-tables elements, it stays in the earlier element.

The pass changes placement only; rendering the document gives back the
exact source text.

## Merge

One recursive merge per container, replacing the top-level-only restore:

- Key order is unchanged (base order, then overlay-only, then kept-base-only),
  except that values go before sections, so tomlkit never moves a value away
  from its leading comments.
- An item from the overlay brings its attached comments; a kept base item
  brings its own.
- A table present on both sides takes its header (and header comments) from the
  overlay and merges its body recursively.
- Independent blocks come from the side that supplied the following item.
  A base block whose text also appears as an independent block in the same
  overlay container is dropped.
- A container's tail comes from the overlay, else from the kept base.
- A kept-base-only section with no leading trivia gets a blank line before it,
  like a new table.
- Blank-line runs are collapsed once on the final text, and the document does
  not end with blank lines.

## Scope & Constraints

- Only `src/dotman/transforms/toml.py` and its tests; no CLI or doc change
  beyond noting the rule in `docs/cli.md`.
- Keep existing TOML tests passing unless one pins behaviour the rule
  intentionally changes; record any such change here.
- No compatibility path for the old placement logic.

## Work Plan

1. Failing tests for the known bug (cleanup remove either table, merge).
2. Ownership pass at parse; delete tail detaching and attached-comment removal.
3. Recursive merge; delete top-level trivia restore and separator collapsing.
4. Comment signature for compare reuse computed on owned documents.
5. Docs, fuzz, full suite.

## Validation

- Ownership pass renders every fuzzed source unchanged.
- `uv run pytest -q tests/test_toml_transform.py tests/test_toml_root_cli.py`.
- Scratch fuzzer `toml_fuzz.py` shows no new failure kinds.
- Real codex and noctalia configs: capture then render is a no-op.
- Full suite before handoff.

## Progress

- [x] 1. Failing tests
- [x] 2. Ownership pass
- [x] 3. Recursive merge
- [x] 4. Signature
- [x] 5. Docs and validation

## Surprises & Discoveries

- tomlkit itself does not round-trip some out-of-order files (e.g.
  `[a.c.f]\nx = 1\n[[k]]\n[a.c]\n`), so the identity check compares against
  tomlkit's own rendering.
- tomlkit indents each new child of a table or inline table by any spaces in the
  table's indent. That indent now holds attached comments, so merged tables are
  filled under blank trivia and wrapped afterwards.
- `container[key]` and proxy `.items()` return a plain `bool` with no trivia,
  which dropped attached comments; lookups use the stored item instead.
- A key object parsed from a `[table]` header keeps header spelling, and a dotted
  key renders sections nested under it at the wrong path. Keys are rebuilt by
  name unless a dotted table renders only key-value lines.
- tomlkit inserts new values above unfixed trailing whitespace; merged trivia
  whitespace is marked fixed.

## Decisions

- User approved the overhaul and the rule (2026-09-29).

## Outcomes & Retrospective

Capture-then-render fuzz over 1500 random files, baseline → new:

| Check | Baseline | New |
|---|---|---|
| Comments lost or added | 201 | 0 |
| Second render changes output | 762 | 1 |
| Exact round trip | 150 | 373 |

No file that round-tripped exactly before regressed. The value fuzz (4500 seeds)
has the same failure profile as the baseline. The one non-idempotent seed has an
out-of-order source (`[[t.f]]` before `[t]`), and the baseline has it too.

Real configs: codex capture equals the repo copy, and render with
`--compare-file` equals the live file. Noctalia capture equals the repo copy.
