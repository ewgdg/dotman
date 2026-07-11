#!/usr/bin/env python3

from __future__ import annotations

import argparse
from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
import re
import sys
from typing import Any, ClassVar, Iterable, Protocol, runtime_checkable


class TransformMode(StrEnum):
    CLEANUP = "cleanup"
    MERGE = "merge"


class SelectorAction(StrEnum):
    REMOVE = "remove"
    RETAIN = "retain"


def compile_selector_regexes(
    raw_regexes: Iterable[str],
    selector_description: str,
) -> tuple[re.Pattern[str], ...]:
    compiled_regexes: list[re.Pattern[str]] = []
    for raw_regex in raw_regexes:
        try:
            compiled_regexes.append(re.compile(raw_regex))
        except re.error as error:
            raise ValueError(
                f"invalid {selector_description} regex {raw_regex!r}: {error}"
            ) from error
    return tuple(compiled_regexes)


@dataclass(frozen=True)
class SelectorSpec:
    name: str
    description: str
    prefix: str
    is_default: bool = False
    examples: tuple[str, ...] = ()
    supported_modes: frozenset[TransformMode] = field(
        default_factory=lambda: frozenset({TransformMode.CLEANUP, TransformMode.MERGE})
    )

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("selector spec name must not be empty")
        if not self.prefix:
            raise ValueError("selector spec prefix must not be empty")
        if not self.description:
            raise ValueError("selector spec description must not be empty")
        if not self.supported_modes:
            raise ValueError("selector spec supported_modes must not be empty")


@dataclass(frozen=True)
class TransformRequest:
    base_path: Path
    output_path: Path | None
    mode: TransformMode
    selector_action: SelectorAction
    selectors_by_type: Mapping[str, tuple[str, ...]]
    overlay_path: Path | None = None
    engine_options: Mapping[str, Any] = field(default_factory=dict)

    def validate_basic(self) -> None:
        if self.mode == TransformMode.MERGE and self.overlay_path is None:
            raise ValueError("overlay_path is required when mode=merge")
        if self.mode == TransformMode.CLEANUP and self.overlay_path is not None:
            raise ValueError("overlay_path is only valid when mode=merge")
        if self.output_path is None and not self.engine_option("stdout", False):
            raise ValueError("output_path is required unless stdout output is enabled")

    def selector_values(self, selector_type: str) -> tuple[str, ...]:
        return self.selectors_by_type.get(selector_type, ())

    def engine_option(self, option_name: str, default: Any = None) -> Any:
        return self.engine_options.get(option_name, default)


@dataclass(frozen=True)
class TransformOutput:
    content: str | bytes
    mode_reference_path: Path | None
    reused_compare_path: Path | None = None

    @property
    def is_binary(self) -> bool:
        return isinstance(self.content, bytes)

    def as_text(self, encoding: str = "utf-8") -> str:
        if isinstance(self.content, str):
            return self.content
        return self.content.decode(encoding, errors="surrogateescape")


def sync_output_mode(reference_path: Path | None, output_path: Path) -> None:
    if reference_path is None or not reference_path.exists() or not output_path.exists():
        return

    target_mode = reference_path.stat().st_mode & 0o777
    current_mode = output_path.stat().st_mode & 0o777
    if current_mode != target_mode:
        output_path.chmod(target_mode)



def write_output_to_stdout(output: TransformOutput) -> None:
    if isinstance(output.content, str):
        sys.stdout.write(output.content)
        return

    stdout_buffer = getattr(sys.stdout, "buffer", None)
    if stdout_buffer is not None:
        stdout_buffer.write(output.content)
        return

    # Non-interactive runners may replace stdout with a text-only stream like
    # io.StringIO. Decode with surrogateescape so byte-preserving compare reuse
    # still works when stdout is captured as text.
    stdout_encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
    sys.stdout.write(output.content.decode(stdout_encoding, errors="surrogateescape"))



def write_output_to_path(output_path: Path, output: TransformOutput) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Reusing the existing compare file is intentional. If that same path is also
    # the destination, skip the rewrite to preserve metadata like mtime.
    if output.reused_compare_path is not None and output.reused_compare_path == output_path:
        sync_output_mode(output.mode_reference_path, output_path)
        return

    if isinstance(output.content, bytes):
        output_path.write_bytes(output.content)
    else:
        output_path.write_text(output.content, encoding="utf-8")
    sync_output_mode(output.mode_reference_path, output_path)



def emit_transform_output(
    output_path: Path | None,
    output: TransformOutput,
    *,
    stdout: bool = False,
) -> None:
    if stdout:
        write_output_to_stdout(output)
        return

    assert output_path is not None
    write_output_to_path(output_path, output)


@runtime_checkable
class TransformEngine(Protocol):
    name: str

    @classmethod
    def selector_specs(cls) -> tuple[SelectorSpec, ...]:
        ...

    def requires_selectors(self) -> bool:
        ...

    def configure_parser(self, parser: argparse.ArgumentParser) -> None:
        ...

    def build_engine_options(
        self,
        parsed_args: argparse.Namespace,
    ) -> Mapping[str, Any]:
        ...

    def validate_request(self, request: TransformRequest) -> None:
        ...

    def transform(self, request: TransformRequest) -> TransformOutput:
        ...


class BaseTransformEngine(ABC):
    name: ClassVar[str]
    SELECTOR_SPECS: ClassVar[tuple[SelectorSpec, ...]]

    @classmethod
    def selector_specs(cls) -> tuple[SelectorSpec, ...]:
        return cls.SELECTOR_SPECS

    @classmethod
    def selector_spec_map(cls) -> dict[str, SelectorSpec]:
        return {spec.name: spec for spec in cls.selector_specs()}

    def requires_selectors(self) -> bool:
        return True

    def configure_parser(self, parser: argparse.ArgumentParser) -> None:
        del parser

    def build_engine_options(
        self,
        parsed_args: argparse.Namespace,
    ) -> Mapping[str, Any]:
        del parsed_args
        return {}

    def validate_request(self, request: TransformRequest) -> None:
        request.validate_basic()
        if self.requires_selectors() and not any(request.selectors_by_type.values()):
            raise ValueError("at least one selector value is required")

        supported_specs = self.selector_spec_map()
        unknown_selector_types = sorted(
            selector_type
            for selector_type in request.selectors_by_type
            if selector_type not in supported_specs
        )
        if unknown_selector_types:
            raise ValueError(
                f"{self.name} does not support selector types: {', '.join(unknown_selector_types)}"
            )

        unsupported_mode_selector_types = sorted(
            selector_type
            for selector_type in request.selectors_by_type
            if selector_type in supported_specs
            and request.selector_values(selector_type)
            and request.mode not in supported_specs[selector_type].supported_modes
        )
        if unsupported_mode_selector_types:
            raise ValueError(
                f"{self.name} selector types not supported in {request.mode.value} mode: "
                f"{', '.join(unsupported_mode_selector_types)}"
            )

    @abstractmethod
    def transform(self, request: TransformRequest) -> TransformOutput:
        raise NotImplementedError
