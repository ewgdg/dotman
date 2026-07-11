# Plist structured transform

1. Read base operand as bytes; missing file means empty dictionary, and `-` means stdin.
2. Parse plist and reject non-dictionary roots.
3. Parse selectors: unprefixed/`exact:` dotted dictionary paths (quoted parts preserve literal dots); `re:` patterns search complete dotted paths. Arrays and scalars remain atomic.
4. With no selectors, preserve entire base. Otherwise retain or remove selected paths, including whole subtree when dictionary path matches.
5. For merge, recursively overlay second plist along selected nested ancestors; overlay values replace base values and omitted managed descendants stay deleted.
6. Compare plist values recursively with exact runtime types: dictionaries compare matching keys and typed child values, lists compare ordered typed elements, and scalar values require identical types before value equality. This keeps plist booleans distinct from integers. Reuse raw compare bytes only when this typed semantic comparison succeeds; otherwise serialize sorted keys using requested `xml` or `binary` format.
7. Emit bytes binary-safely to stdout or file. File output inherits base permissions when base is a file.
