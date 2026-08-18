"""
tests/systema/engine/test_provider_model_tables.py

A model dropdown is a promise that the ids in it exist and behave as labelled.
On 2026-08-19 `provider_nvidia` was offering four ids that `GET /v1/models` no
longer served — including its own default, `z-ai/glm-5.1`, which was not even
present in its own dropdown. They had been copied from third-party catalog
pages rather than measured.

These checks are offline and structural. They cannot tell you an id is still
served — only `scripts/probe_provider.py` against the live API can, and that is
the required step before changing a dropdown. What they DO catch is the table
drifting out of step with itself, and the specific retired ids coming back.
"""
import pytest

from systema import APP_ROOT
from systema.engine import provider_contract as pc

FOLDER = APP_ROOT / "resources" / "providers" / "large-language-models"

# Confirmed absent from GET /v1/models on 2026-08-19. Re-add only after a live
# probe says otherwise.
NVIDIA_RETIRED = (
    "z-ai/glm-5.1",
    "z-ai/glm5",
    "deepseek-ai/deepseek-v4-pro",
    "deepseek-ai/deepseek-v4-flash",          # superseded by the -0731 snapshot
    "qwen/qwen3-coder-480b-a35b-instruct",    # whole Qwen endpoint retired
)


def _load(name):
    mod = pc.load_module(str(FOLDER / name))
    assert mod is not None, f"{name} failed to import"
    return mod


def _options(mod, var="MODEL"):
    spec = (pc.validate_display(mod) or {}).get(var)
    assert spec, f"{var} is not a Display entry"
    out = []
    for opt in (spec[2] or []):
        out.append(opt[1] if isinstance(opt, (tuple, list)) else opt)
    return out, (spec[3] or {}).get("item_tooltips") or []


@pytest.fixture(scope="module")
def nvidia():
    return _load("provider_nvidia.py")


def test_every_option_has_its_own_tooltip(nvidia):
    """The two lists are positional. A mismatch silently shifts every tooltip
    onto the wrong model, which is worse than having none."""
    options, tips = _options(nvidia)
    assert len(options) == len(tips)


def test_no_retired_id_is_offered(nvidia):
    options, _ = _options(nvidia)
    back = [m for m in NVIDIA_RETIRED if m in options]
    assert not back, f"retired NVIDIA model ids are back in the dropdown: {back}"


def test_the_default_model_is_one_the_dropdown_offers(nvidia):
    """The old default `z-ai/glm-5.1` was absent from its own list, so the
    settings form could not even display what was selected."""
    options, _ = _options(nvidia)
    assert nvidia.MODEL in options


def test_every_offered_model_is_in_the_capability_table(nvidia):
    """The table is authoritative for shipped ids; the string heuristics exist
    only for whatever a user types into Custom…."""
    options, _ = _options(nvidia)
    missing = [m for m in options if m not in nvidia._MODEL_INFO]
    assert not missing, f"offered but not described in _MODEL_INFO: {missing}"


def test_vision_answers_come_from_the_table_not_the_heuristic(nvidia):
    """glm-5.2 is the case that matters: it accepts image blocks and answers
    without seeing them, so nothing errors and only the table can say no.
    Equally, minimax-m3 carries no 'vl'/'vision'/'omni' marker in its id and
    the old substring guess called it blind — while it is the vision model."""
    original = nvidia.MODEL
    try:
        nvidia.MODEL = "z-ai/glm-5.2"
        assert pc.supports_images(nvidia) is False
        nvidia.MODEL = "minimaxai/minimax-m3"
        assert pc.supports_images(nvidia) is True
    finally:
        nvidia.MODEL = original


def test_thinking_toggle_produces_distinct_params_per_family(nvidia):
    """Families do not share a spelling. Handing a model another family's keys
    is silent — no reasoning, no error — which is how `if "deepseek" in MODEL`
    shipped GLM's keys to MiniMax and Inkling."""
    options, _ = _options(nvidia)
    original, original_flag = nvidia.MODEL, nvidia.THINKING
    try:
        for model in options:
            nvidia.MODEL = model
            nvidia.THINKING = True
            on = nvidia._thinking_kwargs()
            nvidia.THINKING = False
            off = nvidia._thinking_kwargs()
            assert isinstance(on, dict) and isinstance(off, dict)
            family = nvidia._MODEL_INFO[model][1]
            if family is None:
                assert on == {} and off == {}, (
                    f"{model} has no reasoning mode but was sent {on}")
            else:
                assert on != off, f"{model}: the toggle changes nothing ({on})"
    finally:
        nvidia.MODEL, nvidia.THINKING = original, original_flag


def test_deepseek_effort_is_clamped_to_what_it_accepts(nvidia):
    """DeepSeek V4 takes high or max only. It also HANGS rather than erroring
    when the block is missing, so the off variant still sends one."""
    original, effort, flag = nvidia.MODEL, nvidia.REASONING_EFFORT, nvidia.THINKING
    try:
        nvidia.MODEL = "deepseek-ai/deepseek-v4-flash-0731"
        nvidia.THINKING = True
        for asked in ("low", "medium", "high", "max"):
            nvidia.REASONING_EFFORT = asked
            got = nvidia._thinking_kwargs()["reasoning_effort"]
            assert got in ("high", "max"), f"{asked} -> {got}"
        nvidia.THINKING = False
        assert nvidia._thinking_kwargs(), "off must still send the block"
    finally:
        nvidia.MODEL, nvidia.REASONING_EFFORT, nvidia.THINKING = (
            original, effort, flag)
