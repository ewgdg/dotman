# Move the TOML transform from tomlkit to tomlrt

## Goal

Rebuild `src/dotman/transforms/toml.py` on tomlrt's public API so the TOML engine uses no private library internals and needs no version pin, with at least today's behaviour.

## Intention

tomlkit forces private internals (`_body`, `_map`, `OutOfOrderTableProxy._tables`, `Array._value`) and a layer of workaround code, because it attaches comments to the previous item and re-indents or hoists inserted items. tomlrt attaches comments to the next item, keeps blank-separated comment groups apart, round-trips untouched bytes exactly (CRLF, BOM, split tables), and documents a semver-stable public API. The comment ownership rule in `docs/cli.md` should become a thin mapping onto that API.

## Scope & Constraints

- In scope: parse/render, comment ownership, cleanup (retain/remove), merge, compare-file reuse, removal of the tomlkit dependency.
- Keep the user-facing contract in `docs/cli.md` (selectors, comment rule, blank lines, line endings, dotted keys, inline style).
- Public tomlrt API only (top-level `tomlrt` exports). No private attributes.
- Out of scope: the other transform engines.

## Work Plan

0. Gates (prototype, no rewrite): scalar spelling on cross-document copy; comments inside brackets. See Progress.
1. Parse/render and the comment signature on tomlrt, with the existing TOML tests as the oracle.
2. Cleanup (retain/remove) as in-place edits of one parsed document.
3. Merge: clone the overlay, graft live-only retained items (containers byte-faithfully; scalars per the gate-1 decision).
4. Remove tomlkit from `pyproject.toml` and `uv.lock`; delete the superseded helpers.

## Validation

- `tests/test_toml_transform.py`, `tests/test_toml_root_cli.py`, `tests/test_transform_cli.py`.
- Capture/render fuzz (1500 files) and value fuzz; compare against the tomlkit baseline (comment diffs 0, non-idempotent 1, exact round trips 373).
- Smoke: real codex and noctalia configs.

## Progress

- 2026-09-29: Gates run with tomlrt 2.2.14.
  - Gate 1 **failed**: every public route re-spells a scalar copied between documents (`0x10`→`16`, `1_000`→`1000`, `'C:\dir'`→`"C:\\dir"`). Tables, arrays and arrays of tables copy byte-faithfully.
  - Gate 2 **partial**: per-element array comments are exposed; comments right after `[`, right before `]`, and trailing inside an inline table are not. A text scan of a rendered clone covers them.

## Surprises & Discoveries

- tomlrt `entry()` returns plain Python values; there is no scalar view. Only containers carry formatting across a copy.
- Same-document moves also re-spell scalars.

## Decisions

- Blocked on: how merge handles live-only retained scalars inside a table the repo copy also has. Options: accept re-spelling (document it), wait for an upstream public API that copies a key slot with its formatting, or stay on tomlkit's public API.

## Outcomes & Retrospective

(pending)
