"""
tests/systema/common/test_path_anchoring.py

APP CODE MUST NEVER DEPEND ON THE CURRENT WORKING DIRECTORY.

Agent-authored code inside python_interpreter is free to `os.chdir()` — that is
a normal thing for it to do. Which means any app-side filesystem call written
against a RELATIVE path silently follows the agent around: a config, cache or
log ends up wherever the interpreter happened to be, creating folders in places
the app has never written before.

The rule (already in the project guidance) is that every path is anchored to `APP_ROOT`.
This test enforces it mechanically for the whole `systema/` package, so the next
relative `open("thing.json", "w")` fails here instead of scattering files across
the user's disk months later.

Companion protection for the AGENT's own code lives in
`code_guard.annotate_relative_paths`, which tells the user where a relative
write will actually land before they approve it.
"""
import ast
from pathlib import Path

import pytest

import systema


PACKAGE = Path(systema.__file__).resolve().parent

# Only forms where argument 0 REALLY is a path.
#
#   open("x")                    -> yes
#   os.remove("x") / shutil.*    -> yes  (module-qualified only)
#   Path("x")                    -> yes  (the constructor is where a relative
#                                         path enters; every p.write_text(...)
#                                         downstream inherits it)
#
# Deliberately NOT matched: bound methods like p.write_text(data) — argument 0
# there is CONTENT, not a path — and same-named string methods (str.replace).
_BARE_CALLS = {"open"}
_PATH_MODULES = {"os", "shutil", "os.path"}
_MODULE_CALLS = {
    "mkdir", "makedirs", "removedirs", "remove", "unlink", "rmdir", "rmtree",
    "copy", "copy2", "copytree", "move", "rename", "replace",
}
_PATH_CTORS = {"Path", "PurePath", "PosixPath", "WindowsPath"}
# Names that mark an anchored expression.
_ANCHORS = ("APP_ROOT", "_APP_ROOT", "ROOT", "_ROOT", "CONFIG_FILE", "DATA_DIR",
            "CACHE_DIR", "_SEC_DIR", "_FILE", "cache_dir", "tempfile",
            "gettempdir", "mkdtemp", "mkstemp")


def _sources():
    return sorted(p for p in PACKAGE.rglob("*.py") if "__pycache__" not in p.parts)


def _is_anchored(node) -> bool:
    """True when the expression visibly derives from an anchored base."""
    for sub in ast.walk(node):
        if isinstance(sub, ast.Name) and any(a in sub.id for a in _ANCHORS):
            return True
        if isinstance(sub, ast.Attribute) and any(a in sub.attr for a in _ANCHORS):
            return True
    return False


def _offenders(path: Path):
    """Relative-literal filesystem calls in one module."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except SyntaxError:
        return []
    out = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not node.args:
            continue
        func = node.func
        if isinstance(func, ast.Attribute):
            owner = func.value
            owner_name = (owner.id if isinstance(owner, ast.Name)
                          else getattr(owner, "attr", ""))
            name = func.attr
            takes_path = (owner_name in _PATH_MODULES and name in _MODULE_CALLS)
        else:
            name = getattr(func, "id", "")
            takes_path = name in _BARE_CALLS or name in _PATH_CTORS
        if not takes_path:
            continue
        arg = node.args[0]
        # Only a bare string LITERAL can be judged statically. A variable may
        # well hold an absolute path built elsewhere; those are out of scope.
        if not (isinstance(arg, ast.Constant) and isinstance(arg.value, str)):
            continue
        value = arg.value
        if not value or value.startswith(("/", "\\")) or (len(value) > 1 and value[1] == ":"):
            continue        # absolute
        if _is_anchored(node):
            continue
        try:
            where = path.relative_to(PACKAGE.parent)
        except ValueError:
            where = path                    # outside the package (self-tests)
        out.append(f"{where}:{node.lineno} "
                   f"{name}({value!r}) is relative to the CURRENT DIRECTORY")
    return out


@pytest.mark.parametrize("source", _sources(), ids=lambda p: p.name)
def test_no_module_opens_a_cwd_relative_path(source):
    """Every filesystem call in systema/ must anchor to APP_ROOT (or a temp
    dir), never to whatever directory the process happens to be in."""
    offenders = _offenders(source)
    assert offenders == [], (
        "CWD-dependent path(s) — anchor these to APP_ROOT:\n  "
        + "\n  ".join(offenders))


def test_the_checker_actually_catches_a_relative_open(tmp_path):
    """Guard the guard: without this, the sweep above could silently pass by
    never matching anything."""
    bad = tmp_path / "bad_module.py"
    bad.write_text("open('scratch.json', 'w')\n", encoding="utf-8")

    found = _offenders(bad)

    assert found and "scratch.json" in found[0]


def test_the_checker_accepts_an_anchored_path(tmp_path):
    good = tmp_path / "good_module.py"
    good.write_text("open(APP_ROOT / 'data' / 'x.json', 'w')\n", encoding="utf-8")
    assert _offenders(good) == []


def test_the_checker_ignores_absolute_literals(tmp_path):
    mod = tmp_path / "abs_module.py"
    mod.write_text("open('/etc/hosts')\nopen('C:/tmp/x.txt')\n", encoding="utf-8")
    assert _offenders(mod) == []


def test_the_package_is_actually_being_scanned():
    """A typo in the glob would make every parametrized case vacuously pass."""
    assert len(_sources()) > 50
