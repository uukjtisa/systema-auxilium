"""
providers/large-language-models/llama_cpp_provider.py
Local LLaMA / GGUF models via llama-cpp-python — fully offline LLM provider

Runs a quantized GGUF model on your own machine (CPU and/or GPU). No API key,
no network — everything happens locally.

────────────────────────────────────────────────────────────────────────────────
SETUP
────────────────────────────────────────────────────────────────────────────────
1. Install the runtime (pick ONE that matches your hardware):

     # CPU only (works everywhere)
     pip install llama-cpp-python

     # NVIDIA GPU (CUDA 12.1 prebuilt wheels — much faster)
     pip install llama-cpp-python \
         --extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cu121

     # Apple Silicon (Metal) is enabled by default in the standard wheel.

2. Download a GGUF model file. Good starting points on Hugging Face:
     - bartowski/Qwen2.5-7B-Instruct-GGUF          (Q4_K_M ≈ 4.7 GB)
     - bartowski/Meta-Llama-3.1-8B-Instruct-GGUF    (Q4_K_M ≈ 4.9 GB)
     - bartowski/Mistral-7B-Instruct-v0.3-GGUF      (Q4_K_M ≈ 4.4 GB)
   Q4_K_M is a good size/quality balance. Bigger quant = better quality, more RAM.

3. Point MODEL_PATH below at the downloaded .gguf file, then select this script
   as your Custom Script Provider in Settings.

────────────────────────────────────────────────────────────────────────────────
NOTES
────────────────────────────────────────────────────────────────────────────────
- The model is loaded ONCE and cached across requests (stashed on `sys`), so the
  multi-gigabyte file is not reloaded on every message. Changing any setting
  below transparently reloads it on the next request.
- Chat formatting uses the template embedded in the GGUF metadata, so the correct
  prompt format for your model is applied automatically. Override with CHAT_FORMAT
  only if a model ships without a usable template.
"""

import sys

# ─── Configuration ─────────────────────────────────────────────────────────────

# Absolute path to your .gguf model file. Forward slashes are fine on Windows.
MODEL_PATH   = r"C:/models/Qwen2.5-7B-Instruct-Q4_K_M.gguf"

N_CTX        = 8192      # context window (tokens). Larger = more RAM.
N_GPU_LAYERS = 0         # layers to offload to GPU: 0 = CPU only, -1 = all on GPU.
N_THREADS    = None      # CPU threads. None = let llama.cpp choose (os.cpu_count()).
N_BATCH      = 512       # prompt batch size; raise for faster prompt ingestion.

TEMPERATURE  = 0.7
TOP_P        = 0.95
MAX_TOKENS   = 2048      # max tokens to generate per reply.
REPEAT_PENALTY = 1.1
SEED         = -1        # -1 = random each load; set an int for reproducibility.

CHAT_FORMAT  = None      # e.g. "llama-3", "chatml", "mistral-instruct".
                         # None = use the template baked into the GGUF (recommended).
VERBOSE      = False     # True to print llama.cpp load/inference logs to console.
# ──────────────────────────────────────────────────────────────────────────────

# ── Native tool calling (opt-in, model-dependent) ──────────────────────────────
# llama-cpp-python CAN do OpenAI-style function calling, but ONLY if the loaded
# GGUF model and chat template actually support tools. Many local models do not
# and will just chat instead of emitting tool_calls. So this ships DISABLED:
# leave it False (Systema's universal Compatibility mode drives tools via the
# system prompt and works with any local model). Flip it to True only after you
# confirm your model returns real tool_calls — a tool-capable model plus
# CHAT_FORMAT = "chatml-function-calling" is the usual recipe. chat_tools() below
# is fully implemented either way.
SUPPORTS_NATIVE_TOOLS = False
NATIVE_DIALECT        = "openai"


# Key under which the loaded model + its config live on the persistent `sys`
# module, so the instance survives this file being re-imported each request.
_CACHE_ATTR = "_systema_llama_cpp_cache"


def _config_signature():
    """Settings that require a full model reload if changed."""
    return (MODEL_PATH, N_CTX, N_GPU_LAYERS, N_THREADS, N_BATCH, CHAT_FORMAT, SEED)


