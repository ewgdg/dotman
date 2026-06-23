# CLI Entrypoint and Interactive Flows

## Intent

Provide the top-level `dotman` command, compatibility helpers, interactive selection/review prompts, and CLI orchestration.

## Behavior

```pseudo
main(argv):
  rewrite edit shorthand arguments when applicable
  parse CLI arguments
  build command handlers

  if command succeeds:
    return success status

  if user interrupts:
    emit interrupt notice
    return interrupt status

  if command raises known dotman error:
    emit formatted error
    return failure status

selection_prompt(options):
  if interactive mode is disabled:
    reject need for selection

  if fzf is enabled and available for options:
    return selected fzf option

  print numbered options
  parse user answer as one or more indexes/tokens

  if answer is invalid:
    prompt again

  return selected option(s)

resolve_*_text(engine, query):
  collect exact and fuzzy matches for requested domain object

  if query has exactly one match:
    return that match

  if query has partial match requiring confirmation:
    ask user before accepting

  if multiple matches remain:
    ask user to choose interactively

  if no match:
    reject query

selection/review plan items:
  include active probe targets as selectable install rows with a probe badge and no path payload
  include active probe targets in the review menu as probe rows so item numbers stay aligned with the later selection menu
  exclude inactive/noop probe targets from normal selection and review
  when user excludes an active probe target, remove that target and recompute hook eligibility
  for probe review rows, match the selection menu probe row without extra parenthetical metadata
  for probe review inspection, render the header detail as hint text `probe target: no files`, then show a probe summary instead of a file diff and fall back to related target hook command summaries when no custom review payload exists

review_plans_for_interactive_diffs(plans):
  build review items from file/directory plans plus active probe plans
  for each review item:
    show diff header and diff output
    ask inspect/next/all/list/skip-review/abort command
    if user requests list:
      reprint the review item menu without changing current review position
    apply command to pending plans
  return filtered or edited plans

run_execution(plans, args):
  if dry-run requested:
    emit payload
    return success

  if execution needs confirmation or hazard approval:
    ask required prompts unless assume-yes allows skipping

  execute plans
  emit execution result
  return execution exit code
```

## Review Needed

`cli.py` is compatibility-heavy. Exact legacy wrappers, prompt tokens, shorthand rewriting, and command error codes need implementation review before changes.
