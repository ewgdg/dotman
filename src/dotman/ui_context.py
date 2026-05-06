from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar, Token
from typing import Iterator

from dotman.models import UiConfig


_ui_config: ContextVar[UiConfig | None] = ContextVar(
    "ui_config",
    default=None,
)


@contextmanager
def ui_config_scope(ui_config: UiConfig | None) -> Iterator[None]:
    token: Token[UiConfig | None] = _ui_config.set(ui_config)
    try:
        yield
    finally:
        _ui_config.reset(token)


def current_ui_config() -> UiConfig | None:
    return _ui_config.get()
