"""Tests for the menu callback fallback pattern."""
from accountingbot.bot import MAIN_MENU_ACTIONS, MENU_CALLBACK_FALLBACK_PATTERN


def test_menu_fallback_pattern_ignores_known_actions():
    for action in MAIN_MENU_ACTIONS:
        assert (
            MENU_CALLBACK_FALLBACK_PATTERN.match(f"menu:{action}") is None
        ), action


def test_menu_fallback_pattern_matches_unknown_actions():
    unknown_actions = ["menu:unknown", "menu:new_feature", "menu:123"]
    for callback_data in unknown_actions:
        assert MENU_CALLBACK_FALLBACK_PATTERN.match(callback_data)
