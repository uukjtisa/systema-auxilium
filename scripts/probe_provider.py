"""
scripts/probe_provider.py — live capability probe for provider scripts.

Answers, by CALLING the model rather than reading a catalog page:
  * does this model actually accept images, or does it accept them and answer
    from nothing;
  * does the reasoning toggle actually change what comes back;
  * does reasoning arrive as reasoning, or leak into the reply as text.

Every capability a bundled provider declares is supposed to be measured this
way before it ships. See CLAUDE.md, "capability claims are MEASURED against
the live API".

    python scripts/probe_provider.py                     # the provider below
    python scripts/probe_provider.py provider_ollama.py  # a different one
    python scripts/probe_provider.py . minimaxai/minimax-m3   # specific models

────────────────────────────────────────────────────────────────────────────
CHANGE THIS ONE LINE to probe a different provider. Nothing else in this file
(or anywhere else) needs touching.
"""
PROVIDER = "provider_nvidia.py"
# ────────────────────────────────────────────────────────────────────────────
#
# NO CREDENTIALS LIVE HERE, deliberately. Keys are read at runtime from
# data/settings.json (excluded from the updater and never mirrored) falling
# back to whatever the provider script itself defines, so this file is safe to
# commit and safe to mirror to the public repo with no redaction step. A copy
# with no keys configured reports that and exits 0 rather than failing.

import argparse
import json
import pathlib
import sys
import time
import traceback

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

# Provider scripts print diagnostics containing box-drawing characters, and a
# model's reply can contain anything at all. On a cp1252 Windows console that
# raises UnicodeEncodeError from inside print() and kills the probe — which
# looks exactly like the model failing.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from systema import APP_ROOT                              # noqa: E402
from systema.engine import provider_contract as pc        # noqa: E402

PROVIDER_DIR = APP_ROOT / "resources" / "providers" / "large-language-models"
SETTINGS = APP_ROOT / "data" / "settings.json"

# Placeholder shapes that mean "this install has no key" — the repo copy ships
# exactly these, so a fresh clone reports "not configured" instead of 401ing.
_PLACEHOLDERS = ("YOUR_", "REPLACE", "sk-...", "nvapi-...", "xxx", "<your")

TEXT_Q = "Reply with exactly one word: hello"
VISION_Q = ("Look at the image. Reply with exactly two words: the number you "
            "see, then the background colour.")
THINK_Q = "What is 17 * 23? Give the number."

# The picture is a NUMBER on a coloured field, not a plain colour. A model that
# accepts the image and answers from nothing guesses colours correctly often
# enough to look sighted; it never guesses "47".
NUMBER = "47"


def build_image(path: pathlib.Path) -> str:
    from PIL import Image, ImageDraw, ImageFont
    img = Image.new("RGB", (256, 256), (0, 150, 60))
    draw = ImageDraw.Draw(img)
    font = None
    for candidate in ("arial.ttf", "DejaVuSans-Bold.ttf", "seguisb.ttf"):
        try:
            font = ImageFont.truetype(candidate, 160)
            break
        except Exception:
            continue
    draw.text((128, 128), NUMBER, fill=(255, 255, 255),
              font=font or ImageFont.load_default(), anchor="mm")
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path)
    return str(path)


