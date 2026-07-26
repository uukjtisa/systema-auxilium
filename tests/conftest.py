"""
tests/conftest.py

Shared pytest fixtures for the Systema Auxilium suite.

The core suite (execution + engine) is dependency-free: it imports only stdlib
and the lightweight `systema.execution` / `systema.engine.prompts` modules, so
it runs on a bare `pip install pytest` in CI. Fixtures that need PyQt6
(`qapp`) call `pytest.importorskip` so those tests skip cleanly where Qt is
absent instead of erroring.
"""
import os
import sys
from pathlib import Path

import pytest

# Make the project root importable even if pytest is invoked oddly (belt-and-
# braces alongside pytest.ini's `pythonpath = .`).
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

# Qt must never try to reach a real display during tests.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture
def sample_tree(tmp_path):
    """A small, deterministic source tree for search / read / edit tests.

    Layout::

        <tmp>/
          notes.txt          "plain text, no todo here"  (lowercase todo)
          src/a.py           import os / # TODO alpha / def send / # TODO beta
          src/b.js           // TODO js / function send() {}
          .venv/ignored.py   # TODO should be pruned      (skipped by default)
    """
    (tmp_path / "src").mkdir()
    (tmp_path / ".venv").mkdir()
    (tmp_path / "src" / "a.py").write_text(
        "import os\n"
        "# TODO alpha\n"
        "def send(msg):\n"
        "    return msg\n"
        "# TODO beta\n",
        encoding="utf-8",
    )
    (tmp_path / "src" / "b.js").write_text(
        "// TODO js\nfunction send() {}\n", encoding="utf-8"
    )
    (tmp_path / ".venv" / "ignored.py").write_text(
        "# TODO should be pruned\n", encoding="utf-8"
    )
    (tmp_path / "notes.txt").write_text(
        "plain text, no todo here\n", encoding="utf-8"
    )
    return tmp_path


@pytest.fixture
def tool_manager(qapp):
    """A ToolManager with deterministic teardown.

    Each ToolManager owns an ApprovalSignal QObject wired to its own bound
    methods, plus a PythonInterpreter. Letting dozens of them simply fall out of
    scope over a full-suite run left queued signal deliveries aimed at
    already-collected C++ objects — which then crashed the interpreter (Windows
    access violation) inside whichever unrelated test next called
    processEvents(). Tests that build a ToolManager should use this fixture.
    """
    from systema.execution.tool_manager import ToolManager
    tm = ToolManager()
    yield tm
    try:
        tm.approval_signal.disconnect()
    except (TypeError, RuntimeError):
        pass            # nothing connected — fine
    try:
        qapp.processEvents()
    except Exception:
        pass


@pytest.fixture(scope="session")
def qapp():
    """A headless QApplication for tests that construct Qt objects.

    Skips the whole test when PyQt6 is unavailable, keeping the core suite
    runnable without GUI dependencies.
    """
    QtWidgets = pytest.importorskip("PyQt6.QtWidgets")
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    yield app
