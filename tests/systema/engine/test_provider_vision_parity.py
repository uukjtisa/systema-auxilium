"""
tests/systema/engine/test_provider_vision_parity.py

A provider must not be able to CLAIM vision without the machinery to send a
picture.

provider_opencode_zen declared vision (2026-08-02, once per-model capability
made it True for mimo-v2.5-free and the paid multimodal ids) while its chat()
accepted `images=` and never read it. The app therefore attached the picture,
flattened it to the `[Image N]` text marker, and the model answered by GUESSING
what was in it — confidently, with no error anywhere. That is strictly worse
than refusing the attachment.

These tests are structural, like test_capability_manifest.py: they check the
shipped scripts, so a new provider (or a capability flipped on) cannot
reintroduce the same silent hole.
"""
import pathlib
import re

import pytest

from systema import APP_ROOT
from systema.engine import provider_contract as pc

FOLDER = APP_ROOT / "resources" / "providers" / "large-language-models"


def _scripts():
    return sorted(p for p in FOLDER.glob("*.py"))


def _can_ever_see(mod) -> bool:
    """True if this script reports vision for ANY model it offers.

    SUPPORTS_VISION is per-model now, so asking once is not enough — walk the
    Model dropdown's values the way a user picking from it would.
    """
    declared = getattr(mod, "SUPPORTS_VISION", True)
    if not callable(declared):
        return bool(declared)
    display = pc.validate_display(mod) or {}
    var_names = [v for v in ("MODEL", "CF_MODEL") if v in display]
    for var in var_names:
        options = display[var][2] or []
        original = getattr(mod, var, None)
        try:
            for opt in options:
                value = opt[1] if isinstance(opt, (tuple, list)) and len(opt) >= 2 else opt
                setattr(mod, var, value)
                if pc.supports_images(mod):
                    return True
        finally:
            if original is not None:
                setattr(mod, var, original)
    return False


@pytest.mark.parametrize("path", _scripts(), ids=lambda p: p.name)
def test_a_vision_capable_script_can_actually_encode_an_image(path):
    """Claiming vision requires an encoder. This is the exact hole that let
    provider_opencode_zen accept attachments and drop them on the floor.

    Checked by CAPABILITY, not by name: the bundled scripts spell the encoder
    `_encode_image`, `_image_block` and `_inline_image`, and all three are
    fine. What must not exist is a script that reports vision with no way to
    turn a path into a payload block at all."""
    mod = pc.load_module(str(path))
    assert mod is not None, f"{path.name} failed to import"
    if not _can_ever_see(mod):
        return                      # honestly text-only, nothing to check
    encoders = [n for n in dir(mod)
                if "image" in n.lower() and callable(getattr(mod, n, None))]
    src = pathlib.Path(path).read_text(encoding="utf-8")
    assert encoders or "b64encode" in src, (
        f"{path.name} reports vision for at least one model but has no image "
        f"encoder — an attachment would be silently dropped and the model "
        f"would guess")


@pytest.mark.parametrize("path", _scripts(), ids=lambda p: p.name)
def test_a_vision_capable_script_reads_its_images_argument(path):
    """chat() must actually USE `images`, not merely accept it."""
    mod = pc.load_module(str(path))
    if mod is None or not _can_ever_see(mod):
        return
    src = pathlib.Path(path).read_text(encoding="utf-8")
    body = src.split("def chat(", 1)[-1]
    # Drop the signature line — `images=None` there proves nothing, which is
    # exactly how provider_opencode_zen looked while dropping every picture.
    body = body.split("\n", 1)[1] if "\n" in body else ""
    # Stop at the next top-level def so we only read chat()'s own body.
    body = re.split(r"\n(?=def |# ─|# ━)", body)[0]
    assert "images" in body, (
        f"{path.name}: chat() accepts `images` and never reads it — the "
        f"attachment would be silently dropped and the model would guess")


@pytest.mark.parametrize("path", _scripts(), ids=lambda p: p.name)
def test_declared_formats_are_broad_enough_to_be_useful(path):
    """A script that RE-ENCODES through Pillow gates what can be ATTACHED, not
    what the endpoint accepts, so its list should be broad — a narrow one
    refuses a .jfif at the dialog for no reason.

    Scripts that ship the file's own bytes are exempt and SHOULD stay narrow:
    declaring a format they cannot convert would just move the failure from
    the attach dialog to the provider's API."""
    mod = pc.load_module(str(path))
    if mod is None or not _can_ever_see(mod):
        return
    src = pathlib.Path(path).read_text(encoding="utf-8")
    if "PIL" not in src:
        return                      # no converter — a narrow list is honest
    caps = pc.image_capabilities(mod)
    if caps.formats is None:
        return                      # None = accepts anything, fine
    # Assert on the declared SET, not caps.accepts(): accepts() is also gated on
    # vision for the CURRENTLY selected model, which is right for the app but
    # would make this test depend on whichever model the script defaults to.
    for ext in ("png", "jpg", "jpeg", "jfif", "bmp", "tif", "tiff", "webp", "gif"):
        assert ext in caps.formats, f"{path.name} refuses .{ext} attachments"
