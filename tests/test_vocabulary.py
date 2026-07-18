"""Lexicon sanity checks: the vocabulary module is the single source of truth."""

from spot_intake.vocabulary import (
    COMMENT_KEYWORD_CATEGORIES,
    COMMENT_KEYWORD_CATEGORY_ALIASES,
    FISH_PATTERNS,
)


def test_canonical_name_is_its_own_alias():
    for canonical, aliases in FISH_PATTERNS.items():
        assert canonical in aliases, f"{canonical} missing from its own alias list"


def test_no_alias_collides_across_species():
    owner: dict[str, str] = {}
    for canonical, aliases in FISH_PATTERNS.items():
        for alias in aliases:
            assert alias not in owner, f"alias {alias!r} claimed by both {owner.get(alias)} and {canonical}"
            owner[alias] = canonical


def test_keyword_category_aliases_resolve_to_known_categories():
    for alias, target in COMMENT_KEYWORD_CATEGORY_ALIASES.items():
        assert target in COMMENT_KEYWORD_CATEGORIES, f"alias {alias!r} -> unknown category {target!r}"
