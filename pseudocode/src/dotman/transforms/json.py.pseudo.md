# JSON structured transform

- Load top-level JSON objects from files or the single captured stdin input.
- Parse exact dotted/quoted key paths and regex selectors against base paths.
- Cleanup retains or removes selected base regions; no selectors preserve identity.
- Merge filters base first, then overlays managed regions while retaining base order and nested identity.
- Serialize with detected indentation and Unicode; end with newline.
- Read compare files as raw bytes, decode only for semantic comparison, and emit semantically equal bytes unchanged (including line endings).
