from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping


_HOME_SHORTHAND = re.compile(r"~")
_WORD_CHARACTER = re.compile(r"\w")
# Treat path/name punctuation as attachment on the left to protect URLs and
# escapes. Slash stays open on the right so genuine child paths still rewrite.
_LEFT_ATTACHMENT_PUNCTUATION = frozenset(".~+-/\\")
_RIGHT_ATTACHMENT_PUNCTUATION = frozenset(".~+-\\")


def _is_word_or_mark(character: str) -> bool:
    return bool(_WORD_CHARACTER.fullmatch(character)) or unicodedata.category(character).startswith("M")


def _is_standalone_fragment(text: str, *, start: int, end: int) -> bool:
    left = text[start - 1] if start > 0 else ""
    right = text[end] if end < len(text) else ""
    left_attached = bool(left) and (
        _is_word_or_mark(left) or left in _LEFT_ATTACHMENT_PUNCTUATION
    )
    right_attached = bool(right) and (
        _is_word_or_mark(right) or right in _RIGHT_ATTACHMENT_PUNCTUATION
    )
    return not left_attached and not right_attached


def active_home_path(environment: Mapping[str, str]) -> str:
    home = environment.get("HOME", "")
    trimmed_home = home.rstrip("/")
    if not trimmed_home.startswith("/"):
        raise ValueError("$HOME must be a non-root absolute POSIX path")
    return trimmed_home


def expand_home_paths(text: str, *, home: str) -> str:
    """Expand standalone home shorthand fragments without parsing paths."""

    return _HOME_SHORTHAND.sub(
        lambda match: home
        if _is_standalone_fragment(text, start=match.start(), end=match.end())
        else match.group(),
        text,
    )


def collapse_home_paths(text: str, *, home: str) -> str:
    """Collapse standalone active-home fragments without parsing paths."""

    home_path = re.compile(re.escape(home))
    return home_path.sub(
        lambda match: "~"
        if _is_standalone_fragment(text, start=match.start(), end=match.end())
        else match.group(),
        text,
    )
