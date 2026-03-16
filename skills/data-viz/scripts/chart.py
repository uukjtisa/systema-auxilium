"""
data-viz chart.py
Main chart generation engine.
After exec'ing this file, call make_chart(config) with your config dict.
"""

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import numpy as np
import os

# ── Default palette (Anthropic-inspired) ──────────────────────
DEFAULT_PALETTE = [
    "#d97757", "#6a9bcc", "#788c5d",
    "#b0aea5", "#c4956a", "#7a9e9f",
    "#e8c99a", "#8b7355"
]

LIGHT_BG   = "#faf9f5"
DARK_BG    = "#1a1a1a"
LIGHT_TEXT = "#141413"
DARK_TEXT  = "#e8e6dc"
GRID_COLOR = "#e8e6dc"
DARK_GRID  = "#2e2e2e"

# ── Helpers ───────────────────────────────────────────────────
def _apply_style(ax, fig, style, title, xlabel, ylabel):
    is_dark = style == "dark"
    bg   = DARK_BG   if is_dark else LIGHT_BG
    txt  = DARK_TEXT if is_dark else LIGHT_TEXT
    grid = DARK_GRID if is_dark else GRID_COLOR

    fig.patch.set_facecolor(bg)
    ax.set_facecolor(bg)

    ax.set_title(title, color=txt, fontsize=15, fontweight="bold", pad=16)
    if xlabel: ax.set_xlabel(xlabel, color=txt, fontsize=11)
    if ylabel: ax.set_ylabel(ylabel, color=txt, fontsize=11)

    ax.tick_params(colors=txt, labelsize=10)
    for spine in ax.spines.values():
        spine.set_color(grid)

    ax.yaxis.grid(True, color=grid, linewidth=0.6, linestyle="--", alpha=0.7)
    ax.set_axisbelow(True)
    ax.xaxis.grid(False)


def _get_colors(config, n):
    if "palette" in config:
        p = config["palette"]
        return [p[i % len(p)] for i in range(n)]
    if "color" in config:
        return [config["color"]] * n
    return [DEFAULT_PALETTE[i % len(DEFAULT_PALETTE)] for i in range(n)]


def _save(fig, output):
    os.makedirs(os.path.dirname(os.path.abspath(output)), exist_ok=True)
    fig.savefig(output, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"Saved: {output}")


# ── Chart builders ────────────────────────────────────────────
def _bar(config):
    data   = config["data"]
    style  = config.get("style", "light")
    fw, fh = config.get("figsize", [10, 6])
    fig, ax = plt.subplots(figsize=(fw, fh))

    if isinstance(data, dict) and not any(isinstance(v, list) for v in data.values()):
        # Simple dict: {"A": 10, "B": 20}
        labels = list(data.keys())
        values = list(data.values())
        colors = _get_colors(config, len(labels))
        bars = ax.bar(labels, values, color=colors, edgecolor="none", width=0.6)
        for bar, val in zip(bars, values):
            ax.text(bar.get_x() + bar.get_width()/2, bar.get_height() + max(values)*0.01,
                    f"{val:,.1f}" if isinstance(val, float) else str(val),
                    ha="center", va="bottom", fontsize=9,
                    color=DARK_TEXT if style=="dark" else LIGHT_TEXT)
    else:
        # Multi-series: {"Series1": [1,2,3], "Series2": [4,5,6]}
        series = {k: v for k, v in data.items() if k not in ("x",)}
        keys   = list(series.keys())
        colors = _get_colors(config, len(keys))
        x      = np.arange(len(list(series.values())[0]))
        width  = 0.8 / len(keys)
        for i, (name, vals) in enumerate(series.items()):
            offset = (i - len(keys)/2 + 0.5) * width
            ax.bar(x + offset, vals, width=width*0.9,
                   color=colors[i], label=name, edgecolor="none")
        ax.set_xticks(x)
        ax.legend(facecolor=DARK_BG if style=="dark" else LIGHT_BG,
                  labelcolor=DARK_TEXT if style=="dark" else LIGHT_TEXT,
                  edgecolor="none")

    _apply_style(ax, fig, style, config.get("title","Chart"),
                 config.get("xlabel",""), config.get("ylabel",""))
    _save(fig, config["output"])


def _line(config):
    data   = config["data"]
    style  = config.get("style", "light")
    fw, fh = config.get("figsize", [10, 6])
    fig, ax = plt.subplots(figsize=(fw, fh))

    if "x" in data and "y" in data:
        color = _get_colors(config, 1)[0]
        ax.plot(data["x"], data["y"], color=color, linewidth=2.5, marker="o",
                markersize=5, markerfacecolor="white", markeredgewidth=1.5)
    else:
        keys   = [k for k in data if k != "x"]
        colors = _get_colors(config, len(keys))
        x_vals = data.get("x", list(range(len(list(data.values())[0]))))
        for i, key in enumerate(keys):
            ax.plot(x_vals, data[key], color=colors[i], linewidth=2.5,
                    label=key, marker="o", markersize=4,
                    markerfacecolor="white", markeredgewidth=1.5)
        ax.legend(facecolor=DARK_BG if style=="dark" else LIGHT_BG,
                  labelcolor=DARK_TEXT if style=="dark" else LIGHT_TEXT,
                  edgecolor="none")

    _apply_style(ax, fig, style, config.get("title","Chart"),
                 config.get("xlabel",""), config.get("ylabel",""))
    _save(fig, config["output"])


