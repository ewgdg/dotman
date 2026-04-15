# AGENTS.md

## Project structure

- Core package code lives in `src/dotman/`.
- CLI and engine coverage lives in `tests/`.
- Example repo layouts and sample config live in `examples/repo/`.
- Historical `dotdrop` reference code lives in `../dotman.archived/`.
- Keep repo-orientation notes here.
- Put design and feature documentation under `docs/`.
- Put implementation plans under `plans/`.

## UI design

- Stay consistent with the existing design.
- Use `repo:package (target)` as the main user-facing identifier form. Do not reveal the actual tracked root binding except in info command output.
- Reuse the same rendering for keyword terms when possible to keep the color schema consistent.
