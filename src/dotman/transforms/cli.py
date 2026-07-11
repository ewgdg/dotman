#!/usr/bin/env python3

from __future__ import annotations

import argparse
from pathlib import Path

from dotman.transforms.framework import (
    SelectorAction,
    TransformEngine,
    TransformMode,
    TransformRequest,
    emit_transform_output,
)


def parse_selectors(engine: TransformEngine, selectors: list[str]) -> dict[str, tuple[str, ...]]:
    selectors_by_type: dict[str, list[str]] = {spec.name: [] for spec in engine.selector_specs()}
    prefix_to_spec = {f"{spec.prefix}:": spec for spec in engine.selector_specs()}
    default_spec = next((spec for spec in engine.selector_specs() if spec.is_default), None)

    for selector in selectors:
        matched = False
        for prefix, spec in prefix_to_spec.items():
            if selector.startswith(prefix):
                selectors_by_type[spec.name].append(selector[len(prefix):])
                matched = True
                break
        if not matched:
            if default_spec is None:
                raise ValueError(
                    f"Engine {engine.name} has no default selector spec, but no prefix was matched for: {selector}"
                )
            selectors_by_type[default_spec.name].append(selector)

    return {name: tuple(values) for name, values in selectors_by_type.items()}



def selector_help(engine: TransformEngine) -> str:
    specs = engine.selector_specs()
    default_spec = next((spec for spec in specs if spec.is_default), None)
    prefix_descriptions = "; ".join(
        f"{spec.prefix}: {spec.description}" for spec in specs
    )
    default_description = (
        f"Unprefixed selectors use {default_spec.prefix}: ({default_spec.description}). "
        if default_spec is not None
        else ""
    )
    return f"Base-file selectors. {default_description}Prefixes: {prefix_descriptions}."


def configure_parser(parser: argparse.ArgumentParser, engine: TransformEngine) -> None:
    parser.add_argument(
        "base_path",
        type=Path,
        help="Base file. Selectors always apply to this file.",
    )
    parser.add_argument(
        "output_path",
        nargs="?",
        type=Path,
        help="Transformed output path. Optional when --stdout is used.",
    )
    parser.add_argument(
        "--mode",
        choices=[mode.value for mode in TransformMode],
        required=True,
        help="Transform mode.",
    )
    parser.add_argument(
        "--overlay-file",
        "--merge-file",
        dest="overlay_path",
        type=Path,
        help="Overlay file applied on top of the filtered base. Required when --mode=merge.",
    )
    parser.add_argument(
        "--selector-type",
        choices=[action.value for action in SelectorAction],
        default=SelectorAction.RETAIN.value,
        help="Preserve or remove the selected region from the base file.",
    )
    parser.add_argument(
        "--selectors",
        nargs="*",
        default=[],
        help=selector_help(engine),
    )
    parser.add_argument(
        "--stdout",
        action="store_true",
        help="Write the transformed output to stdout instead of a file.",
    )

    engine.configure_parser(parser)


def build_parser(engine: TransformEngine) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=f"Run the {engine.name} structured transform."
    )
    configure_parser(parser, engine)
    return parser



def build_request(
    parser: argparse.ArgumentParser,
    engine: TransformEngine,
    parsed_args: argparse.Namespace,
) -> TransformRequest:
    try:
        selectors_by_type = parse_selectors(engine, getattr(parsed_args, "selectors", []))
    except ValueError as e:
        parser.error(str(e))

    request = TransformRequest(
        base_path=parsed_args.base_path,
        output_path=parsed_args.output_path,
        mode=TransformMode(parsed_args.mode),
        selector_action=SelectorAction(
            getattr(parsed_args, "selector_type", SelectorAction.RETAIN.value)
        ),
        selectors_by_type=selectors_by_type,
        overlay_path=parsed_args.overlay_path,
        engine_options=engine.build_engine_options(parsed_args),
    )

    try:
        engine.validate_request(request)
    except ValueError as error:
        parser.error(str(error))

    return request



def run_parsed_engine(engine: TransformEngine, parser: argparse.ArgumentParser, parsed_args: argparse.Namespace) -> int:
    stdin_inputs = [
        path for path in (parsed_args.base_path, parsed_args.overlay_path) if path == Path("-")
    ]
    if len(stdin_inputs) > 1:
        raise ValueError("at most one of BASE and --overlay-file may read from stdin ('-')")

    parsed_args.stdin_text = __import__("sys").stdin.read() if stdin_inputs else None
    if parsed_args.output_path == Path("-"):
        parsed_args.stdout = True
        parsed_args.output_path = None
    request = build_request(parser, engine, parsed_args)
    output = engine.transform(request)
    emit_transform_output(
        request.output_path,
        output,
        stdout=bool(request.engine_option("stdout", False)),
    )
    return 0


def run_engine_cli(engine: TransformEngine, argv: list[str] | None = None) -> int:
    parser = build_parser(engine)
    return run_parsed_engine(engine, parser, parser.parse_args(argv))
