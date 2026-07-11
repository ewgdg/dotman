# Terminal Prompt Utilities

## Intent

Read interactive input without leaving terminal state corrupted.

## Behavior

```pseudo
preserve_terminal_state():
  capture terminal state for available TTY streams
  run caller body
  restore captured terminal states best-effort

read_prompt_line(message, optional escape result):
  if prompt-toolkit is supported in current terminal:
    if escape result is configured:
      bind Escape to immediately finish input with that result
    read line using prompt-toolkit
  else:
    print prompt and read from standard input
  return user input line

_prompt_toolkit_supported():
  if required streams are not usable TTYs:
    return false
  if prompt-toolkit cannot run in environment:
    return false
  return true
```
