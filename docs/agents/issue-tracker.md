# Issue tracker: GitHub

Issues and PRDs for this repo live as GitHub issues. Use the `gh` CLI for all operations.

## Conventions

- Create: `gh issue create --title "..." --body-file <path>`
- Read: `gh issue view <number> --comments`
- List: `gh issue list` with suitable state and label filters
- Comment: `gh issue comment <number> --body "..."`
- Apply or remove labels: `gh issue edit <number> --add-label "..."` or `--remove-label "..."`
- Close: `gh issue close <number> --comment "..."`

Infer the repository from the current Git remote.

## Pull requests as a triage surface

External pull requests are not treated as request intake. Triage skills process GitHub issues only; pull requests keep their normal review workflow.

## Skill terminology

When a skill says to publish to the issue tracker, create a GitHub issue. When a skill says to fetch the relevant ticket, read the GitHub issue and its comments.
