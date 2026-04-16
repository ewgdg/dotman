from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar, Token
from typing import Iterator

from dotman.models import SelectionMenuConfig


_selection_menu_config: ContextVar[SelectionMenuConfig | None] = ContextVar(
    "selection_menu_config",
    default=None,
)


@contextmanager
def selection_menu_config_scope(selection_menu_config: SelectionMenuConfig | None) -> Iterator[None]:
    token: Token[SelectionMenuConfig | None] = _selection_menu_config.set(selection_menu_config)
    try:
        yield
    finally:
        _selection_menu_config.reset(token)


def current_selection_menu_config() -> SelectionMenuConfig | None:
    return _selection_menu_config.get()
