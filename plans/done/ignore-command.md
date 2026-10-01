# Computed target exclusions: `ignore.command`

## Goal

A directory target can exclude paths computed from live state, in addition to
static `ignore.patterns`:

```toml
[targets.d_agents.ignore]
patterns = ["tools/"]
command = "sh hooks/npx-skill-ignores.sh"
```

The command prints extra Git ignore lines relative to the target root.

## Intention

Driving case: `~/.agents` is one directory target, and `npx skills add -g`
installs third-party skills inside it. Those skills are listed in a lock file
and should be installed by a probe, not copied into the repository. A static
pattern list duplicates the lock file and drifts.

The following alternatives were rejected by a weighted decision matrix:

- Live skip markers: `skills@1.7.0` runs `rm -rf` on the skill directory on
  every add or update.
- Static or repository-side markers: they drift from the lock file.
- Allowlist inversion: a new custom skill would be silently left out.
- Generated pattern files: they go stale when skills change outside dotman.
- A declarative JSON selector: too narrow.

## Scope and constraints

- Target level only. Package and repository levels wait for a real need.
- The value is a single string, like `probe` and `capture`. A command
  produces one result; a shell string or script can combine sources.
- The command is Jinja-rendered like `probe`. It runs with the target's
  `command_cwd` (the declaring package directory) and the target command
  environment (`DOTMAN_REPO_PATH`, `DOTMAN_LIVE_PATH`, `DOTMAN_OPERATION`, ...).
- It applies identically to Push, Pull and Sync through the shared directory
  census. Excluded paths are neither observed nor changed, and they are
  preserved by directory writes and stale-path deletion.
- It runs only when a directory target is censused during observation. It
  never runs during projection, so `info`, `list` and collision validation
  don't execute it. It runs once per session, and frozen execution never
  re-observes.
- Side-effect-free by convention, the same contract as probes.
- Fail fast:
  - A non-zero exit, output that isn't UTF-8, or a `!` negation line aborts
    the session open with `planning-failed`, naming the target and the first
    output line.
  - Empty output means no extra exclusions.
- Computed lines are appended after the static layers and must not use `!`,
  so they can only narrow what syncs.
- Collision validation sees only static patterns. That is stricter and
  therefore safe.
- Inspection (`doctor` Sync Base orphan checks) does not execute the command.
  A census with an unresolved command is treated as restricted, so it can't
  prove a path absent.
- A probe target must not define `ignore.command`, matching `ignore.patterns`.

## Work plan

1. Tests first (`tests/engine/test_ignore_command.py`), failing for the
   expected reason:
   - Push, Pull and Sync observation omit command-excluded children on both
     sides.
   - Push preserves an excluded live child; Pull preserves an excluded
     repository child and does not capture the excluded live child.
   - The command runs in the package directory (reads a package-relative
     file).
   - A failing command, or one that prints a negation line, fails the session
     open with `planning-failed` and the target identity.
   - Manifest validation: the value is a non-empty string, and a probe target
     rejects it.
2. Model and manifest:
   - Add `TargetSpec.ignore_command` and the `command` key to
     `TARGET_IGNORE_KEYS`.
   - Validate it, merge it across presets and `extends`, and add it to the
     probe forbidden-field checks in `manifest.py` and `projection.py`.
3. Projection:
   - Add `TargetMetadata.ignore_command` (rendered).
   - Add `resolve_ignore_command(runtime, metadata)` beside
     `run_probe_command`. It returns metadata with the computed lines
     appended and the command cleared.
   - Generalize `ProbeCommandError` into `PlanningCommandError` so both
     commands share the session-open failure path.
4. Observation:
   - Resolve the command just before `census_directory` in `observe_scope`.
   - The census treats an unresolved command as restricted.
5. Docs and skill:
   - `docs/repository.md` § Unified exclusions: key table, semantics and an
     example.
   - `skills/dotman/references/noise-filtering.md`: when to use the command
     instead of patterns.

## Validation

- Run the new test file and `tests/engine/test_ignore_patterns.py`, then
  directory observation/topology, probe and planning-guard tests. Run the full
  suite once at the end.
- No new UI: failures reuse the existing `planning-failed` session diagnostic,
  and `info` does not render ignore settings today.

## Progress

- [x] Investigation and design decisions.
- [x] Failing tests (all 15 failed on `unsupported keys: command`).
- [x] Implementation.
- [x] Docs and skill.
- [x] Full suite: 2039 passed.

## Surprises and discoveries

- `docs/repository.md` says target guards run before directory scanning, but
  `observe_scope` runs the census before guards on purpose ("configured
  ineligibility must survive a later Guard failure"). So the ignore command
  runs even when a guard would later skip the target. Docs vs code mismatch;
  out of scope, propose separately.
- All exclusion enforcement flows through `census_directory`. Its other
  caller is Sync Base inspection.

## Decisions

- `command` (singular), target level, appended last, exclusion-only.
- Abort on failure; no fallback. Users who want lenience write `|| true`
  explicitly.
- No `info` rendering of computed lines, so read-only commands never run user
  commands.
- Dotfiles follow-up: the command should read the union of the repository and
  live lock files. On a fresh machine's first push the live lock does not
  exist yet.

## Outcomes and retrospective

- Shipped as planned. The command resolves in one call before
  `census_directory`, so Push, Pull and Sync share the same exclusion
  behavior with no per-operation code.
- `ProbeCommandError` became `PlanningCommandError`, shared by probes and
  ignore commands.
- Test pitfall: inside a TOML literal string, `\"` reaches the shell as a
  literal quote. Use plain double quotes in test commands.
