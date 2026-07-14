"""
Tests for systema/execution/file_tools.py

Covers the `grep` search tool (all three output modes, filters, context,
only-matching, multiline, head-limit, and error paths) plus the read/edit
helpers that back read_file / edit_file. Pure stdlib — no PyQt6.
"""
from systema.execution import file_tools as ft


# ── grep: output modes ───────────────────────────────────────────────────────

def test_grep_files_with_matches_default(sample_tree):
    out = ft.grep("TODO", str(sample_tree))
    assert "src/a.py" in out
    assert "src/b.js" in out
    # notes.txt only has lowercase "todo" — case-sensitive search misses it.
    assert "notes.txt" not in out
    # default mode lists paths, never line content.
    assert "def send" not in out


def test_grep_content_mode_line_numbers(sample_tree):
    out = ft.grep("TODO", str(sample_tree), output_mode="content", type="py")
    assert "src/a.py:2:# TODO alpha" in out
    assert "src/a.py:5:# TODO beta" in out


def test_grep_content_no_line_numbers(sample_tree):
    out = ft.grep("TODO", str(sample_tree), output_mode="content",
                  type="py", line_numbers=False)
    assert "src/a.py:# TODO alpha" in out
    assert ":2:" not in out


def test_grep_count_mode(sample_tree):
    out = ft.grep("TODO", str(sample_tree), output_mode="count")
    assert "src/a.py:2" in out
    assert "src/b.js:1" in out


# ── grep: file filters ───────────────────────────────────────────────────────

def test_grep_glob_filter(sample_tree):
    out = ft.grep("TODO", str(sample_tree), glob="**/*.js")
    assert "b.js" in out
    assert "a.py" not in out


def test_grep_type_filter(sample_tree):
    out = ft.grep("send", str(sample_tree), output_mode="content", type="js")
    assert "b.js" in out
    assert "a.py" not in out


def test_grep_unknown_type_errors(sample_tree):
    out = ft.grep("x", str(sample_tree), type="zzz")
    assert out.startswith("ERROR:")
    assert "unknown type" in out


# ── grep: ignore_common (the default-on switch the user asked for) ───────────

def test_grep_ignore_common_prunes_build_dirs(sample_tree):
    out = ft.grep("TODO", str(sample_tree))          # ignore_common=True default
    assert ".venv" not in out


def test_grep_ignore_common_false_searches_everything(sample_tree):
    out = ft.grep("TODO", str(sample_tree), ignore_common=False)
    assert ".venv" in out


# ── grep: context, only-matching, multiline ──────────────────────────────────

def test_grep_after_context(sample_tree):
    out = ft.grep("# TODO alpha", str(sample_tree), output_mode="content",
                  type="py", after=1)
    assert "src/a.py:2:# TODO alpha" in out            # match line, ':' sep
    assert "src/a.py-3-def send(msg):" in out          # context line, '-' sep


def test_grep_context_both_sides(sample_tree):
    out = ft.grep("def send", str(sample_tree), output_mode="content",
                  type="py", context=1)
    assert "src/a.py-2-# TODO alpha" in out
    assert "src/a.py:3:def send(msg):" in out
    assert "src/a.py-4-    return msg" in out


def test_grep_only_matching(sample_tree):
    out = ft.grep(r"TODO \w+", str(sample_tree), output_mode="content",
                  type="py", only_matching=True)
    assert "src/a.py:2:TODO alpha" in out
    # only the matched span is printed — the leading "# " is dropped.
    assert "# TODO" not in out


def test_grep_multiline(sample_tree):
    out = ft.grep(r"import os\n# TODO", str(sample_tree), output_mode="content",
                  type="py", multiline=True)
    assert "src/a.py" in out


# ── grep: case-insensitivity, head-limit, error paths ────────────────────────

def test_grep_case_insensitive(sample_tree):
    hit = ft.grep("todo", str(sample_tree), case_insensitive=True)
    assert "src/a.py" in hit
    miss = ft.grep("todo", str(sample_tree))           # case-sensitive
    assert "src/a.py" not in miss


