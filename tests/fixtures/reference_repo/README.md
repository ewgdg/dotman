# Reference Test Repo

Minimal in-repo reference repository for engine tests.

All fixture content must stay synthetic and sanitized:

- no personal usernames
- no real email addresses
- no machine-specific absolute paths
- no copied secrets, tokens, or host identifiers

It exists only for tests and covers:

- profile include composition
- host group wrappers around host meta packages
- host meta package dependency expansion
- namespaced packages with `extends`
- templated target source paths
- nested directory and file targets with ignore rules