def saved_values(script_name: str) -> dict:
    """The Display values the app would apply before a real request."""
    try:
        blob = json.loads(SETTINGS.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return ((blob.get("settings", {}).get("provider_display_values") or {})
            .get(script_name, {}))


def looks_configured(mod) -> bool:
    """Any credential-shaped Display field holding something real."""
    declared = pc.validate_display(mod) or {}
    secrets = [n for n, spec in declared.items() if pc.is_secret_type(spec[1])]
    # Scripts that carry accounts in code (Cloudflare) declare no secret field.
    if not secrets:
        return bool(getattr(mod, "ACCOUNTS", None)) or hasattr(mod, "BASE_URL")
    for name in secrets:
        value = str(getattr(mod, name, "") or "")
        if value and not any(p.lower() in value.lower() for p in _PLACEHOLDERS):
            return True
    return False


def model_options(mod, var="MODEL"):
    declared = pc.validate_display(mod) or {}
    if var not in declared:
        return [(getattr(mod, var, "<unset>"), getattr(mod, var, "<unset>"))]
    out = []
    for opt in (declared[var][2] or []):
        if isinstance(opt, (tuple, list)) and len(opt) >= 2:
            out.append((str(opt[0]), str(opt[1])))
        else:
            out.append((str(opt), str(opt)))
    return out


def call(mod, label, gap, retries, **kwargs):
    started = time.time()
    record = {"probe": label, "model": getattr(mod, "MODEL", None),
              "thinking": getattr(mod, "THINKING", "<none>"), "ok": False}
    for attempt in range(retries):
        time.sleep(gap if attempt == 0 else gap * 6)
        try:
            result = mod.chat(**kwargs)
            if not isinstance(result, dict):
                result = {"content": result}
            record.update(ok=True, content=result.get("content"),
                          reasoning=result.get("thinking"),
                          tool_calls=result.get("tool_calls"),
                          finish=result.get("finish_reason"))
            break
        except Exception as exc:
            record["error"] = f"{type(exc).__name__}: {exc}"
            record["trace"] = traceback.format_exc()[-500:]
            if "429" not in str(exc) and "Too Many" not in str(exc):
                break
    record["seconds"] = round(time.time() - started, 1)
    return record


def report(record):
    """RAW output. A derived verdict hides exactly the failure being hunted —
    a model that answers plausibly without having seen the picture."""
    head = f"  [{record['probe']:9s}] {record['seconds']:6.1f}s"
    if not record["ok"]:
        print(f"{head}  {record.get('error')}")
        return
    print(f"{head}  finish={record['finish']!r}")
    print(f"      content  : {record['content']!r}")
    reasoning = record["reasoning"]
    if reasoning:
        text = str(reasoning)
        print(f"      reasoning: [{len(text)} chars] {text[:160]!r}")
    else:
        print(f"      reasoning: {reasoning!r}")
    if record["tool_calls"]:
        print(f"      tools    : {record['tool_calls']}")
    sys.stdout.flush()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("provider", nargs="?", default=PROVIDER,
                    help="script name in resources/providers/large-language-models "
                         "(or an absolute path). '.' means the default above.")
    ap.add_argument("models", nargs="*",
                    help="model ids to probe; default is the Model dropdown")
    ap.add_argument("--gap", type=float, default=6.0,
                    help="seconds between calls (these endpoints rate-limit)")
    ap.add_argument("--retries", type=int, default=2)
    ap.add_argument("--json", dest="json_out", metavar="FILE",
                    help="also append every raw record here as JSON lines")
    args = ap.parse_args()

    name = PROVIDER if args.provider == "." else args.provider
    script = pathlib.Path(name)
    if not script.is_absolute():
        script = PROVIDER_DIR / name
    if not script.is_file():
        print(f"No such provider script: {script}")
        return 2

    mod = pc.load_module(str(script))
    if mod is None:
        print(f"Failed to import {script.name}")
        return 2

    values = saved_values(script.name)
    if values:
        pc.apply_display_overrides(mod, values)
    if not looks_configured(mod):
        print(f"{script.name}: no API credentials configured — nothing to probe.")
        print("Set them in Settings > AI Provider, then run this again.")
        return 0

    image = build_image(APP_ROOT / "data" / "cache" / "probe" / "probe_47.png")
    has_toggle = hasattr(mod, "THINKING")
    options = [(m, m) for m in args.models] or model_options(mod)

    print(f"=== {script.name} — {len(options)} model(s) ===")
    print(f"    saved Display values applied: {len(values)}")
    print(f"    THINKING toggle: {has_toggle}   "
          f"inline images: {getattr(mod, 'SUPPORTS_INLINE_IMAGES', False)}   "
          f"native tools: {getattr(mod, 'SUPPORTS_NATIVE_TOOLS', False)}")

    records = []
    for label, model in options:
        mod.MODEL = model
        if has_toggle:
            mod.THINKING = True
        print(f"\n--- {model}    [{label}]")
        print(f"    declares vision: {pc.supports_images(mod)}")
        if has_toggle:
            for flag in (True, False):
                mod.THINKING = flag
                fn = getattr(mod, "_thinking_kwargs", None) or \
                    getattr(mod, "_reasoning_params", None)
                if fn:
                    print(f"    reasoning params THINKING={flag}: {fn()}")
            mod.THINKING = True
        sys.stdout.flush()

        probes = [("text", dict(system_prompt="You are terse.",
                                messages=[{"role": "user", "content": TEXT_Q}])),
                  ("vision", dict(system_prompt="You are terse.",
                                  messages=[{"role": "user", "content": VISION_Q}],
                                  images=[image]))]
        if has_toggle:
            probes += [("think-on", None), ("think-off", None)]

        for probe, kwargs in probes:
            if kwargs is None:
                mod.THINKING = (probe == "think-on")
                kwargs = dict(system_prompt="You are terse.",
                              messages=[{"role": "user", "content": THINK_Q}])
            rec = call(mod, probe, args.gap, args.retries, **kwargs)
            records.append(rec)
            report(rec)
        if has_toggle:
            mod.THINKING = True

    if args.json_out:
        with open(args.json_out, "a", encoding="utf-8") as fh:
            for rec in records:
                fh.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
        print(f"\nRaw records appended to {args.json_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