def test_grep_head_limit_caps_output(sample_tree):
    out = ft.grep("TODO", str(sample_tree), output_mode="content", head_limit=1)
    body = [ln for ln in out.splitlines()[1:] if ln.strip()]
    assert len(body) == 1
    assert "capped" in out


def test_grep_invalid_regex_errors(sample_tree):
    out = ft.grep("(unclosed", str(sample_tree))
    assert out.startswith("ERROR:")
    assert "invalid regex" in out


def test_grep_missing_path_errors():
    out = ft.grep("x", "no/such/path/xyz-123")
    assert out.startswith("ERROR:")
    assert "not found" in out


def test_grep_no_matches_returns_header_only(sample_tree):
    out = ft.grep("zzz-not-present-anywhere", str(sample_tree))
    assert out.startswith("grep")
    assert "0 file(s) with matches" in out


# ── read_file / edit helpers ─────────────────────────────────────────────────

def test_read_file_window(sample_tree):
    out = ft.read_file(str(sample_tree / "src" / "a.py"), start_line=1, max_lines=2)
    assert "FILE:" in out
    assert "1\timport os" in out           # numbered line body
    assert "def send" not in out           # window stops at 2 lines


def test_read_file_missing():
    out = ft.read_file("nope/does-not-exist.py")
    assert out.startswith("ERROR:")


def test_prepare_edit_anchor(sample_tree):
    f = sample_tree / "src" / "a.py"
    new, err, stats = ft.prepare_edit(str(f), "def send(msg):", "def send(message):")
    assert err is None
    assert "def send(message):" in new


def test_prepare_edit_ambiguous(sample_tree):
    f = sample_tree / "src" / "a.py"
    new, err, stats = ft.prepare_edit(str(f), "# TODO", "# DONE")
    assert new is None
    assert "matches 2" in err


def test_prepare_edit_not_found(sample_tree):
    f = sample_tree / "src" / "a.py"
    new, err, stats = ft.prepare_edit(str(f), "not in the file", "x")
    assert new is None
    assert "not found" in err


def test_prepare_edit_lines(sample_tree):
    f = sample_tree / "src" / "a.py"
    new, err, stats = ft.prepare_edit_lines(str(f), 1, 1, "import sys")
    assert err is None
    assert new.startswith("import sys")


# ── edit_file whitespace tolerance (D4) ──────────────────────────────────────

def test_prepare_edit_tolerates_trailing_whitespace(tmp_path):
    f = tmp_path / "t.py"
    f.write_text("def g():\n    return 1\n", encoding="utf-8")
    # anchor has trailing spaces the file doesn't have
    new, err, stats = ft.prepare_edit(str(f), "    return 1   ", "    return 2")
    assert err is None
    assert "return 2" in new


def test_prepare_edit_tolerates_leading_indent(tmp_path):
    f = tmp_path / "t.py"
    f.write_text("def g():\n        return 1\n", encoding="utf-8")  # 8-space indent
    # anchor uses 4-space indent — should still match via normalization
    new, err, stats = ft.prepare_edit(str(f), "    return 1", "    return 99")
    assert err is None
    assert "return 99" in new


def test_prepare_edit_ws_ambiguous_still_errors(tmp_path):
    f = tmp_path / "t.py"
    f.write_text("x = 1  \ny = 2\nx = 1\n", encoding="utf-8")
    new, err, stats = ft.prepare_edit(str(f), "x = 1", "x = 3")
    # exact "x = 1" appears twice (well, once exact + one with trailing spaces);
    # normalized it is ambiguous → error, not a silent wrong edit
    assert new is None
    assert err is not None


def test_prepare_edit_absent_anchor_still_errors(tmp_path):
    f = tmp_path / "t.py"
    f.write_text("a = 1\n", encoding="utf-8")
    new, err, stats = ft.prepare_edit(str(f), "totally different", "x")
    assert new is None
    assert "not found" in err
