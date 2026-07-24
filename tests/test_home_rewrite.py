from __future__ import annotations

import pytest

from dotman.rewrites.home import active_home_path, collapse_home_paths, expand_home_paths


def test_expand_home_paths_rewrites_standalone_home_fragments() -> None:
    assert expand_home_paths("~ ~/projects", home="/home/alice") == "/home/alice /home/alice/projects"


def test_collapse_home_paths_rewrites_exact_home_and_descendants() -> None:
    assert (
        collapse_home_paths("/home/alice /home/alice/projects", home="/home/alice")
        == "~ ~/projects"
    )


def test_active_home_path_removes_redundant_trailing_slashes() -> None:
    assert active_home_path({"HOME": "/home/alice///"}) == "/home/alice"


@pytest.mark.parametrize("home", [None, "", "alice", "/", "///"])
def test_active_home_path_rejects_invalid_home_values(home: str | None) -> None:
    environment = {} if home is None else {"HOME": home}

    with pytest.raises(ValueError, match=r"\$HOME must be a non-root absolute POSIX path"):
        active_home_path(environment)


@pytest.mark.parametrize(
    "text",
    [
        "word~",
        "é~",
        ".~",
        "~~",
        "+~",
        "-~",
        "/~",
        r"\~",
        "~user",
        "~é",
        "~\N{COMBINING ACUTE ACCENT}",
        "~.",
        "~+",
        "~-",
        r"~\share",
        "https://~/project",
        r"\~/project",
    ],
)
def test_expand_home_paths_protects_attached_lookalikes(text: str) -> None:
    assert expand_home_paths(text, home="/home/alice") == text


@pytest.mark.parametrize(
    "text",
    [
        "word/home/alice",
        "é/home/alice",
        "./home/alice",
        "~/home/alice",
        "+/home/alice",
        "-/home/alice",
        "//home/alice",
        r"\/home/alice",
        "https://host/home/alice",
        "/home/alice2",
        "/home/aliceé",
        "/home/alice\N{COMBINING ACUTE ACCENT}",
        "/home/alice.",
        "/home/alice~",
        "/home/alice+",
        "/home/alice-",
        r"/home/alice\share",
    ],
)
def test_collapse_home_paths_protects_attached_lookalikes(text: str) -> None:
    assert collapse_home_paths(text, home="/home/alice") == text


def test_home_rewrites_leave_environment_variable_text_unchanged() -> None:
    text = "$HOME ${HOME}"

    assert expand_home_paths(text, home="/home/alice") == text
    assert collapse_home_paths(text, home="/home/alice") == text
