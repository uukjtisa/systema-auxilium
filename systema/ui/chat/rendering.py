"""
systema/ui/chat/rendering.py
RenderingMixin — markdown / LaTeX / table rendering helpers for ChatWindow.

LaTeX pipeline (2026-07-20 overhaul): hash-keyed memory + disk cache under
data/cache/latex/ plus ONE background render worker. A cache miss never blocks
the GUI thread — _preprocess_latex emits a small placeholder, the worker
renders the PNG (paying the matplotlib import off-thread, once), and
_on_latex_ready refreshes the affected message labels. Session reloads replay
through the same cache, so previously rendered math loads instantly.
"""
import base64
import hashlib
import html as _html_mod
import queue
import threading

import markdown2
from PyQt6.QtCore import QObject, pyqtSignal

from systema.common.logger import _make_logger

log = _make_logger("ChatWindow")


class _LatexSignals(QObject):
    """Worker → GUI bridge: emitted with the cache key once a PNG lands."""
    ready = pyqtSignal(str)


def _render_latex_png(latex_expr: str, display: bool, color: str) -> bytes:
    """Blocking matplotlib mathtext render → tight transparent PNG bytes.
    Runs on the render worker thread (or in tests) — never on the GUI thread."""
    import io
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt

    fig = plt.figure(figsize=(0.01, 0.01))
    fig.patch.set_alpha(0)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_axis_off()
    ax.patch.set_alpha(0)
    fontsize = 15 if display else 12
    ax.text(0.5, 0.5, f'${latex_expr}$',
            fontsize=fontsize, color=color,
            ha='center', va='center', transform=ax.transAxes)
    buf = io.BytesIO()
    # High DPI + CSS max-height downscale = crisp on hi-dpi displays.
    plt.savefig(buf, format='png', dpi=220, bbox_inches='tight',
                transparent=True, pad_inches=0.06, facecolor='none')
    plt.close(fig)
    return buf.getvalue()


