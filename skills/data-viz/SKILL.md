---
name: data-viz
description: Create data visualizations, charts, and graphs as PNG/HTML files on the user's desktop or any path. Use this skill whenever the user asks to visualize data, make a chart, graph, plot, dashboard, or anything involving visual representation of numbers or datasets — even if they just say "graph this" or "show me a chart of X". Always use this skill for any visualization request.
---

# Data Visualization Skill

Generate charts and graphs using Python only. All dependencies managed via pip.

## Workflow

**Step 1 — Generate chart:**
```python
exec(open('scripts/chart.py').read())
```
Then immediately below it, define your `config` dict and call `make_chart(config)`.

## Config Reference

```python
config = {
    # REQUIRED
    "type": "bar"|"line"|"pie"|"scatter"|"area"|"histogram"|"heatmap",
    "title": "Chart Title",
    "output": r"C:\Users\Administrator\Desktop\chart.png",  # always .png

    # DATA — pick one format:
    "data": {"Label A": 42, "Label B": 88},          # simple dict
    # OR
    "data": {"x": [1,2,3,4], "y": [10,20,15,30]},   # x/y series
    # OR
    "data": {"Col1": [1,2,3], "Col2": [4,5,6]},      # multi-series

    # OPTIONAL
    "xlabel": "X Axis Label",
    "ylabel": "Y Axis Label",
    "color": "#d97757",           # single color override
    "palette": ["#d97757","#6a9bcc","#788c5d"],  # multi-series colors
    "figsize": [10, 6],           # [width, height] in inches
    "style": "dark"|"light",      # default: light
}
make_chart(config)
```

## Chart Types Quick Guide
- `bar` — comparisons between categories
- `line` — trends over time
- `pie` — proportions (use sparingly, max 6 slices)
- `scatter` — correlations, needs x+y arrays
- `area` — like line but filled
- `histogram` — distribution of values, data = flat list
- `heatmap` — 2D matrix data, data = dict of lists (columns)

## Notes
- Always save to Desktop unless user specifies otherwise
- After saving, print the full output path
- For multiple charts, call `make_chart(config)` multiple times with different configs
- Open the file after saving with `os.startfile(output_path)` so user sees it immediately
