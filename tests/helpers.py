from __future__ import annotations

from pathlib import Path

import dotman.cli as cli
import pytest


def write_example_local_override(tmp_path: Path, *, repo_name: str, repo_path: Path) -> None:
    example_local_path = repo_path / "local.example.toml"
    if not example_local_path.exists():
        return
    local_path = tmp_path / "xdg-config" / "dotman" / "repos" / repo_name / "local.toml"
    local_path.parent.mkdir(parents=True, exist_ok=True)
    local_path.write_text(example_local_path.read_text(encoding="utf-8"), encoding="utf-8")


# Shared repo builders live here so split test modules reuse identical setup code.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
EXAMPLE_REPO = PROJECT_ROOT / "examples" / "repo"
REFERENCE_REPO = PROJECT_ROOT / "tests" / "fixtures" / "reference_repo"


def capture_parser_help(capsys: pytest.CaptureFixture[str], *args: str) -> str:
    parser = cli.build_parser()

    with pytest.raises(SystemExit) as exc_info:
        parser.parse_args([*args, "--help"])

    assert exc_info.value.code == 0
    return capsys.readouterr().out


def write_named_manager_config(tmp_path: Path, repos: dict[str, Path]) -> Path:
    config_path = tmp_path / "config.toml"
    lines: list[str] = []
    for index, (repo_name, repo_path) in enumerate(repos.items(), start=1):
        lines.extend(
            [
                f"[repos.{repo_name}]",
                f'path = "{repo_path}"',
                f"order = {index * 10}",
                "",
            ]
        )
        write_example_local_override(tmp_path, repo_name=repo_name, repo_path=repo_path)
    config_path.write_text("\n".join(lines), encoding="utf-8")
    return config_path


def write_manager_config(tmp_path: Path) -> Path:
    return write_named_manager_config(
        tmp_path,
        {
            "example": EXAMPLE_REPO,
            "sandbox": REFERENCE_REPO,
        },
    )


def write_named_manager_config_with_state_keys(
    tmp_path: Path,
    repos: dict[str, Path],
    *,
    state_keys: dict[str, str] | None = None,
) -> Path:
    config_path = tmp_path / "config.toml"
    lines: list[str] = []
    for index, (repo_name, repo_path) in enumerate(repos.items(), start=1):
        lines.extend(
            [
                f"[repos.{repo_name}]",
                f'path = "{repo_path}"',
                f"order = {index * 10}",
                (
                    f'state_key = "{state_keys[repo_name]}"'
                    if state_keys is not None and repo_name in state_keys
                    else ""
                ),
                "",
            ]
        )
        write_example_local_override(tmp_path, repo_name=repo_name, repo_path=repo_path)
    config_path.write_text("\n".join(lines), encoding="utf-8")
    return config_path


def write_single_repo_config(tmp_path: Path, *, repo_name: str, repo_path: Path) -> Path:
    return write_named_manager_config(tmp_path, {repo_name: repo_path})


def write_single_repo_config_with_state_key(
    tmp_path: Path,
    *,
    repo_name: str,
    repo_path: Path,
    state_key: str | None = None,
) -> Path:
    return write_named_manager_config_with_state_keys(
        tmp_path,
        {repo_name: repo_path},
        state_keys=None if state_key is None else {repo_name: state_key},
    )


def write_profile_switch_repo(repo_root: Path) -> None:
    (repo_root / "profiles").mkdir(parents=True)
    (repo_root / "packages" / "alpha" / "files").mkdir(parents=True)
    (repo_root / "packages" / "beta" / "files").mkdir(parents=True)
    (repo_root / "profiles" / "basic.toml").write_text("", encoding="utf-8")
    (repo_root / "profiles" / "work.toml").write_text("", encoding="utf-8")
    (repo_root / "packages" / "alpha" / "files" / "alpha.conf").write_text("alpha\n", encoding="utf-8")
    (repo_root / "packages" / "beta" / "files" / "beta.conf").write_text("beta\n", encoding="utf-8")
    (repo_root / "packages" / "alpha" / "package.toml").write_text(
        "\n".join(
            [
                'id = "alpha"',
                "",
                "[targets.alpha]",
                'source = "files/alpha.conf"',
                'path = "~/.config/{{ profile }}.conf"',
                "",
            ]
        ),
        encoding="utf-8",
    )
    (repo_root / "packages" / "beta" / "package.toml").write_text(
        "\n".join(
            [
                'id = "beta"',
                "",
                "[targets.beta]",
                'source = "files/beta.conf"',
                'path = "~/.config/basic.conf"',
                "",
            ]
        ),
        encoding="utf-8",
    )


