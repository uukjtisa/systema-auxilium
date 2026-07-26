"""
tests/systema/ui/test_card_text_format.py

A QLabel left on AutoText decides between plain text and rich text by sniffing
the string for markup. An HTML-ESCAPED string has none — `Skill &#x27;pptx&#x27;
loaded.` contains no tag — so Qt showed it verbatim, entities and all, which is
what the skill-loaded card was displaying. The same escape sat on the
image-attach card's filenames, where an apostrophe would have done it too.

Escaping is only correct when the string is being assembled INTO html. Where a
card shows a bare string, the fix is the other direction: don't escape, and
declare PlainText — which also stops a traceback's `<module>` or a filename's
angle brackets from being eaten as a tag.

These check the raw sources, so the rule holds without standing up a chat
window: any label built from a bare (unescaped) string must declare its format.
"""
import ast
from pathlib import Path

import pytest

_CARDS = Path(__file__).resolve().parents[3] / "systema" / "ui" / "chat" / "event_cards.py"


def _tree():
    return ast.parse(_CARDS.read_text(encoding="utf-8"))


def _card_body(name: str):
    for node in ast.walk(_tree()):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"{name} not found in {_CARDS.name}")


def _label_assignments(fn):
    """{variable name: the QLabel(...) call} for every `x = QLabel(...)`."""
    out = {}
    for node in ast.walk(fn):
        if (isinstance(node, ast.Assign) and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
                and isinstance(node.value, ast.Call)
                and getattr(node.value.func, 'id', None) == 'QLabel'):
            out[node.targets[0].id] = node.value
    return out


def _escapes(call):
    return any(isinstance(n, ast.Attribute) and n.attr == 'escape'
               for n in ast.walk(call))


def _declares_plain_text(fn, var):
    for node in ast.walk(fn):
        if (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == 'setTextFormat'
                and getattr(node.func.value, 'id', None) == var):
            return 'PlainText' in ast.dump(node)
    return False


@pytest.mark.parametrize("card,var", [
    ("add_skill_action_card", "detail_lbl"),
    ("add_image_attach_card", "name_lbl"),
])
def test_a_bare_string_label_is_not_html_escaped(card, var):
    fn = _card_body(card)
    call = _label_assignments(fn).get(var)
    assert call is not None, f"{var} is no longer a QLabel(...) in {card}"
    assert not _escapes(call), (
        f"{card}: {var} escapes its text but never renders as html — the "
        f"entities show up literally (&#x27; for an apostrophe)")


@pytest.mark.parametrize("card,var", [
    ("add_skill_action_card", "detail_lbl"),
    ("add_image_attach_card", "name_lbl"),
])
def test_a_bare_string_label_declares_plain_text(card, var):
    fn = _card_body(card)
    assert _declares_plain_text(fn, var), (
        f"{card}: {var} is left on AutoText, so a message containing "
        f"<module> or a tag-shaped filename gets parsed as markup")


def test_labels_that_really_do_build_html_still_escape():
    """Guard rail on the fix: the web card's <a href> must keep escaping, or
    this becomes an injection instead of a rendering bug."""
    fn = _card_body("add_web_page_card")
    call = _label_assignments(fn).get("src_lbl")
    assert call is not None and _escapes(call)
