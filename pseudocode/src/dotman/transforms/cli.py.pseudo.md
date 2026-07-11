# Structured transform CLI

- Register flat `dotman transform json BASE [OUTPUT]` parser with mode, overlay, selector action, selectors, stdout, and format options.
- Build public selector help from format metadata, naming unprefixed default and each supported prefix.
- Parse selector prefixes; unprefixed selectors use format default.
- Preserve stdout precedence when positional output is also supplied.
- Treat `-` as stdin for base or overlay and stdout for output; reject more than one stdin input before reading.
- Build validated request and run format engine without repository discovery or DotmanEngine construction.
