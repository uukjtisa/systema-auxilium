"""
tests/systema/app/test_identity_prompt.py

A fresh install shipped `user_name = "USER"`. That string is truthy, so it flowed
into the system prompt as **USER NAME: USER** together with "address the user by
their name" — and the assistant greeted real people as "USER".

A placeholder is not a name. With no name set the prompt must say so, tell the
assistant to address them directly instead, and mention the sidebar's
name / assistant-name / custom-instructions options once, as optional.
"""
import pytest

from systema.app.controller import build_identity_block


# ── the unnamed user ─────────────────────────────────────────────────────────

def test_a_fresh_install_is_not_told_the_user_is_called_USER():
    block = build_identity_block()

    assert "USER NAME: NOT SET YET" in block
    assert "**USER NAME:** USER" not in block


def test_the_shipped_placeholder_is_treated_as_unset():
    """Installs that already saved the old default must recover too."""
    assert "NOT SET YET" in build_identity_block(user_name="USER")


@pytest.mark.parametrize("placeholder", ["user", " USER ", "Username", "name", ""])
def test_placeholder_spellings_are_all_treated_as_unset(placeholder):
    assert "NOT SET YET" in build_identity_block(user_name=placeholder)


def test_the_assistant_is_told_not_to_use_a_placeholder_or_invent_one():
    block = build_identity_block()

    assert "NEVER call them" in block
    assert "invent a name" in block


def test_the_sidebar_options_are_offered_once_and_optional():
    """Encouragement, not nagging — the point is that they CAN personalise it."""
    block = build_identity_block().lower()

    assert "sidebar" in block
    assert "custom instructions" in block
    assert "personality" in block
    assert "optional" in block
    assert "once" in block and "without nagging" in block


# ── the named user ───────────────────────────────────────────────────────────

def test_a_real_name_is_used_normally():
    block = build_identity_block(user_name="Thirdy")

    assert "**USER NAME:** Thirdy" in block
    assert "NOT SET YET" not in block


def test_a_real_name_that_merely_contains_user_is_kept():
    """Guard rail on the placeholder check: don't reject a legitimate name."""
    assert "**USER NAME:** Usermann" in build_identity_block(user_name="Usermann")


def test_surrounding_whitespace_is_trimmed_from_a_real_name():
    assert "**USER NAME:** Thirdy" in build_identity_block(user_name="  Thirdy  ")


# ── assistant identity + custom instructions ─────────────────────────────────

def test_a_custom_assistant_name_becomes_the_primary_identity():
    block = build_identity_block(assistant_name="Kimi")

    assert "Your name is **Kimi**" in block
    assert "CUSTOM ASSISTANT IDENTITY" in block


def test_without_a_custom_name_it_offers_one():
    block = build_identity_block()

    assert "You are Systema Auxilium" in block
    assert "custom name" in block


def test_custom_instructions_are_appended_verbatim():
    block = build_identity_block(custom_instructions="Always answer in Tagalog.")
    assert "Always answer in Tagalog." in block


def test_no_custom_instructions_section_when_there_are_none():
    assert "CUSTOM USER INSTRUCTIONS" not in build_identity_block()


def test_the_block_is_a_pure_function_of_its_inputs():
    """No app state, no I/O — the same inputs always produce the same text."""
    a = build_identity_block("Kimi", "Thirdy", "be terse")
    b = build_identity_block("Kimi", "Thirdy", "be terse")
    assert a == b
