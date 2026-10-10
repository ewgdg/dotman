---
status: done
---

# Parallel planning commands

## Goal

Sync, Push and Pull open faster. Comparison projection commands and probe
commands run concurrently with a small bound, and stay deterministic and
interruptible. Results, row order, diagnostics and the reported error stay
exactly as they are today.

## Intention

- Planning time is mostly time spent waiting on user commands. Measured on a
  real dotfiles repo (`dotman --unattended --json push -d`, 24 cores):
  - 4.6s wall; 70 subprocesses account for 4.2s.
  - Observation: 24 projection commands, 2.5s. Probes: 46 commands, 1.7s.
  - Children are CPU-bound (87% child CPU per wall second, 95% for
    projections). Most of that is interpreter startup (`uv run`, `dotman
    transform`, `npm`). dotman itself only waits, which releases the GIL.
- Ideal makespan from the measured durations, assuming no contention:

  | Workers | Observation | Probes | Total command time |
  |---|---|---|---|
  | 1 | 2.53s | 1.65s | 4.2s |
  | 2 | 1.28s | 0.95s | 2.2s |
  | 4 | 0.66s | 0.63s | 1.3s |
  | 8 | 0.36s | 0.51s | 0.9s |

- Separately, `_wait` adds ~10-12ms to every command.
  `Popen.wait(timeout=0.05)` restarts its backoff ladder on each loop, so an
  exit is noticed late: `sleep 0.07` measures 82ms, `sleep 0.12` measures
  132ms. That is ~0.7s here, and it stays a cost after parallelizing.
- Evidence: `~/.agents/artifacts/outputs/dotman/2026-10-02/parallel-drift-detection/`.

## Scope and constraints

In scope:

- `_observe_file` calls in `sync_observation.observe_scope`.
- `run_probe_command` calls in `sync_auxiliary.plan_auxiliary`.
- `_wait` polling in `command_runtime.ProductionCommandRuntime`.
- Serializing sudo ticket acquisition in `file_access._SudoLease.request`.

Out of scope:

- Guards, `ignore.command`, hooks and execution. They keep their documented
  serial order.
- asyncio. The runtime is synchronous and blocks on subprocesses. Threads give
  the same overlap without a rewrite.
- A user-facing setting for the bound. Add one only when someone needs it.

Constraints:

- Concurrency bound: `PLANNING_COMMAND_CONCURRENCY = min(4, os.cpu_count() or 1)`.
  Four keeps ~85% of the ideal gain and avoids oversubscribing small
  machines, because children are CPU-bound.
- Sync Base store access (`lifecycle.inspect`, `direct_agreement`) stays on
  the calling thread, in target order. Workers never touch the store.
- Output is identical to serial planning: observations in target order,
  auxiliary rows in input order, and the same error when several targets fail.
  The error reported is the first in target order, not the first to finish.
- Ctrl-C stops every running child. Projection and probe children own their
  process groups, so the terminal's SIGINT never reaches them; dotman must
  kill them through the shared cancellation latch.
- Workers run in `contextvars.copy_context()`. The cancellation latch
  (`_ACTIVE_OPERATION`) and the command runtime (`_ACTIVE_COMMAND_RUNTIME`)
  are ContextVars. `SyncSession.open` already wraps planning in
  `command_operation()`, so copied contexts share one latch.
- Protected endpoints are read through sudo (`file_access.read_bytes`,
  including the projection staging path). Two workers must never prompt for a
  password at once. One lock around ticket acquisition makes the first worker
  prompt; the others wait, then find a valid ticket.
- Projection providers are already documented as side-effect-free stdout
  producers. Probes get the same statement in the docs, which now also say
  that these commands may run concurrently.

## Design

One small helper, used by both call sites:

```python
def run_ordered(tasks: Sequence[Callable[[], T]]) -> Iterator[T]:
    """Run tasks on a bounded pool; yield results in task order."""
```

- Each task is submitted as `copy_context().run(task)`.
- Results are yielded in submission order. The caller handles each result
  serially on its own thread as soon as that result's turn comes up, so
  progress still advances during the run.
- On an exception (including `KeyboardInterrupt` on the main thread):
  call `request_cancel()` on the active operation, cancel pending futures,
  wait for running ones (their `_wait` sees the latch and stops their process
  groups), then re-raise the original exception.
- A single task, or a bound of 1, runs inline with no pool. Tests and small
  scopes then take the serial path unchanged.

Observation becomes three steps, keeping today's per-unit semantics:

1. Serial: resolve `effective` policy and run `lifecycle.inspect` for every
   unit to build its `BaseEvidence`. These are fast store reads, already done
   before `_observe_file` today.
2. Parallel: `_observe_file(...)` per unit (endpoint reads and projections).
   Child-failure units skip the pool, as today.
3. Serial, in order, as results arrive: `direct_agreement`, warnings,
   directory topology, `sink.update(1)`.

