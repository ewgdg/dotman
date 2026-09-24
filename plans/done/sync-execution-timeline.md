# Sync execution timeline

## Goal

Human Sync/Push/Pull execution streams a step timeline while it runs (the
pre-`fffbc7b` design), instead of printing one log after everything finished.

## Intention

Users watching a long push must see which step is running, its live hook output,
and each step's status as it lands. The end-of-run entry log only recaps what the
timeline could not show, so nothing prints twice.

## Scope & Constraints

- Human execute mode only. Dry-run, abort and pre-execution failures keep the
  current entry log. JSON prints no progress and stays byte-for-byte unchanged.
- Restore child-output streaming in human mode (lost since `fffbc7b`: nothing
  passes `stream_output=True`). TTY hooks keep terminal passthrough.
- Events are typed and synchronous, emitted in execution order; the engine never
  prints. Renderers never execute.
- `unit-completion` steps are bookkeeping and stay invisible.
- No compatibility path for the flush-at-end human execute log.

## Design

- `execution.py`: `StepStarted(stage, step, index, total)` and
  `StepFinished(stage, result, index, total)`. `index/total` count visible steps
  within one group (repo hooks group by repo, everything else by package) per stage.
- `execute_repository_apply` / `execute_publication` take `observe`; each visible
  planned step emits Started, then Finished with the first attempted result of that
  iteration (snapshot-create failure reports under the write that triggered it).
  Snapshot finalize failure emits a Finished without a Started.
- Sessions forward these through the existing `event_sink` (added to
  `SessionEvent`) and take `stream_output`.
- CLI human execute: pass the timeline renderer as `event_sink`, `stream_output=True`.

Timeline format (old design):

```
:: Push
  main:app
    [1/3] pre_push   ./check.sh
          <live hook output>
          ok
    [2/3] write      ~/.config/app/unit
          ok
```

Failed hook: `exit N` then `failed` (its output was already streamed). Other
failures: error line then `failed`. Interrupted TTY hooks print nothing extra.

Final recap (execute mode): entries whose outcome is not `ok` or that carry
diagnostics, minus failures already shown in the timeline; no Resolution/effect
detail lines; guard skips; summary line; stderr diagnostics not already shown.

## Work Plan

1. Tests: engine event order (hook + write + failure), CLI timeline output with
   live hook output, recap dedupe, JSON unchanged.
2. Engine events + observe seams; session forwarding and `stream_output`.
3. Timeline renderer + recap in `sync_deck_command.py`; wire runner.
4. Docs (`docs/sync.md` Result log), full suite, commit.

## Validation

- Focused: `tests/cli/test_sync_deck_command.py`, execution/pull/push CLI tests.
- `uv run pytest -q` once at the end.

## Progress

- [x] Old design and current seams inspected; layout decided by user (timeline replaces log).
- [x] Engine events + session forwarding (Sync, Pull, Push) test-first.
- [x] Timeline renderer, recap log, docs; full suite green (1741).

## Surprises & Discoveries

- Pull overrides `_publish`, so it needed its own wiring.
- Additional Source Changes apply outside the step loop; the recap always lists them.
- Once the timeline showed failed steps, the recap's failed-step entries and
  `SyncStepOutcome.output_line` (from #90/#91) were superseded and removed.

## Decisions

- Timeline replaces the execute-mode entry log (user choice over unit-grouped
  timeline or timeline + full log).
- Reuse `event_sink` rather than a second observer channel.

## Outcomes & Retrospective

- Human execute mode streams `[i/n] action target` + live hook output + status,
  then a recap of only what the timeline could not show.
- JSON unchanged; dry-run/abort keep the outcome-led entry log.
