# Tests

Automated test suite for Systema Auxilium, using [pytest](https://pytest.org).

## Layout

`tests/` mirrors the `systema/` package. Each source module gets a matching
`test_<module>.py` under the same sub-path:

```
tests/
  conftest.py                         # shared fixtures (sample_tree, qapp)
  systema/
    execution/
      test_file_tools.py              # -> systema/execution/file_tools.py
      test_tool_registry.py           # -> systema/execution/tool_registry.py
      test_tool_manager.py            # -> systema/execution/tool_manager.py
    engine/
      test_prompts.py                 # -> systema/engine/prompts/{compat,native}.py
```

## Running

From the repository root:

```bash
pip install pytest              # once
pytest                          # whole suite
pytest tests/systema/execution  # one area
pytest -k grep                  # by keyword
```

Configuration lives in `pytest.ini` (import mode, test discovery, `pythonpath`).

## Dependencies

The **core** suite — the `grep` search tool, the tool registry, and the
compat/native prompt-parity checks — imports only the standard library and the
lightweight `systema.execution` / `systema.engine.prompts` modules, so it runs
on a bare `pip install pytest` with no GUI stack.

Tests that build Qt objects (e.g. `test_tool_manager.py`) use the `qapp`
fixture, which **skips** automatically when PyQt6 is not installed. Install the
full app requirements to exercise them:

```bash
pip install -r requirements.txt
pytest
```

## Continuous integration

`.github/workflows/tests.yml` runs the suite on every push and pull request to
`main` and `unstable` across Python 3.10–3.12, with coverage reported for
`systema.execution` and `systema.engine.prompts`.

## Adding tests

1. Create `tests/systema/<subpkg>/test_<module>.py` mirroring the source path.
2. Name test functions `test_*`; put shared setup in a fixture (module-local or
   in `conftest.py`).
3. Keep new tests dependency-free where practical; gate any that need PyQt6
   behind the `qapp` fixture so CI stays green without a display.
