from __future__ import annotations

from dotman.resolver import (
    ResolverOption,
    build_full_spec_selector_field_kinds,
    build_full_spec_selector_match_fields,
    build_profile_field_kinds,
    build_profile_match_fields,
    build_selector_field_kinds,
    build_package_match_fields,
    build_selector_match_fields,
    parse_slash_qualified_query,
    rank_resolver_option,
)


def test_rank_resolver_option_prefers_leftmost_selector_segment() -> None:
    sunshine = ResolverOption(
        display_label="sandbox/sunshine",
        match_fields=build_selector_match_fields(repo_name="sandbox", selector="sunshine"),
        field_kinds=build_selector_field_kinds(),
    )
    linux_meta = ResolverOption(
        display_label="sandbox/host/linux-meta",
        match_fields=build_selector_match_fields(repo_name="sandbox", selector="host/linux-meta"),
        field_kinds=build_selector_field_kinds(),
    )

    assert rank_resolver_option(query="s", option=sunshine) < rank_resolver_option(query="s", option=linux_meta)


def test_rank_resolver_option_treats_profile_matches_as_weaker_fallback() -> None:
    selector_hit = ResolverOption(
        display_label="sandbox/host/linux-meta@default",
        match_fields=build_full_spec_selector_match_fields(
            repo_name="sandbox",
            selector="host/linux-meta",
            profile="default",
        ),
        field_kinds=build_full_spec_selector_field_kinds(),
    )
    profile_only_hit = ResolverOption(
        display_label="sandbox/sunshine@host/linux",
        match_fields=build_full_spec_selector_match_fields(
            repo_name="sandbox",
            selector="sunshine",
            profile="host/linux",
        ),
        field_kinds=build_full_spec_selector_field_kinds(),
    )

    assert rank_resolver_option(query="linux", option=selector_hit) < rank_resolver_option(
        query="linux",
        option=profile_only_hit,
    )


def test_parse_slash_qualified_query_uses_repo_prefix_as_lookup_hint() -> None:
    assert parse_slash_qualified_query(
        repo_names=["alpha", "beta"],
        explicit_repo=None,
        selector="beta/sunshine",
    ) == ("beta", "sunshine")


def test_profile_ranking_uses_simple_match_instead_of_segment_scoring() -> None:
    profile_prefix = ResolverOption(
        display_label="linux/work",
        match_fields=build_profile_match_fields(profile="linux/work"),
        field_kinds=build_profile_field_kinds(),
    )
    profile_substring = ResolverOption(
        display_label="host/linux",
        match_fields=build_profile_match_fields(profile="host/linux"),
        field_kinds=build_profile_field_kinds(),
    )

    assert rank_resolver_option(query="linux", option=profile_prefix) < rank_resolver_option(
        query="linux",
        option=profile_substring,
    )


def test_package_match_fields_keep_repo_qualified_fallback_after_selector_field() -> None:
    assert build_package_match_fields(repo_name="sandbox", package_id="sunshine") == (
        "sunshine",
        "sandbox:sunshine",
        "sandbox/sunshine",
    )


def test_selector_match_fields_prefer_canonical_repo_qualified_form_before_alias() -> None:
    assert build_selector_match_fields(repo_name="sandbox", selector="sunshine") == (
        "sunshine",
        "sandbox:sunshine",
        "sandbox/sunshine",
    )


def test_binding_match_fields_keep_canonical_repo_qualified_binding_before_slash_alias() -> None:
    assert build_full_spec_selector_match_fields(repo_name="sandbox", selector="sunshine", profile="host/linux") == (
        "sunshine",
        "sandbox:sunshine",
        "sandbox/sunshine",
        "sunshine@host/linux",
        "sandbox:sunshine@host/linux",
        "sandbox/sunshine@host/linux",
    )
