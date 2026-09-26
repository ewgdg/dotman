"""Throwaway Sync demo scene for trying the Command Deck by hand.

    uv run python -m tests.demo_scene [--dir PATH] [--no-open]

Each run rebuilds the scene from scratch, then opens `dotman sync` inside it.
HOME and the XDG directories point into the scene, so real dotfiles are never touched.
`tests/cli/test_demo_scene.py` keeps every case showing what it advertises.
"""

from __future__ import annotations

import argparse
import shlex
import shutil
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from dotman.engine import DotmanEngine
from tests.helpers import write_sync_repository

SCENE_MARKER = ".dotman-demo-scene"
MERGE_BASE = b"first\nmiddle\nlast\n"


@dataclass(frozen=True)
class DemoCase:
    name: str
    policy: str
    repository: bytes | None
    live: bytes | None
    shows: str
    """Text the case's Proposal Review must contain; this is what the case demonstrates."""
    extra: str = ""
    base: bytes | None = None
    """When set, both sides start at these bytes to establish a Sync Base, then diverge."""


CASES = (
    DemoCase("push-edit", "push-only", b"theme = dark\nfont = 14\n", b"theme = light\nfont = 14\n",
             shows="+theme = dark"),
    DemoCase("pull-capture", "pull-only", b"alias ll='ls -l'\n", b"alias ll='ls -la'\nalias gs='git status'\n",
             shows="+alias gs='git status'"),
    # Capture drops the volatile line, so nothing is written, and Render cannot reproduce
    # live, so no Sync Base can be recorded either: a [-] "No-op" row.
    DemoCase("pull-noop", "pull-only", b"window = 1200x800\n", b"window = 1200x800\nopened-at = 2026-09-25T10:00\n",
             extra="""capture = '''grep -v '^opened-at' "$DOTMAN_LIVE_PATH"'''\ncompare = { repo = "raw", live = "raw" }""",
             shows="Nothing to do: Approval would neither write nor record a Sync Base"),
    # Capture and Render fold case, so nothing is written, but Approval records the Sync Base.
    DemoCase("pull-records-base", "pull-only", b"editor = vim\n", b"EDITOR = VIM\n",
             extra='render = "tr a-z A-Z < $DOTMAN_SOURCE"\ncapture = "tr A-Z a-z < $DOTMAN_LIVE_PATH"\n'
                   'compare = { repo = "raw", live = "raw" }',
             shows="Nothing will be written; Approval records the Sync Base"),
    DemoCase("live-missing-empty", "push-only", b"", None, shows="new file"),
    DemoCase("repo-deleted", "push-only-delete", None, b"stale\n", shows="deleted file"),
    DemoCase("merge-clean", "both", b"repository\nmiddle\nlast\n", b"first\nmiddle\nlive\n",
             base=MERGE_BASE, shows="+live"),
    DemoCase("merge-conflict", "both", b"repository\nmiddle\nlast\n", b"conflict\nmiddle\nlast\n",
             base=MERGE_BASE, shows=":: Merge conflicts"),
)


def default_scene_root() -> Path:
    # gettempdir follows TMPDIR (per-user on macOS); resolve past macOS's /var -> /private/var
    # symlink so live paths do not trip dotman's symlink checks.
    return Path(tempfile.gettempdir()).resolve() / "dotman-demo"


def scene_environment(root: Path) -> dict[str, str]:
    """HOME is the scene root so paths render as `~/repo/...` and `~/live/...`."""
    return {
        "HOME": str(root),
        "XDG_CONFIG_HOME": str(root / "xdg-config"),
        "XDG_STATE_HOME": str(root / "state"),
        "XDG_DATA_HOME": str(root / "xdg-data"),
    }


def build_scene(root: Path) -> Path:
    """Write every case into `root` and return the config path; the scene environment must be active."""
    import os

    mismatched = {name for name, value in scene_environment(root).items() if os.environ.get(name) != value}
    if mismatched:
        raise RuntimeError(f"Scene environment is not active: {', '.join(sorted(mismatched))}")
    root.mkdir(parents=True)
    (root / SCENE_MARKER).touch()
    config = write_sync_repository(root, [
        (case.name, case.policy,
         case.base if case.base is not None else case.repository,
         case.base if case.base is not None else case.live,
         case.extra)
        for case in CASES
    ])
    # Opening a non-preview session records a Sync Base for units whose sides agree.
    engine = DotmanEngine.from_config_path(config)
    with engine.open_sync_session(engine.resolve_sync_scope([]), preview=False):
        pass
    for case in (case for case in CASES if case.base is not None):
        (root / "repo/packages/app" / case.name).write_bytes(case.repository)
        (root / "live" / case.name).write_bytes(case.live)
    return config


def reset_scene_root(root: Path) -> None:
    """Only remove a directory this script created, never an arbitrary --dir."""
    if not root.exists():
        return
    if not (root / SCENE_MARKER).exists():
        raise SystemExit(f"Refusing to replace {root}: it is not a dotman demo scene")
    shutil.rmtree(root)


def main(argv: list[str] | None = None) -> int:
    import os

    parser = argparse.ArgumentParser(description="Rebuild the Sync demo scene and open dotman sync in it")
    parser.add_argument("--dir", type=Path, default=default_scene_root(), help="scene directory")
    parser.add_argument("--no-open", action="store_true", help="build the scene without opening dotman")
    args = parser.parse_args(argv)
    root = args.dir.expanduser().resolve()
    reset_scene_root(root)
    environment = scene_environment(root)
    os.environ.update(environment)
    config = build_scene(root)
    rerun = " ".join([*(f"{name}={shlex.quote(value)}" for name, value in environment.items()),
                      "uv run dotman --config", shlex.quote(str(config)), "sync"])
    print(f"Demo scene: {root}\nReopen without rebuilding:\n  {rerun}", file=sys.stderr)
    if args.no_open:
        return 0
    from dotman.cli import main as dotman_main

    return dotman_main(["--config", str(config), "sync"])


if __name__ == "__main__":
    raise SystemExit(main())
