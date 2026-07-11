# TOML structured transform

1. Read base TOML from file or stdin with `tomlkit`; require one or more selectors. Parse unprefixed/`exact:` selectors as exact dotted key paths and compile `re:` selectors for Python regex search over dotted table and key paths.
2. Preserve `tomlkit` document items and trivia. Traverse ordinary tables and every table in each array of tables. Within each table tail, split at the blank separator before an independent comment block: comments before that separator stay attached to the table or array, while the blank-separated block is detached after the owning item and survives cleanup deletion or merge replacement/deletion.
3. For cleanup `retain`, rebuild only exact and regex-selected items plus required ancestor tables. For cleanup `remove`, delete matches deepest-first. Normalize runs of blank lines without fresh semantic serialization.
4. For merge, partition base using selector action, then recursively overlay TOML tables into original base slots. Overlay scalar values and arrays, including arrays of tables, as atomic TOML values. Missing managed items remain deleted; selected base items survive.
5. Restore leading/trailing comment regions, original ordering, and one section separator between adjacent tables.
6. Read `--compare-file` as bytes. If decoded TOML parses to the same unwrapped value, reuse those exact bytes, including CRLF, for file or stdout output. Otherwise emit preserved `tomlkit` document text.
7. Emit to file or stdout (`-` supported for one input and output). File output inherits base-file permissions.
