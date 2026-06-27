"""
ui/theme.py
Single source of truth for the app's colour themes.

Every top-level window pulls its colours from here so the whole app stays in
visual unity with whatever theme the user has selected in Settings
(``controller.settings['chat_theme']``).

Layers
------
THEMES               Raw per-theme palette (8 structural keys). Shared with
                     ChatWindow, which historically owned this dict.
resolve_palette()    Expands a raw palette into the full *semantic* palette
                     every window needs (surface2, text, muted, status colours,
                     glow, derived accent shades).
current_palette()    Reads the user's selected theme from controller settings
                     and returns the resolved palette.
*_qss builders       Ready-to-use stylesheet strings built from a resolved
                     palette, so buttons / inputs / checkboxes look identical
                     in every window.
"""

DEFAULT_THEME_KEY = 'obsidian_blue'

# ── Raw palettes (structural surfaces) ────────────────────────────────────────
# Keys: base, surface, elevated, border, accent, deep, input_card,
#       input_card_border.  ChatWindow.apply_theme() consumes these directly.
THEMES = {
    'obsidian_blue': {
        'base':    '#0D1117', 'surface': '#161B22', 'elevated': '#21262D',
        'border':  '#30363D', 'accent':  '#58A6FF', 'deep': '#0D1117',
        'input_card': '#1C2128', 'input_card_border': '#2D333B',
    },
    'onyx': {
        'base':    '#18181B', 'surface': '#1C1C1F', 'elevated': '#27272A',
        'border':  '#3F3F46', 'accent':  '#6366F1', 'deep': '#101013',
        'input_card': '#1C1C20', 'input_card_border': '#3A3A40',
    },
    'carbon': {
        'base':    '#111214', 'surface': '#1E1F22', 'elevated': '#2B2D31',
        'border':  '#3B3D43', 'accent':  '#5865F2', 'deep': '#0B0C0E',
        'input_card': '#1A1B1E', 'input_card_border': '#35373D',
    },
    'midnight_rose': {
        'base':    '#120F1A', 'surface': '#1D1825', 'elevated': '#2A2436',
        'border':  '#3E3556', 'accent':  '#A78BFA', 'deep': '#0C0910',
        'input_card': '#1A1625', 'input_card_border': '#382E50',
    },
    'emerald': {
        'base':    '#0D1210', 'surface': '#131A15', 'elevated': '#1C2B1F',
        'border':  '#274D30', 'accent':  '#3FB950', 'deep': '#090E0B',
        'input_card': '#162019', 'input_card_border': '#243D28',
    },
    'copper': {
        'base':    '#110D09', 'surface': '#1A1310', 'elevated': '#261D17',
        'border':  '#4A3020', 'accent':  '#E8834A', 'deep': '#0D0A07',
        'input_card': '#201812', 'input_card_border': '#3D2818',
    },
    'crimson': {
        'base':    '#120A0A', 'surface': '#1C1010', 'elevated': '#2A1515',
        'border':  '#4D1F1F', 'accent':  '#FF4C4C', 'deep': '#0D0707',
        'input_card': '#201212', 'input_card_border': '#3D1A1A',
    },
    'arctic': {
        'base':    '#0A0E12', 'surface': '#111620', 'elevated': '#192030',
        'border':  '#243348', 'accent':  '#67E8F9', 'deep': '#070B0F',
        'input_card': '#141D2C', 'input_card_border': '#1F2E42',
    },
    'golden': {
        'base':    '#0F0D08', 'surface': '#19160A', 'elevated': '#252010',
        'border':  '#473D18', 'accent':  '#F5C518', 'deep': '#0A0905',
        'input_card': '#1E1A0D', 'input_card_border': '#3A3214',
    },
    'slate': {
        'base':    '#0C0E12', 'surface': '#141820', 'elevated': '#1E2330',
        'border':  '#2C3444', 'accent':  '#94A3B8', 'deep': '#090B0F',
        'input_card': '#181D28', 'input_card_border': '#252D3E',
    },
    # ── Monochrome dark series ────────────────────────────────────────────────
    'void': {
        'base':    '#000000', 'surface': '#090909', 'elevated': '#111111',
        'border':  '#1c1c1c', 'accent':  '#555555', 'deep': '#000000',
        'input_card': '#0d0d0d', 'input_card_border': '#1c1c1c',
    },
    'mono_obsidian': {
        'base':    '#0a0a0a', 'surface': '#101010', 'elevated': '#171717',
        'border':  '#222222', 'accent':  '#666666', 'deep': '#060606',
        'input_card': '#131313', 'input_card_border': '#222222',
    },
    'mono_charcoal': {
        'base':    '#0e0e10', 'surface': '#141416', 'elevated': '#1c1c1f',
        'border':  '#252528', 'accent':  '#606063', 'deep': '#0b0b0d',
        'input_card': '#181819', 'input_card_border': '#252528',
    },
    'ember': {
        'base':    '#0f0d0b', 'surface': '#161310', 'elevated': '#1e1a16',
        'border':  '#2a2520', 'accent':  '#6e665c', 'deep': '#0c0a08',
        'input_card': '#1a1714', 'input_card_border': '#2a2520',
    },
}


