from __future__ import annotations

import subprocess
import sys

import pytest


# Every command pays for what the CLI imports before it starts work, and sync
# shows nothing until its progress bar starts. These are needed only for an
# interactive prompt or `--version`.
@pytest.mark.parametrize("module", ["prompt_toolkit", "importlib.metadata"])
def test_cli_import_defers_modules_only_some_commands_need(module: str) -> None:
    completed = subprocess.run(
        [sys.executable, "-c", f"import sys, dotman.cli; print({module!r} in sys.modules)"],
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )

    assert completed.stdout.strip() == "False"


# Repo manifests call these helpers from every render, capture, and probe, so
# dotman's startup cost is paid once per call. They must not load the sync
# engine that only repo-aware commands use.
@pytest.mark.parametrize(
    ("argv", "stdin"),
    [
        (["transform", "json", "-", "--stdout", "--mode", "cleanup"], "{}"),
        (["rewrite", "home", "expand", "-"], "~/x\n"),
        (["render", "jinja", "{template}"], ""),
    ],
)
def test_standalone_helpers_skip_repo_engine_imports(tmp_path, argv: list[str], stdin: str) -> None:
    template = tmp_path / "template.j2"
    template.write_text("{{ profile }}\n")
    argv = [arg.format(template=template) for arg in argv]
    script = (
        "import sys\n"
        "from dotman.cli import main\n"
        f"exit_code = main({argv!r})\n"
        "loaded = [name for name in ('dotman.engine', 'dotman.models', 'dotman.cli_emit') if name in sys.modules]\n"
        "print(exit_code, loaded, file=sys.stderr)\n"
    )
    completed = subprocess.run(
        [sys.executable, "-c", script],
        input=stdin,
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )

    assert completed.stderr.strip().splitlines()[-1] == "0 []"
