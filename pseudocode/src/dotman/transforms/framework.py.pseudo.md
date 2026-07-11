# Transform framework

- Model cleanup/merge modes, retain/remove actions, selector specifications, requests, and outputs.
- Validate merge overlay, cleanup exclusion, output destination, selector support, and required selectors.
- Compile selector regexes with contextual `ValueError` messages.
- Emit text or bytes to stdout or path; when semantic compare matches, reuse bytes and avoid same-path rewrite.
- Synchronize output permissions from file base when available; skip synchronization for stdin base.