class RenderingMixin:
    """Markdown, LaTeX, and table rendering helpers (mixed into ChatWindow)."""

    _LATEX_COLOR = '#E8EAED'   # glyph colour matches body text

    # ── LaTeX cache + async worker ────────────────────────────────────────────

    def _latex_init(self):
        """Lazily create cache/worker state — the mixin has no __init__. Must
        first run on the GUI thread (it does: _preprocess_latex call sites)."""
        if not hasattr(self, '_latex_mem'):
            self._latex_mem = {}          # key -> final html snippet
            self._latex_pending = set()   # keys currently queued on the worker
            self._latex_waiters = {}      # key -> [message_data, ...]
            self._latex_misses = []       # keys missed during ONE preprocess run
            self._latex_q = queue.Queue()
            self._latex_thread = None
            self._latex_sig = _LatexSignals()
            self._latex_sig.ready.connect(self._on_latex_ready)

    def _latex_cache_dir(self):
        # Consolidated cache layout (2026-07-21): data/cache/latex — the old
        # data/latex_cache is auto-migrated in, so previously rendered math
        # from old sessions still hits the cache.
        from systema.common.data_paths import cache_dir
        try:
            return cache_dir('latex', legacy='latex_cache')
        except Exception:
            from pathlib import Path
            from systema import APP_ROOT
            d = Path(APP_ROOT) / 'data' / 'cache' / 'latex'
            d.mkdir(parents=True, exist_ok=True)
            return d

    def _latex_key(self, expr: str, display: bool) -> str:
        raw = f"{expr}|{'d' if display else 'i'}|{self._LATEX_COLOR}"
        return hashlib.sha1(raw.encode('utf-8')).hexdigest()

    def _latex_html(self, b64: str, expr: str, display: bool) -> str:
        """The <img> snippet — LaTeX source rides along in title= so it stays
        copy-inspectable. Returns ONLY the img (no raw-source duplicate)."""
        title = _html_mod.escape(expr, quote=True)
        if display:
            return (
                f'<div style="text-align:center;margin:6px 0;">'
                f'<img src="data:image/png;base64,{b64}" '
                f'style="max-height:52px;" title="{title}">'
                f'</div>'
            )
        return (
            f'<img src="data:image/png;base64,{b64}" '
            f'style="vertical-align:middle;margin:0 1px;max-height:20px;" '
            f'title="{title}">'
        )

    def _latex_lookup(self, expr: str, display: bool):
        """Memory → disk → None. No rendering here — GUI-thread safe."""
        self._latex_init()
        key = self._latex_key(expr, display)
        hit = self._latex_mem.get(key)
        if hit is not None:
            return hit
        png = self._latex_cache_dir() / f"{key}.png"
        try:
            if png.exists():
                b64 = base64.b64encode(png.read_bytes()).decode('utf-8')
                snip = self._latex_html(b64, expr, display)
                self._latex_mem[key] = snip
                return snip
        except Exception:
            pass   # corrupt/unreadable cache file → treat as a miss, re-render
        return None

    def _latex_render_and_store(self, expr: str, display: bool) -> str:
        """Worker-side: render, write the PNG cache, fill the memory cache.
        A failed render caches the escaped-source fallback so it never loops."""
        key = self._latex_key(expr, display)
        try:
            data = _render_latex_png(expr, display, self._LATEX_COLOR)
            try:
                (self._latex_cache_dir() / f"{key}.png").write_bytes(data)
            except Exception:
                pass                        # disk cache is best-effort
            b64 = base64.b64encode(data).decode('utf-8')
            self._latex_mem[key] = self._latex_html(b64, expr, display)
        except Exception:
            log.warning(f"[RenderingMixin._latex_render_and_store] render failed "
                        f"for {expr[:60]!r}", exc_info=True)
            self._latex_mem[key] = f'<code>{_html_mod.escape(expr)}</code>'
        return key

    def _latex_worker_loop(self):
        while True:
            expr, display = self._latex_q.get()
            key = self._latex_render_and_store(expr, display)
            self._latex_pending.discard(key)
            try:
                self._latex_sig.ready.emit(key)   # queued back to the GUI thread
            except RuntimeError:
                return                            # window gone — thread exits

    def _latex_img_or_placeholder(self, expr: str, display: bool) -> str:
        """Cache hit → final snippet. Miss → placeholder now + background
        render; _on_latex_ready refreshes the label. Never blocks the GUI."""
        hit = self._latex_lookup(expr, display)
        if hit is not None:
            return hit
        key = self._latex_key(expr, display)
        self._latex_misses.append(key)
        if key not in self._latex_pending:
            self._latex_pending.add(key)
            self._latex_q.put((expr, display))
            if self._latex_thread is None or not self._latex_thread.is_alive():
                self._latex_thread = threading.Thread(
                    target=self._latex_worker_loop, name='latex-render', daemon=True)
                self._latex_thread.start()
        if display:
            return ('<div style="text-align:center;margin:6px 0;color:#8B949E;">'
                    '⟳ rendering math…</div>')
        return '<span style="color:#8B949E;">⟳ math</span>'

    def _latex_to_base64_img(self, latex_expr, display=True):
        """SYNCHRONOUS render-through-cache. Kept for tests/tooling — the chat
        path goes through _latex_img_or_placeholder (async on miss)."""
        hit = self._latex_lookup(latex_expr, display)
        if hit is not None:
            return hit
        key = self._latex_render_and_store(latex_expr, display)
        return self._latex_mem[key]

    def _take_latex_misses(self) -> list:
        """Keys that MISSED during the last _preprocess_latex run (drained)."""
        self._latex_init()
        out = self._latex_misses
        self._latex_misses = []
        return out

    def _register_latex_waiter(self, keys, message_data):
        """Remember which message(s) are waiting on which pending renders."""
        self._latex_init()
        for k in keys:
            self._latex_waiters.setdefault(k, []).append(message_data)

    def _on_latex_ready(self, key: str):
        """GUI thread: a math render landed in the cache — refresh waiters."""
        mds = self._latex_waiters.pop(key, [])
        for md in mds:
            try:
                self._refresh_message_latex(md)    # BubblesMixin
            except Exception:
                log.warning("[RenderingMixin._on_latex_ready] refresh failed",
                            exc_info=True)

    # ── LaTeX detection / preprocessing ──────────────────────────────────────

    def _looks_like_math(self, s):
        """Heuristic: does the text between single $…$ read as MATH (not money)?
        Currency/plain-number runs ($0.30, $3, $20 billion, $ rate / hour) are
        rejected; a LaTeX command, sub/superscript, or a short symbolic
        expression with a variable is accepted."""
        import re
        s = (s or '').strip()
        if not s:
            return False
        # Pure number / currency / unit runs are NOT math.
        if re.fullmatch(r'[\d.,%\s/×xX+\-]+', s):
            return False
        if re.fullmatch(r'[\d.,\s]+(?:billion|million|thousand|trillion|bn|mn|k|m|b)?',
                        s, re.IGNORECASE):
            return False
        # Positive math signals.
        if re.search(r'\\[a-zA-Z]+', s):            # \frac \int \alpha \sqrt …
            return True
        if re.search(r'[_^{}]', s):                # sub/superscript / groups
            return True
        if re.search(r'[=<>]', s) and re.search(r'[A-Za-z]', s):
            return True                             # an equation with a variable
        if (re.search(r'[A-Za-z]', s) and re.search(r'[+\-*/^]', s)
                and not re.search(r'[A-Za-z]{4,}', s)):
            return True                             # short symbolic expr (x+y), not prose
        return False

    def _preprocess_latex(self, text):
        """Turn LaTeX math into rendered PNGs, adaptively and safely:
          • fenced code, inline `code`, and pipe-table rows are masked first so a
            `$` inside them is never read as math;
          • $$…$$ and \\[…\\] render as centered display math;
          • \\(…\\) renders inline; $…$ renders inline ONLY when it actually looks
            like math — currency ($0.30, $3, $20 billion) stays literal text;
          • \\$ is a literal dollar sign.
        Masks are restored verbatim before returning (they never reach markdown2).
        Cache misses insert a placeholder and render in the background — collect
        them via _take_latex_misses() right after calling this."""
        import re

        # ── 1. Mask code / tables so their $ can't be mistaken for math ────────
        _stash = []
        def _mask(m):
            _stash.append(m.group(0))
            return f'\x00LX{len(_stash) - 1}\x00'
        text = re.sub(r'```.*?```', _mask, text, flags=re.DOTALL)   # fenced code
        text = re.sub(r'`[^`\n]+`', _mask, text)                    # inline code
        text = re.sub(r'^[ \t]*\|.*\|[ \t]*$', _mask, text,         # table rows
                      flags=re.MULTILINE)

        # ── 2. Escaped dollar → sentinel (restored to a literal $ at the end) ──
        text = text.replace(r'\$', '\x00USD\x00')

        # ── 3. Display math: $$…$$ and \[…\] ──────────────────────────────────
        def _display(m):
            return self._latex_img_or_placeholder(m.group(1).strip(), True)
        text = re.sub(r'\$\$(.+?)\$\$', _display, text, flags=re.DOTALL)
        text = re.sub(r'\\\[(.+?)\\\]', _display, text, flags=re.DOTALL)

        # ── 4. Inline: \(…\) always; $…$ only when it looks like math ──────────
        def _inline(m):
            return self._latex_img_or_placeholder(m.group(1).strip(), False)
        text = re.sub(r'\\\((.+?)\\\)', _inline, text, flags=re.DOTALL)

        def _inline_dollar(m):
            inner = m.group(1)
            if self._looks_like_math(inner):
                return self._latex_img_or_placeholder(inner.strip(), False)
            return m.group(0)   # currency / prose — leave untouched
        # Single $…$ on one line. Opening $ not preceded by a word char and not
        # followed by whitespace/digit (kills $0.30 / $3); closing $ not followed
        # by a word char. Bounded length so it can't run across a paragraph.
        text = re.sub(r'(?<![\w$])\$(?![\s\d$])([^$\n]{1,120}?)\$(?![\w$])',
                      _inline_dollar, text)

        # ── 5. Loose bracketed display math: a whole line like [ … \frac … ] ──
        def _bracket(m):
            inner = m.group(1).strip()
            if re.search(r'\\[a-zA-Z]+|[_^{}]|\bfrac\b|\bsum\b|\bint\b', inner):
                return self._latex_img_or_placeholder(inner, True)
            return m.group(0)
        text = re.sub(r'^\[\s*(.+?)\s*\]\s*$', _bracket, text, flags=re.MULTILINE)

        # ── 6. Restore literal $ and masked code / tables ─────────────────────
        # Loop until stable: a table row masked AFTER its inline-code spans holds
        # nested \x00LXn\x00 sentinels in its stashed text, so a single re.sub
        # pass would re-insert them verbatim (the rendered-\x00LXn\x00 bug).
        text = text.replace('\x00USD\x00', '$')
        for _ in range(10):                     # bounded — nesting is ever ≤2 deep
            if '\x00LX' not in text:
                break
            text = re.sub(r'\x00LX(\d+)\x00',
                          lambda m: _stash[int(m.group(1))], text)
        return text

    # ── Markdown ──────────────────────────────────────────────────────────────

    def render_markdown(self, text):
        """Render markdown to HTML"""
        try:
            html = markdown2.markdown(text, extras=["fenced-code-blocks", "tables", "break-on-newline"])
            return self._cap_heading_sizes(html)
        except Exception:
            return text.replace('\n', '<br>')

    def _cap_heading_sizes(self, html):
        """Qt's rich-text engine renders <h1>-<h6> at its own large built-in
        sizes — a message QLabel's internal document takes no stylesheet, so a
        '# heading' line in a reply towers over the bubble text. Rewrite heading
        tags into bold blocks sized relative to the chat font scale (still
        bigger than body text, just proportionate). Code blocks are safe: their
        literal '<h1>' is already HTML-escaped by markdown2."""
        import re
        try:
            base = int(self._get_msg_font_size())
        except Exception:
            base = 13
        _bump = {1: 5, 2: 3, 3: 2, 4: 1, 5: 0, 6: 0}

        def _open(m):
            lvl = int(m.group(1))
            return (f'<div style="font-size:{base + _bump.get(lvl, 0)}px; '
                    f'font-weight:700; margin-top:8px; margin-bottom:3px;">')
        html = re.sub(r'<h([1-6])(?:\s[^>]*)?>', _open, html)
        html = re.sub(r'</h[1-6]>', '</div>', html)
        return html

    def render_markdown_with_code_blocks(self, text):
        """Render markdown with special handling for code blocks"""
        import re

        parts = []
        last_end = 0

        code_pattern = r'```(\w+)?\n(.*?)```'

        for match in re.finditer(code_pattern, text, re.DOTALL):
            if match.start() > last_end:
                before_text = text[last_end:match.start()]
                if before_text.strip():
                    parts.append(('text', before_text))

            language = match.group(1) or 'text'
            code_content = match.group(2)
            parts.append(('code', language, code_content))

            last_end = match.end()

        if last_end < len(text):
            remaining = text[last_end:]
            if remaining.strip():
                parts.append(('text', remaining))

        if not any(p[0] == 'code' for p in parts):
            return self.render_markdown(text)

        return parts

    def _is_table_separator(self, line):
        """True if `line` is a markdown table separator row (e.g. |---|:--:|)."""
        import re
        s = line.strip()
        if '-' not in s or '|' not in s:
            return False
        cells = [c.strip() for c in s.strip('|').split('|')]
        cells = [c for c in cells if c != '']
        if not cells:
            return False
        return all(re.fullmatch(r':?-+:?', c) for c in cells)

    def _split_text_and_tables(self, md):
        """Split a markdown chunk into ordered ('text', md) / ('table', md) parts,
        pulling out pipe-tables so each can render in its own scrollable widget."""
        lines = md.split('\n')
        out, buf = [], []
        n = len(lines)

        def flush():
            if buf:
                chunk = '\n'.join(buf)
                if chunk.strip():
                    out.append(('text', chunk))
                buf.clear()

        i = 0
        while i < n:
            # A table = a row containing '|' immediately followed by a separator row.
            if '|' in lines[i] and i + 1 < n and self._is_table_separator(lines[i + 1]):
                start = i
                j = i + 2
                while j < n and lines[j].strip() and '|' in lines[j]:
                    j += 1
                flush()
                out.append(('table', '\n'.join(lines[start:j])))
                i = j
            else:
                buf.append(lines[i])
                i += 1
        flush()
        return out

    def _split_message_parts(self, text):
        """Split a message into ordered ('text', md) / ('code', lang, code) /
        ('table', md) parts. Code and tables are pulled out of the prose so each
        renders in its own scrollable widget. Always returns a list. Tables
        inside code fences are NOT extracted (code is split out first)."""
        import re
        parts = []
        last_end = 0
        for match in re.finditer(r'```(\w+)?\n(.*?)```', text, re.DOTALL):
            if match.start() > last_end:
                parts.extend(self._split_text_and_tables(text[last_end:match.start()]))
            parts.append(('code', match.group(1) or 'text', match.group(2)))
            last_end = match.end()
        if last_end < len(text):
            parts.extend(self._split_text_and_tables(text[last_end:]))
        return parts