def _get_llm():
    """Return a cached Llama instance, (re)loading only when config changes."""
    import os

    cache = getattr(sys, _CACHE_ATTR, None)
    sig = _config_signature()
    if cache is not None and cache.get("sig") == sig:
        return cache["llm"]

    try:
        from llama_cpp import Llama
    except ImportError as e:
        raise RuntimeError(
            "llama-cpp-python is not installed. Install it with:\n"
            "    pip install llama-cpp-python\n"
            "(or the CUDA/Metal build — see the header of this file)."
        ) from e

    if not os.path.isfile(MODEL_PATH):
        raise RuntimeError(
            f"Model file not found: {MODEL_PATH}\n"
            "Download a .gguf model and set MODEL_PATH at the top of this script."
        )

    # Free a previously loaded model before loading a new one (config changed).
    if cache is not None:
        try:
            del cache["llm"]
        except Exception:
            pass

    kwargs = dict(
        model_path=MODEL_PATH,
        n_ctx=N_CTX,
        n_gpu_layers=N_GPU_LAYERS,
        n_batch=N_BATCH,
        seed=SEED,
        verbose=VERBOSE,
    )
    if N_THREADS:
        kwargs["n_threads"] = N_THREADS
    if CHAT_FORMAT:
        kwargs["chat_format"] = CHAT_FORMAT

    llm = Llama(**kwargs)
    setattr(sys, _CACHE_ATTR, {"sig": sig, "llm": llm})
    return llm


def _build_messages(system_prompt, messages):
    return (
        [{"role": "system", "content": system_prompt}] if system_prompt else []
    ) + list(messages)


def chat(system_prompt: str, messages: list) -> str:
    """
    Required signature for all LLM provider scripts.

    Args:
        system_prompt : Full effective system prompt (may be empty, never None).
        messages      : List of {"role": "user"|"assistant", "content": str} dicts.
                        The latest user message is always the last entry.

    Returns:
        A non-empty string response. Raising an exception surfaces as an error.
    """
    llm = _get_llm()

    result = llm.create_chat_completion(
        messages=_build_messages(system_prompt, messages),
        temperature=TEMPERATURE,
        top_p=TOP_P,
        max_tokens=MAX_TOKENS,
        repeat_penalty=REPEAT_PENALTY,
    )

    reply = (result["choices"][0]["message"].get("content") or "").strip()
    if not reply:
        raise RuntimeError("Local model returned an empty response.")
    return reply


def chat_tools(system_prompt: str, messages: list, tools: list, images=None) -> dict:
    """Native function-calling entrypoint (OpenAI dialect).

    ``llm.create_chat_completion`` already returns an OpenAI-shaped dict, so
    native_adapters converts the tool schema going in and parses the reply going
    out into the normalized result the app expects:
        {"text": str | None, "tool_calls": [{"id","name","arguments"}, ...]}
    Requires a tool-capable GGUF model (see the SUPPORTS_NATIVE_TOOLS note above).
    """
    from systema.engine import native_adapters as na

    llm = _get_llm()
    result = llm.create_chat_completion(
        messages=_build_messages(system_prompt, messages),
        tools=na.to_openai_tools(tools),
        tool_choice="auto",
        temperature=TEMPERATURE,
        top_p=TOP_P,
        max_tokens=MAX_TOKENS,
        repeat_penalty=REPEAT_PENALTY,
    )
    return na.parse_openai(result)


# ── Quick test ────────────────────────────────────────────────────────────────
# Run directly to load the model and verify a reply:  python llama-cpp-provider.py
# (Skipped automatically until MODEL_PATH points at a real .gguf file.)

if __name__ == "__main__":
    import os as _os

    print(f"Testing llama.cpp provider... model_path={MODEL_PATH}")
    print("-" * 60)
    if not _os.path.isfile(MODEL_PATH):
        print("MODEL_PATH does not point at a .gguf file yet — set it, then re-run.")
    else:
        try:
            result = chat(
                system_prompt="You are a helpful assistant.",
                messages=[{"role": "user", "content": "Say 'Provider test successful.' and nothing else."}],
            )
            print("Response:", result)
            print("Test passed.")
        except Exception as e:
            print(f"Test failed: {e}")