# ── Colour math helpers ───────────────────────────────────────────────────────

def _rgb(hex_color: str) -> tuple:
    h = hex_color.lstrip('#')
    if len(h) == 3:
        h = ''.join(c * 2 for c in h)
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def _hex(r: float, g: float, b: float) -> str:
    return '#{:02x}{:02x}{:02x}'.format(
        max(0, min(255, int(round(r)))),
        max(0, min(255, int(round(g)))),
        max(0, min(255, int(round(b)))),
    )


def lighten(hex_color: str, t: float) -> str:
    """Blend ``hex_color`` toward white by fraction t (0..1)."""
    r, g, b = _rgb(hex_color)
    return _hex(r + (255 - r) * t, g + (255 - g) * t, b + (255 - b) * t)


def darken(hex_color: str, t: float) -> str:
    """Blend ``hex_color`` toward black by fraction t (0..1)."""
    r, g, b = _rgb(hex_color)
    return _hex(r * (1 - t), g * (1 - t), b * (1 - t))


def rgba(hex_color: str, alpha: float) -> str:
    """CSS rgba() string from a hex colour + alpha (0..1)."""
    r, g, b = _rgb(hex_color)
    return f"rgba({r},{g},{b},{alpha})"


# ── Palette resolution ────────────────────────────────────────────────────────

# Theme-agnostic colours. Text/muted are kept stable so body copy reads the same
# across every palette; the status colours are semantic (error/ok/warn/info).
_TEXT  = '#E8EFF8'
_MUTED = '#8B949E'
_RED    = '#F0524F'
_GREEN  = '#34D058'
_YELLOW = '#E3B341'
_PURPLE = '#A78BFA'


def resolve_palette(raw: dict) -> dict:
    """Expand a raw 8-key theme into the full semantic palette used by windows."""
    accent = raw.get('accent', '#58A6FF')
    return {
        # structural
        'bg':       raw.get('base', '#0D1117'),
        'surface':  raw.get('surface', '#161B22'),
        'surface2': raw.get('elevated', '#21262D'),
        'border':   raw.get('border', '#30363D'),
        'deep':     raw.get('deep', raw.get('base', '#0D1117')),
        'input_card':        raw.get('input_card', raw.get('elevated', '#1C2128')),
        'input_card_border': raw.get('input_card_border', raw.get('border', '#2D333B')),
        # accent + derived shades
        'accent':    accent,
        'accent_lt': lighten(accent, 0.22),
        'accent_dk': darken(accent, 0.18),
        'glow':      rgba(accent, 0.12),
        # text + semantic
        'text':   _TEXT,
        'muted':  _MUTED,
        'red':    _RED,
        'green':  _GREEN,
        'yellow': _YELLOW,
        'purple': _PURPLE,
    }


def current_key(controller) -> str:
    """The user's selected theme key (falls back to the default)."""
    try:
        key = controller.settings.get('chat_theme', DEFAULT_THEME_KEY)
    except Exception:
        key = DEFAULT_THEME_KEY
    return key if key in THEMES else DEFAULT_THEME_KEY


def current_palette(controller) -> dict:
    """Resolved semantic palette for the user's currently selected theme."""
    return resolve_palette(THEMES[current_key(controller)])


# ── Glass (frosted-translucent) mode ──────────────────────────────────────────

# Windows the glass overlay can be applied to (in display order). The Settings
# window is intentionally excluded — it stays opaque for readability of its
# dense forms. Used by the Settings checklist and each window's _sync_glass().
GLASS_WINDOWS = ['chat', 'manage_tasks', 'debug', 'appearance', 'memory']
GLASS_WINDOW_LABELS = {
    'chat':         'Chat window',
    'manage_tasks': 'Manage Tasks window',
    'debug':        'Debug window',
    'appearance':   'Floating Window Appearance',
    'memory':       'Memory window',
}


def glass_state(controller) -> tuple:
    """(enabled, opacity) for the glass background, read from settings."""
    try:
        s = controller.settings
        return bool(s.get('glass_background_enabled', False)), \
            float(s.get('glass_background_opacity', 0.75))
    except Exception:
        return False, 0.75


def glass_windows(controller) -> list:
    """The list of window keys glass should apply to. Defaults to all eligible
    windows so existing configs keep their current (glass-everywhere) behaviour."""
    try:
        wins = controller.settings.get('glass_windows', None)
        if isinstance(wins, list):
            return [w for w in wins if w in GLASS_WINDOWS]
    except Exception:
        pass
    return list(GLASS_WINDOWS)


