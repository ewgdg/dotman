## Project structure

- Core package code lives in `src/dotman/`.
- CLI and engine coverage lives in `tests/`.
- Example repo layouts and sample config live in `examples/repo/`.

## UI design

- Do not forget to update style whenever add or modify a user-facing command/UI
- Stay consistent with the existing design.
- Use `repo:package.target` as the main user-facing target identifier form.
- Use `repo:package<instance>.target` for package-instance targets.
- Reserve `.` as package/target separator. Do not allow `.` inside package IDs or target names.
- Use parentheses only for optional annotations such as hook summaries, not target identity.
- Do not reveal the actual tracked root packages except in info command output.
- Reuse the same rendering for keyword terms when possible to keep the color schema consistent.
