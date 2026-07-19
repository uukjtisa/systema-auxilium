"""
systema/ui/chat/rendering.py
RenderingMixin — markdown / LaTeX / table rendering helpers for ChatWindow.
Extracted verbatim from chat_window.py.
"""
import markdown2


class RenderingMixin:
    """Markdown, LaTeX, and table rendering helpers (mixed into ChatWindow)."""

    def _latex_to_base64_img(self, latex_expr, display=True):
        """Render a LaTeX expression to a tight, transparent base64 PNG via
        matplotlib mathtext. Returns ONLY the <img> — no raw-source duplicate;
        the LaTeX source rides along in title= so it stays copy-inspectable.
        Glyph color matches body text (#E8EAED) so formulas read like prose."""
        try:
            import matplotlib
            matplotlib.use('Agg')
            import matplotlib.pyplot as plt
            import io, base64, html as _html

            fig = plt.figure(figsize=(0.01, 0.01))
            fig.patch.set_alpha(0)
            ax = fig.add_axes([0, 0, 1, 1])
            ax.set_axis_off()
            ax.patch.set_alpha(0)

            fontsize = 15 if display else 12
            ax.text(0.5, 0.5, f'${latex_expr}$',
                    fontsize=fontsize, color='#E8EAED',
                    ha='center', va='center',
                    transform=ax.transAxes)

            buf = io.BytesIO()
            # High DPI + CSS max-height downscale = crisp on hi-dpi displays.
            plt.savefig(buf, format='png', dpi=220, bbox_inches='tight',
                        transparent=True, pad_inches=0.06, facecolor='none')
            plt.close(fig)
            buf.seek(0)
            b64 = base64.b64encode(buf.read()).decode('utf-8')
            title = _html.escape(latex_expr, quote=True)

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
        except Exception:
            import html as _html
            return f'<code>{_html.escape(latex_expr)}</code>'

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
        Masks are restored verbatim before returning (they never reach markdown2)."""
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
            return self._latex_to_base64_img(m.group(1).strip(), display=True)
        text = re.sub(r'\$\$(.+?)\$\$', _display, text, flags=re.DOTALL)
        text = re.sub(r'\\\[(.+?)\\\]', _display, text, flags=re.DOTALL)

        # ── 4. Inline: \(…\) always; $…$ only when it looks like math ──────────
        def _inline(m):
            return self._latex_to_base64_img(m.group(1).strip(), display=False)
        text = re.sub(r'\\\((.+?)\\\)', _inline, text, flags=re.DOTALL)

        def _inline_dollar(m):
            inner = m.group(1)
            if self._looks_like_math(inner):
                return self._latex_to_base64_img(inner.strip(), display=False)
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
                return self._latex_to_base64_img(inner, display=True)
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