def write_implicit_conflict_repo(repo_root: Path) -> None:
    (repo_root / "profiles").mkdir(parents=True)
    (repo_root / "groups").mkdir(parents=True)
    (repo_root / "packages" / "alpha" / "files").mkdir(parents=True)
    (repo_root / "packages" / "beta" / "files").mkdir(parents=True)
    (repo_root / "packages" / "alpha-meta").mkdir(parents=True)
    (repo_root / "packages" / "beta-meta").mkdir(parents=True)
    (repo_root / "profiles" / "basic.toml").write_text("", encoding="utf-8")
    (repo_root / "groups" / "alpha-stack.toml").write_text('members = ["alpha-meta"]\n', encoding="utf-8")
    (repo_root / "groups" / "beta-stack.toml").write_text('members = ["beta-meta"]\n', encoding="utf-8")
    (repo_root / "packages" / "alpha" / "files" / "shared.conf").write_text("alpha\n", encoding="utf-8")
    (repo_root / "packages" / "beta" / "files" / "shared.conf").write_text("beta\n", encoding="utf-8")
    (repo_root / "packages" / "alpha" / "package.toml").write_text(
        "\n".join(
            [
                'id = "alpha"',
                "",
                "[targets.shared]",
                'source = "files/shared.conf"',
                'path = "~/.config/shared.conf"',
                "",
            ]
        ),
        encoding="utf-8",
    )
    (repo_root / "packages" / "beta" / "package.toml").write_text(
        "\n".join(
            [
                'id = "beta"',
                "",
                "[targets.shared]",
                'source = "files/shared.conf"',
                'path = "~/.config/shared.conf"',
                "",
            ]
        ),
        encoding="utf-8",
    )
    (repo_root / "packages" / "alpha-meta" / "package.toml").write_text(
        "\n".join(
            [
                'id = "alpha-meta"',
                'depends = ["alpha"]',
                "",
            ]
        ),
        encoding="utf-8",
    )
    (repo_root / "packages" / "beta-meta" / "package.toml").write_text(
        "\n".join(
            [
                'id = "beta-meta"',
                'depends = ["beta"]',
                "",
            ]
        ),
        encoding="utf-8",
    )


def write_untrack_conflict_repo(repo_root: Path) -> None:
    (repo_root / "profiles").mkdir(parents=True)
    (repo_root / "packages" / "shared" / "files").mkdir(parents=True)
    (repo_root / "packages" / "stack-a").mkdir(parents=True)
    (repo_root / "packages" / "stack-b").mkdir(parents=True)

    for profile_name in ("direct", "work", "personal"):
        (repo_root / "profiles" / f"{profile_name}.toml").write_text("", encoding="utf-8")

    (repo_root / "packages" / "shared" / "files" / "shared.conf").write_text(
        "profile={{ profile }}\n",
        encoding="utf-8",
    )
    (repo_root / "packages" / "shared" / "package.toml").write_text(
        "\n".join(
            [
                'id = "shared"',
                "",
                "[targets.shared]",
                'source = "files/shared.conf"',
                'path = "~/.config/shared.conf"',
                'render = "jinja"',
                "",
            ]
        ),
        encoding="utf-8",
    )
    (repo_root / "packages" / "stack-a" / "package.toml").write_text(
        "\n".join(
            [
                'id = "stack-a"',
                'depends = ["shared"]',
                "",
            ]
        ),
        encoding="utf-8",
    )
    (repo_root / "packages" / "stack-b" / "package.toml").write_text(
        "\n".join(
            [
                'id = "stack-b"',
                'depends = ["shared"]',
                "",
            ]
        ),
        encoding="utf-8",
    )