def _pie(config):
    data   = config["data"]
    style  = config.get("style", "light")
    fw, fh = config.get("figsize", [8, 8])
    fig, ax = plt.subplots(figsize=(fw, fh))

    labels = list(data.keys())
    values = list(data.values())
    colors = _get_colors(config, len(labels))
    bg     = DARK_BG if style=="dark" else LIGHT_BG
    txt    = DARK_TEXT if style=="dark" else LIGHT_TEXT

    wedges, texts, autotexts = ax.pie(
        values, labels=labels, colors=colors,
        autopct="%1.1f%%", startangle=90,
        pctdistance=0.82, wedgeprops={"edgecolor": bg, "linewidth": 2}
    )
    for t in texts:    t.set_color(txt)
    for t in autotexts: t.set_color(txt); t.set_fontsize(9)

    fig.patch.set_facecolor(bg)
    ax.set_facecolor(bg)
    ax.set_title(config.get("title",""), color=txt, fontsize=15,
                 fontweight="bold", pad=16)
    _save(fig, config["output"])


def _scatter(config):
    data   = config["data"]
    style  = config.get("style", "light")
    fw, fh = config.get("figsize", [8, 8])
    fig, ax = plt.subplots(figsize=(fw, fh))
    color  = _get_colors(config, 1)[0]

    ax.scatter(data["x"], data["y"], color=color, alpha=0.7,
               s=60, edgecolors="none")
    _apply_style(ax, fig, style, config.get("title","Scatter"),
                 config.get("xlabel",""), config.get("ylabel",""))
    _save(fig, config["output"])


def _area(config):
    data   = config["data"]
    style  = config.get("style", "light")
    fw, fh = config.get("figsize", [10, 6])
    fig, ax = plt.subplots(figsize=(fw, fh))

    if "x" in data and "y" in data:
        color = _get_colors(config, 1)[0]
        ax.fill_between(data["x"], data["y"], alpha=0.4, color=color)
        ax.plot(data["x"], data["y"], color=color, linewidth=2)
    else:
        keys   = [k for k in data if k != "x"]
        colors = _get_colors(config, len(keys))
        x_vals = data.get("x", list(range(len(list(data.values())[0]))))
        for i, key in enumerate(keys):
            ax.fill_between(x_vals, data[key], alpha=0.3, color=colors[i], label=key)
            ax.plot(x_vals, data[key], color=colors[i], linewidth=2)
        ax.legend(facecolor=DARK_BG if style=="dark" else LIGHT_BG,
                  labelcolor=DARK_TEXT if style=="dark" else LIGHT_TEXT,
                  edgecolor="none")

    _apply_style(ax, fig, style, config.get("title","Area Chart"),
                 config.get("xlabel",""), config.get("ylabel",""))
    _save(fig, config["output"])


def _histogram(config):
    data   = config["data"]
    style  = config.get("style", "light")
    fw, fh = config.get("figsize", [10, 6])
    fig, ax = plt.subplots(figsize=(fw, fh))

    values = list(data.values())[0] if isinstance(data, dict) else data
    color  = _get_colors(config, 1)[0]
    bins   = config.get("bins", "auto")

    ax.hist(values, bins=bins, color=color, edgecolor="none", alpha=0.85)
    _apply_style(ax, fig, style, config.get("title","Distribution"),
                 config.get("xlabel","Value"), config.get("ylabel","Frequency"))
    _save(fig, config["output"])


def _heatmap(config):
    import pandas as pd
    data   = config["data"]
    style  = config.get("style", "light")
    fw, fh = config.get("figsize", [10, 8])
    fig, ax = plt.subplots(figsize=(fw, fh))

    df  = pd.DataFrame(data)
    mat = df.values
    bg  = DARK_BG if style=="dark" else LIGHT_BG
    txt = DARK_TEXT if style=="dark" else LIGHT_TEXT

    cmap = "YlOrRd" if style=="light" else "plasma"
    im = ax.imshow(mat, cmap=cmap, aspect="auto")
    plt.colorbar(im, ax=ax)

    ax.set_xticks(range(len(df.columns))); ax.set_xticklabels(df.columns, rotation=45, ha="right")
    ax.set_yticks(range(len(df.index)));   ax.set_yticklabels(df.index)

    for i in range(mat.shape[0]):
        for j in range(mat.shape[1]):
            ax.text(j, i, f"{mat[i,j]:.1f}", ha="center", va="center",
                    fontsize=9, color=txt)

    fig.patch.set_facecolor(bg)
    ax.set_facecolor(bg)
    ax.set_title(config.get("title","Heatmap"), color=txt,
                 fontsize=15, fontweight="bold", pad=16)
    ax.tick_params(colors=txt)
    for spine in ax.spines.values(): spine.set_color(DARK_GRID if style=="dark" else GRID_COLOR)
    _save(fig, config["output"])


# ── Main entry point ──────────────────────────────────────────
def make_chart(config):
    """
    Generate a chart from config dict and save as PNG.
    See SKILL.md for full config reference.
    """
    required = ["type", "title", "output", "data"]
    for key in required:
        if key not in config:
            raise ValueError(f"Missing required config key: '{key}'")

    chart_type = config["type"].lower()
    dispatch = {
        "bar":       _bar,
        "line":      _line,
        "pie":       _pie,
        "scatter":   _scatter,
        "area":      _area,
        "histogram": _histogram,
        "heatmap":   _heatmap,
    }
    if chart_type not in dispatch:
        raise ValueError(f"Unknown chart type '{chart_type}'. Choose from: {list(dispatch.keys())}")

    dispatch[chart_type](config)

print("make_chart() loaded. Ready.")
