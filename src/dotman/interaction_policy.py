from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from collections.abc import Iterator

_unattended = ContextVar("unattended", default=False)


def unattended_enabled() -> bool:
    return _unattended.get()


@contextmanager
def interaction_scope(*, unattended: bool) -> Iterator[None]:
    token = _unattended.set(unattended)
    try:
        yield
    finally:
        _unattended.reset(token)