def write_multi_instance_repo(repo_root: Path) -> None:
    (repo_root / "profiles").mkdir(parents=True)
    (repo_root / "packages" / "profiled" / "files").mkdir(parents=True)
    (repo_root / "profiles" / "basic.toml").write_text("", encoding="utf-8")
    (repo_root / "profiles" / "work.toml").write_text("", encoding="utf-8")
    (repo_root / "packages" / "profiled" / "files" / "managed.conf").write_text(
        "profile={{ profile }}\n",
        encoding="utf-8",
    )
    (repo_root / "packages" / "profiled" / "package.toml").write_text(
        "\n".join(
            [
                'id = "profiled"',
                'binding_mode = "multi_instance"',
                "",
                "[targets.managed]",
                'source = "files/managed.conf"',
                'path = "~/.config/profiled/{{ profile }}.conf"',
                'render = "jinja"',
                "",
            ]
        ),
        encoding="utf-8",
    )


def write_shared_stack_repo(repo_root: Path) -> None:
    (repo_root / "profiles").mkdir(parents=True)
    (repo_root / "packages" / "shared" / "files").mkdir(parents=True)
    (repo_root / "packages" / "shared-stack").mkdir(parents=True)
    (repo_root / "profiles" / "basic.toml").write_text("", encoding="utf-8")
    (repo_root / "packages" / "shared" / "files" / "shared.conf").write_text(
        "shared\n",
        encoding="utf-8",
    )
    (repo_root / "packages" / "shared" / "package.toml").write_text(
        "\n".join(
            [
                'id = "shared"',
                "",
                "[targets.shared]",
                'source = "files/shared.conf"',
                'path = "~/.config/shared.conf"',
                "",
            ]
        ),
        encoding="utf-8",
    )
    (repo_root / "packages" / "shared-stack" / "package.toml").write_text(
        "\n".join(
            [
                'id = "shared-stack"',
                'depends = ["shared"]',
                "",
            ]
        ),
        encoding="utf-8",
    )


def write_package_override_preview_repo(repo_root: Path) -> None:
    (repo_root / "profiles").mkdir(parents=True)
    (repo_root / "groups").mkdir(parents=True)
    (repo_root / "packages" / "alpha" / "files").mkdir(parents=True)
    (repo_root / "packages" / "beta" / "files").mkdir(parents=True)
    (repo_root / "packages" / "alpha-meta").mkdir(parents=True)
    (repo_root / "packages" / "beta-meta").mkdir(parents=True)

    (repo_root / "profiles" / "basic.toml").write_text("", encoding="utf-8")
    (repo_root / "groups" / "alpha-stack.toml").write_text('members = ["alpha-meta"]\n', encoding="utf-8")
    (repo_root / "groups" / "beta-stack.toml").write_text('members = ["beta-meta"]\n', encoding="utf-8")

    (repo_root / "packages" / "alpha" / "files" / "shared.conf").write_text("alpha shared\n", encoding="utf-8")
    (repo_root / "packages" / "alpha" / "files" / "extra.conf").write_text("alpha extra\n", encoding="utf-8")
    (repo_root / "packages" / "beta" / "files" / "shared.conf").write_text("beta shared\n", encoding="utf-8")
    (repo_root / "packages" / "beta" / "files" / "extra.conf").write_text("beta extra\n", encoding="utf-8")

    (repo_root / "packages" / "alpha" / "package.toml").write_text(
        "\n".join(
            [
                'id = "alpha"',
                "",
                "[targets.shared]",
                'source = "files/shared.conf"',
                'path = "~/.config/shared.conf"',
                "",
                "[targets.extra]",
                'source = "files/extra.conf"',
                'path = "~/.config/extra.conf"',
                "",
            ]
        ),
        encoding="utf-8",
    )
    (repo_root / "packages" / "beta" / "package.toml").write_text(
        "\n".join(
            [
                'id = "beta"',
                "",
                "[targets.shared]",
                'source = "files/shared.conf"',
                'path = "~/.config/shared.conf"',
                "",
                "[targets.extra]",
                'source = "files/extra.conf"',
                'path = "~/.config/extra.conf"',
                "",
            ]
        ),
        encoding="utf-8",
    )
    (repo_root / "packages" / "alpha-meta" / "package.toml").write_text(
        "\n".join(
            [
                'id = "alpha-meta"',
                'depends = ["alpha"]',
                "",
            ]
        ),
        encoding="utf-8",
    )
    (repo_root / "packages" / "beta-meta" / "package.toml").write_text(
        "\n".join(
            [
                'id = "beta-meta"',
                'depends = ["beta"]',
                "",
            ]
        ),
        encoding="utf-8",
    )
