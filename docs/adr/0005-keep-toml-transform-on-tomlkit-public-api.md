# Keep the TOML transform on tomlkit's public API

The TOML transform stays on tomlkit, using only its public API and no version pin. That removes the private-internals risk that motivated a move to tomlrt, without tomlrt's costs.

tomlrt was evaluated (2.2.14, 2026-09-29) and rejected:

- Every public route re-spells a scalar copied between or within documents (`0x10` becomes `16`, `1_000` becomes `1000`, literal strings become basic strings).
- Its native edits do not follow the comment ownership rule in `docs/cli.md`: an entry's whole leading comment block travels and is deleted with it, including the previous table's trailing comments and blank-separated independent comments. Enforcing the rule needs textual slot order, which tomlrt does not expose, so dotman would still own an ownership layer plus a TOML-aware text scanner.

Revisit if tomlrt gains a public API that copies a key slot with its formatting and exposes textual slot order.