Probes: the `directory-root` chmod checks stay serial (they only stat). Only
`run_probe_command` goes through `run_ordered`; rows are assembled in input
order afterwards. `PlanningCommandError` from the first failing probe in
input order propagates, as today.

`_wait` fix: replace the `Popen.wait(timeout=…)` loop with
`while process.poll() is None: operation_event.wait(_EXIT_POLL_SECONDS)`.
This uses a short fixed interval (~5ms) and wakes immediately on cancel.
Choose the exact mechanism by measurement. The bar: `sleep 0.07` measures
within ~5ms of raw `subprocess.run`.

## Work plan

Each step is its own commit.

1. `perf(runtime)`: `_wait` fix.
   - Test first: a cancel request during a long command still stops it
     promptly (existing cancellation tests cover most of this; check).
   - Measure before and after with the `sleep` probe above.
2. `fix(file-access)`: lock around `_SudoLease.request`.
   - Test first: two threads requesting a lease with no ticket produce one
     TTY prompt (MemoryCommandRuntime counts `-v` tty requests).
3. `perf(sync)`: `run_ordered` helper and parallel observation.
   - Tests first:
     - Observations stay in target order when a later target's projection
       finishes first (MemoryCommandRuntime with callables that block on
       events).
     - Interrupting while two projection commands run stops both child
       process groups. Use real short `sleep` commands with a bounded test
       timeout, and assert the children are gone.
     - Base acknowledgment still happens only for directly-in-sync eligible
       units, unchanged under concurrency.
4. `perf(sync)`: parallel probes.
   - Test first: when two probes fail, the error reported is always the one
     earlier in input order.
5. `docs`: `docs/sync.md` (planning commands may run concurrently, up to 4;
   probes and projections must be side-effect free) and the dotman skill's
   probe/projection guidance under `skills/dotman/`.

## Validation

- Targeted tests per step, then the full suite once at the end.
- Re-run the timing harness on the real dotfiles repo
  (`push -d`, `pull -d`, `sync` in unattended JSON). Expect wall time to drop
  from ~4.6s to ~2s or less, with identical JSON output to serial
  (diff serial vs parallel output by forcing the bound to 1).
- Manual: a Ctrl-C during `dotman sync` planning leaves no orphan children
  (`pgrep -f` on a long-sleeping probe).

## Progress

- [x] 1. `_wait` polling fix: fixed 5ms `poll()` loop. Real repo: summed
  command time 4.2-4.4s -> 4.0s, wall ~4.6s -> ~4.4s.
- [x] 2. sudo ticket lock: one module lock around `request_sudo`.
- [x] 3. parallel observation: `run_ordered` in `command_runtime`. Real repo
  wall ~4.4s -> ~2.75s; push/pull `-d` JSON identical to a bound of 1.
- [x] 4. parallel probes. Real repo wall ~2.75s -> ~1.8s (baseline ~4.6s);
  push/pull `-d` JSON identical to a bound of 1.
- [x] 5. docs: `docs/repository.md` (probe and projection contract) and
  `docs/sync.md` (Observation, Probe Work). The skill is unchanged: it is an
  index that already routes probe and projection authoring to those sections.

## Surprises & Discoveries

- `_wait` polling latency (see Intention) turned up while profiling.
- The `_wait` fix saved ~0.25s, not the estimated ~0.7s. The per-command
  overshoot depends on where each exit lands in the backoff ladder, and the
  real durations land in its gaps less often than assumed.
- Projection staging reads protected files through sudo, so workers can reach
  the TTY prompt. Hence step 2.

## Decisions

- Threads over asyncio. Weighted score: thread pool 4.50, serial plus
  polling fix only 3.95, asyncio 3.35. Criteria: speedup 35%, correctness
  risk 30%, simplicity 20%, fit with the existing runtime 15%.
- The bound is 4, not 8. Children are CPU-bound, 4 already captures most of
  the gain, and it is gentler on laptops.
- No configuration knob yet.

## Outcomes & Retrospective

- `dotman --unattended --json push -d` on the real dotfiles repo: ~4.6s ->
  ~1.8s. Push and pull JSON are byte-identical to a bound of 1.
- Ctrl-C with four 30s probes running: exit 130 in 0.5s, no children left.
- The `_wait` fix alone was worth less than estimated (~0.25s, not ~0.7s).
- Parallel speedup came in close to the ideal-makespan estimate, as expected
  with 24 idle cores; smaller machines will see less.
- Lesson: never revert a single line in a dirty file with `git checkout -p`.
  It reverted the wrong hunk, and step 3 had to be amended.
- Revised 2026-10-02: the bound is now `min(8, os.cpu_count())`. After cheaper
  helper startup, the dotfiles plan measured ~1125ms at 4 workers, ~940ms at 8,
  ~867ms at 12 and ~850ms at 24 on 24 cores. Eight keeps most of that gain
  without bursting across every core of a large machine.
