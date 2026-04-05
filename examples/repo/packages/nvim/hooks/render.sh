#!/bin/sh
set -eu

python - <<'PY'
from pathlib import Path
import os

source_path = Path(os.environ["DOTMAN_SOURCE"])
template = source_path.read_text()
template = template.replace("{{ vars.nvim.leader }}", os.environ["DOTMAN_VAR_nvim__leader"])
template = template.replace("{{ vars.nvim.colorscheme }}", os.environ["DOTMAN_VAR_nvim__colorscheme"])
print(template, end="")
PY
