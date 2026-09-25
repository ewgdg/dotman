# No-Base Sync default (#92)

## Goal

Both-policy drift without a usable Sync Base defaults to **Use repository**
in the Command Deck. Unattended Sync skips that drift instead of guessing a side.

## Intention

Without a Base, dotman has no evidence of which side changed. The old **Use live**
fallback deleted repository sources on fresh hosts where live was Missing, and
repository writes have no snapshot. Use repository matches "the repo is the
declared config", and live writes are snapshotted. An unattended run can't judge
either way, so it leaves the unit for a first review.

## Scope & Constraints

- One consistent interactive default: Use repository for every no-Base case,
  including a Missing side. The Deck still offers Use live.
- Unattended skip is an adapter policy in the Sync runner's default selection.
  The engine does not know about unattended defaults. Push and Pull are unchanged:
  they are the explicit way to pick a direction unattended.
- A skipped unit makes no writes and acknowledges no Base.
- Skips must never collapse into the bare `skipped: N` count. Each one prints a
  named line with remediation, placed with guard skips (before the timeline in
  unattended recap mode, listed in `--report`/dry-run). JSON lists them as
  `no_base_skips`.
- Exit status stays 0.

## Design

- `sync_session.default_intent`: both-policy without a usable Base -> `use-repository`.
- `SyncDeckCommandRunner._select_defaults`: approve all, then unapprove rows that
  carry `fallback_reason` (exactly the no-Base both-policy rows).
- `sync_document`: `no_base_skips` = unattended Sync rows with `fallback_reason`
  that are not approved. The unit loop hides them so nothing prints twice.
- Human output: `[skipped] <identity> (no Base)` then
  `Needs a first review: run dotman sync, or push/pull to choose a side`.

## Progress

- [x] Failing tests: default intent, unattended fresh-host skip keeps repo source,
      JSON list, human hint line.
- [x] Implementation.
- [x] Docs: `docs/cli.md`, `docs/sync.md`.
- [x] Full suite, commit, move plan to done.