def glass_enabled_for(controller, window_key: str) -> bool:
    """True if glass is on AND this specific window is opted in."""
    enabled, _ = glass_state(controller)
    return bool(enabled) and window_key in glass_windows(controller)


def glass_backdrop(opacity: float) -> str:
    """Neutral dark translucent backdrop colour (matches the chat window's glass).
    Darker as opacity rises; the desktop shows through underneath."""
    op = max(0.15, min(0.95, float(opacity)))
    b = int(op * 28)
    return f"rgba({b},{b},{b},{op:.2f})"


def glass_panel(opacity: float) -> str:
    """Frosted near-opaque dark panel for text-bearing surfaces in glass mode
    (sidebar, log views). Stays readable while keeping a faint glassy translucency."""
    op = max(0.15, min(0.95, float(opacity)))
    a = max(0.86, min(0.96, op + 0.18))
    return f"rgba(20,20,22,{a:.2f})"


# ── Reusable QSS builders ─────────────────────────────────────────────────────
# Each takes a resolved palette (from resolve_palette / current_palette) and
# returns a stylesheet string. Windows assign these to widgets directly.

def btn(p: dict) -> str:
    """Neutral secondary button."""
    return f"""
    QPushButton {{
        background: {p['surface2']}; color: {p['text']};
        border: 1px solid {p['border']}; border-radius: 8px;
        padding: 6px 16px; font-size: 12px;
    }}
    QPushButton:hover {{ background: {lighten(p['surface2'], 0.10)};
        border-color: {p['accent']}; color: {p['accent']}; }}
    QPushButton:pressed {{ background: {darken(p['surface2'], 0.25)}; }}
    """


def btn_accent(p: dict) -> str:
    """Primary call-to-action button — accent gradient."""
    return f"""
    QPushButton {{
        background: qlineargradient(x1:0,y1:0,x2:0,y2:1,
            stop:0 {p['accent_lt']}, stop:1 {p['accent']});
        color: {darken(p['accent'], 0.78)};
        border: none; border-radius: 8px;
        padding: 6px 18px; font-size: 12px; font-weight: 700;
    }}
    QPushButton:hover {{ background: qlineargradient(x1:0,y1:0,x2:0,y2:1,
        stop:0 {lighten(p['accent'], 0.38)}, stop:1 {p['accent_lt']}); }}
    QPushButton:pressed {{ background: {p['accent_dk']}; }}
    """


def btn_red(p: dict) -> str:
    return f"""
    QPushButton {{
        background: transparent; color: {p['red']};
        border: 1px solid {rgba(p['red'], 0.45)}; border-radius: 8px;
        padding: 5px 12px; font-size: 11px;
    }}
    QPushButton:hover {{ background: {rgba(p['red'], 0.12)}; border-color: {p['red']}; }}
    """


def btn_ghost(p: dict) -> str:
    return f"""
    QPushButton {{
        background: transparent; color: {p['muted']};
        border: 1px solid {p['border']}; border-radius: 8px;
        padding: 5px 12px; font-size: 11px;
    }}
    QPushButton:hover {{ background: {p['surface2']}; color: {p['text']};
        border-color: {p['accent']}; }}
    """


def input_qss(p: dict) -> str:
    return f"""
    QLineEdit, QTextEdit, QSpinBox, QTimeEdit, QDateTimeEdit {{
        background: {p['surface2']}; color: {p['text']};
        border: 1px solid {p['border']}; border-radius: 8px;
        padding: 7px 12px; font-size: 12px;
    }}
    QLineEdit:focus, QTextEdit:focus, QSpinBox:focus, QTimeEdit:focus, QDateTimeEdit:focus {{
        border-color: {p['accent']}; background: {lighten(p['surface2'], 0.06)};
    }}
    """


def check_qss(p: dict) -> str:
    return f"""
    QCheckBox {{ color: {p['text']}; font-size: 12px; spacing: 8px; }}
    QCheckBox::indicator {{ width: 17px; height: 17px; border-radius: 5px;
        border: 1px solid {p['border']}; background: {p['surface2']}; }}
    QCheckBox::indicator:checked {{
        background: qlineargradient(x1:0,y1:0,x2:1,y2:1,
            stop:0 {p['accent_lt']}, stop:1 {p['accent']});
        border-color: {p['accent']}; }}
    QCheckBox::indicator:hover {{ border-color: {p['accent']}; }}
    """


def scrollbar_qss(p: dict) -> str:
    return f"""
    QScrollBar:vertical {{ background: transparent; width: 12px; margin: 0; }}
    QScrollBar::handle:vertical {{ background: {rgba(p['accent'], 0.30)};
        border-radius: 6px; min-height: 30px; margin: 2px; }}
    QScrollBar::handle:vertical:hover  {{ background: {rgba(p['accent'], 0.50)}; }}
    QScrollBar::handle:vertical:pressed {{ background: {rgba(p['accent'], 0.70)}; }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
    QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; }}
    """
