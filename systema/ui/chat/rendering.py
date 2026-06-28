"""
systema/ui/chat/rendering.py
RenderingMixin — markdown / LaTeX / table rendering helpers for ChatWindow.
Extracted verbatim from chat_window.py.
"""
import markdown2


class RenderingMixin:
    """Markdown, LaTeX, and table rendering helpers (mixed into ChatWindow)."""

    def _latex_to_base64_img(self, latex_expr, display=True):
        """Render a LaTeX expression to a tight base64-encoded PNG using matplotlib."""
        try:
            import matplotlib
            matplotlib.use('Agg')
            import matplotlib.pyplot as plt
            import io, base64

            fig = plt.figure(figsize=(0.01, 0.01))
            fig.patch.set_alpha(0)
            ax = fig.add_axes([0, 0, 1, 1])
            ax.set_axis_off()
            ax.patch.set_alpha(0)

            fontsize = 13 if display else 11
            ax.text(0.5, 0.5, f'${latex_expr}$',
                    fontsize=fontsize, color='#BDC1C6',
                    ha='center', va='center',
                    transform=ax.transAxes)

            buf = io.BytesIO()
            plt.savefig(buf, format='png', dpi=150, bbox_inches='tight',
                        transparent=True, pad_inches=0.05, facecolor='none')
            plt.close(fig)
            buf.seek(0)
            b64 = base64.b64encode(buf.read()).decode('utf-8')

            source_html = (
                f'<span style="font-family:monospace;font-size:9px;'
                f'color:#8B949E;user-select:text;">{latex_expr}</span>'
            )

            if display:
                return (
                    f'<br>'
                    f'<div style="text-align:center;margin:4px 0 2px 0;">'
                    f'<img src="data:image/png;base64,{b64}" style="max-height:40px;">'
                    f'</div>'
                    f'<div style="text-align:center;margin:0 0 4px 0;">{source_html}</div>'
                    f'<br>'
                )
            else:
                return (
                    f'<img src="data:image/png;base64,{b64}" '
                    f'style="vertical-align:middle;margin:0 2px;max-height:22px;">'
                    f'&nbsp;{source_html}'
                )
        except Exception:
            return f'<code>{latex_expr}</code>'

    def _preprocess_latex(self, text):
        """Detect and replace LaTeX math expressions with rendered PNG images."""
        import re

        def replace_display(m):
            return self._latex_to_base64_img(m.group(1).strip(), display=True)
        text = re.sub(r'\\\[(.*?)\\\]', replace_display, text, flags=re.DOTALL)

        def replace_inline(m):
            return self._latex_to_base64_img(m.group(1).strip(), display=False)
        text = re.sub(r'(?<!\$)\$(?!\$)(.+?)(?<!\$)\$(?!\$)', replace_inline, text)

        def replace_bracket(m):
            inner = m.group(1).strip()
            has_latex = bool(re.search(r'\\[a-zA-Z]+|[_^{}]|\bfrac\b|\bsum\b|\bint\b', inner))
            if has_latex:
                return self._latex_to_base64_img(inner, display=True)
            return m.group(0)
        text = re.sub(r'^\[\s*(.+?)\s*\]\s*$', replace_bracket, text, flags=re.MULTILINE)

        return text

    def render_markdown(self, text):
        """Render markdown to HTML"""
        try:
            html = markdown2.markdown(text, extras=["fenced-code-blocks", "tables", "break-on-newline"])
            return html
        except Exception:
            return text.replace('\n', '<br>')

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

