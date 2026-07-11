# XML structured transform

1. Read base XML from file or stdin. Require one or more selectors. Parse unprefixed/`exact:` selectors as comma-expandable, `fnmatch`-style, root-inclusive element paths; compile `re:` selectors and search root-inclusive paths.
2. For `retain`, copy each matched subtree plus its ancestor chain. For `remove`, copy tree then delete matched subtrees. Root matches clear root when removed.
3. For merge, overlay second tree into filtered base using original base sibling slots. Match repeated siblings by tag plus available `id`, `name`, `key`, `uuid`, and non-empty text identity; otherwise match next same-tag sibling. Overlay attributes/text replace preserved values. Managed omissions remain deleted.
4. If requested, alphabetically sort attributes for emitted output. Expand repeated or comma-separated `--sort-children` paths and canonically sort only immediate children of matching parents.
5. For semantic comparison only, copy each tree, discard whitespace-only text/tails, sort attributes, and sort children only beneath selected sort parents. If equal, reuse compare file's exact raw bytes.
6. Otherwise pretty-print transformed tree. Do not apply comparison normalization or unconditional canonical serialization to emitted output.
7. Emit to file or stdout (`-` supported for one input and output). File output inherits base-file permissions.
