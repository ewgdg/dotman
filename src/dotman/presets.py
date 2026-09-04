from __future__ import annotations

from typing import Any

from dotman.capture import BUILTIN_PATCH_CAPTURE


BUILTIN_TARGET_PRESETS: dict[str, dict[str, Any]] = {
    "jinja-editor": {
        "render": "jinja",
        "compare": {"repo": "render", "live": "raw"},
        "editor": {"type": "jinja"},
    },
    "jinja-patch": {
        "render": "jinja",
        "capture": BUILTIN_PATCH_CAPTURE,
        "compare": {"repo": "render", "live": "raw"},
    },
    "jinja-patch-editor": {
        "render": "jinja",
        "capture": BUILTIN_PATCH_CAPTURE,
        "compare": {"repo": "render", "live": "raw"},
        "editor": {"type": "jinja"},
    },
}


def get_builtin_target_preset(name: str) -> dict[str, Any] | None:
    preset = BUILTIN_TARGET_PRESETS.get(name)
    if preset is None:
        return None
    return dict(preset)


__all__ = ["BUILTIN_TARGET_PRESETS", "get_builtin_target_preset"]
