"""Static SVG charts for the README, in light and dark versions.

Each function takes plain rows prepared by publish.py and returns an SVG string. Standard
library only. Colour always means the thinking mode (blue off, orange on); other
distinctions use shape or position, so colour never carries two meanings.
"""
import math

FONT = 'font-family="system-ui, -apple-system, Segoe UI, sans-serif"'
# Validated two-slot categorical palette (thinking off, thinking on) plus chrome, per theme.
THEMES = {
    "light": {"surface": "#fcfcfb", "ink": "#0b0b0b", "ink2": "#52514e", "muted": "#898781",
              "grid": "#e1e0d9", "axis": "#c3c2b7", "off": "#2a78d6", "on": "#eb6834", "seq": "#2a78d6"},
    "dark": {"surface": "#1a1a19", "ink": "#f0efec", "ink2": "#c3c2b7", "muted": "#898781",
             "grid": "#2c2c2a", "axis": "#383835", "off": "#3987e5", "on": "#d95926", "seq": "#3987e5"},
}
# Round values for log-scale time axes, in seconds.
TIME_TICKS = (0.1, 0.3, 1, 3, 10, 30, 60, 180, 600, 1800)


class Canvas:
    def __init__(self, theme, width, height, title, subtitle=None):
        self.t = THEMES[theme]
        self.width, self.height = width, height
        self.parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
                      f'viewBox="0 0 {width} {height}" role="img" aria-label="{title}">',
                      f'<rect width="{width}" height="{height}" rx="8" fill="{self.t["surface"]}"/>']
        self.text(24, 34, title, size=16, fill="ink", weight="600")
        if subtitle:
            self.text(24, 56, subtitle, size=12)

    def text(self, x, y, content, size=12, fill="ink2", anchor="start", weight="normal", halo=False, color=None):
        outline = f'stroke="{self.t["surface"]}" stroke-width="4" paint-order="stroke" ' if halo else ""
        self.parts.append(f'<text x="{x:.1f}" y="{y:.1f}" font-size="{size}" font-weight="{weight}" '
                          f'text-anchor="{anchor}" fill="{color or self.t[fill]}" {outline}{FONT}>{content}</text>')

    def line(self, x1, y1, x2, y2, stroke="grid", width=1, extra=""):
        self.parts.append(f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" '
                          f'stroke="{self.t[stroke]}" stroke-width="{width}"{extra}/>')

    def dot(self, x, y, r, color, hollow=False):
        if hollow:
            self.parts.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{r}" fill="{self.t["surface"]}" '
                              f'stroke="{color}" stroke-width="2.5"/>')
        else:
            self.parts.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{r}" fill="{color}" '
                              f'stroke="{self.t["surface"]}" stroke-width="2"/>')

    def legend_dot(self, x, y, color, label, hollow=False):
        self.dot(x + 6, y - 4, 6, color, hollow)
        self.text(x + 18, y, label)
        return x + 30 + 6.8 * len(label)

    def svg(self):
        return "\n".join(self.parts + ["</svg>"]) + "\n"


def log_scale(values, left, width):
    """A log-scale x mapping over round time ticks that cover the values with some margin."""
    low = max((tick for tick in TIME_TICKS if tick <= min(values) / 1.25), default=TIME_TICKS[0])
    high = min((tick for tick in TIME_TICKS if tick >= max(values) * 1.25), default=TIME_TICKS[-1])
    ticks = [tick for tick in TIME_TICKS if low <= tick <= high]
    span = math.log10(high) - math.log10(low)
    return (lambda value: left + width * (math.log10(value) - math.log10(low)) / span), ticks


def time_label(seconds):
    return f"{seconds:g} s" if seconds < 60 else f"{seconds / 60:g} min"


def duration(seconds):
    return f"{seconds:.0f} s" if seconds < 60 else f"{seconds / 60:.1f} min"


def dumbbell(rows, theme, title):
    """One row per model: thinking-off and thinking-on dots, accuracy on the left, time on the right.

    rows: [{"model", "note", "off": {"accuracy", "median_s"} or None, "on": {...} or None}]
    """
    label_w, panel_w, gap = 230, 290, 56
    top, row_h = 92, 44
    height = top + row_h * len(rows) + 54
    c = Canvas(theme, 920, height, title,
               "For each model: the accuracy thinking adds, and the extra time per case it costs.")
    x0_acc = label_w
    x0_time = label_w + panel_w + gap
    times = [mode["median_s"] for row in rows for mode in (row["off"], row["on"]) if mode]
    time_x, ticks = log_scale(times, x0_time, panel_w)

    def acc_x(percent):
        return x0_acc + panel_w * percent / 100

    # Legend in the top-right corner, clear of the title and the panel headings.
    legend_x = c.width - 24 - (30 + 6.8 * len("thinking off")) - (18 + 6.8 * len("thinking on"))
    legend_x = c.legend_dot(legend_x, 34, c.t["off"], "thinking off")
    c.legend_dot(legend_x, 34, c.t["on"], "thinking on")
    bottom = top + row_h * len(rows) - 10
    c.text(x0_acc, top - 8, "Accuracy (cases passed)", fill="ink", weight="600")
    c.text(x0_time, top - 8, "Median time per case (log scale)", fill="ink", weight="600")
    for percent in range(0, 101, 25):
        c.line(acc_x(percent), top + 2, acc_x(percent), bottom)
        c.text(acc_x(percent), bottom + 18, f"{percent}%", fill="muted", anchor="middle")
    for tick in ticks:
        c.line(time_x(tick), top + 2, time_x(tick), bottom)
        c.text(time_x(tick), bottom + 18, time_label(tick), fill="muted", anchor="middle")
    for index, row in enumerate(rows):
        y = top + row_h * index + row_h / 2
        # Left-aligned from the frame edge, so a long note can never run off the left side.
        c.text(24, y + (0 if row["note"] else 4), row["model"], fill="ink", weight="600")
        if row["note"]:
            c.text(24, y + 15, row["note"], size=11)
        for scale, key, fmt in ((acc_x, "accuracy", lambda v: f"{v:.0f}%"), (time_x, "median_s", duration)):
            points = [(mode, row[mode][key]) for mode in ("off", "on") if row[mode]]
            if len(points) == 2:
                c.line(scale(points[0][1]), y, scale(points[1][1]), y, stroke="axis", width=3)
            for mode, value in points:
                c.dot(scale(value), y, 7, c.t[mode])
            # Value labels sit outside the pair: the smaller value to the left, the larger to the right.
            ordered = sorted(points, key=lambda item: item[1])
            for position, (mode, value) in enumerate(ordered):
                outer_right = position == len(ordered) - 1
                c.text(scale(value) + (12 if outer_right else -12), y + 4, fmt(value),
                       anchor="start" if outer_right else "end", halo=True)
    return c.svg()


def heatmap(rows, columns, theme, title):
    """Rows of models and modes, columns of tracks; each cell is shaded by the share passed.

    rows: [{"label", "mode", "values": [percent per column]}]
    """
    label_w, cell_w, cell_h, top = 230, 104, 38, 100
    width = label_w + cell_w * len(columns) + 30
    height = top + cell_h * len(rows) + 46
    c = Canvas(theme, width, height, title, "Share of each track's cases passed. Stronger colour is better.")
    for index, name in enumerate(columns):
        c.text(label_w + cell_w * index + cell_w / 2, top - 12, name, fill="ink", anchor="middle", weight="600")
    for r, row in enumerate(rows):
        y = top + cell_h * r
        c.dot(24 + 6, y + cell_h / 2, 6, c.t[row["mode"]])
        c.text(42, y + cell_h / 2 + 4, row["label"], fill="ink")
        for col, value in enumerate(row["values"]):
            x = label_w + cell_w * col
            # One hue, opacity by value: low scores fade into the surface in both themes.
            opacity = 0.08 + 0.87 * value / 100
            c.parts.append(f'<rect x="{x + 2}" y="{y + 2}" width="{cell_w - 4}" height="{cell_h - 4}" rx="4" '
                           f'fill="{c.t["seq"]}" fill-opacity="{opacity:.2f}"/>')
            colour = "#ffffff" if opacity > 0.55 else None
            c.text(x + cell_w / 2, y + cell_h / 2 + 5, f"{value:.0f}%", size=13, anchor="middle",
                   weight="600" if col == len(columns) - 1 else "normal", fill="ink", color=colour)
    # The overall column is set apart by a gap line.
    x = label_w + cell_w * (len(columns) - 1)
    c.line(x, top - 4, x, top + cell_h * len(rows), stroke="axis", width=1.5)
    c.text(24, height - 16, "Each track has 13 cases; the last column is all 65.", size=11, fill="muted")
    return c.svg()


def threads(rows, theme, title, low, high):
    """Time per case at two thread counts: an outline dot (fewer threads) and a filled dot (more).

    rows: [{"label", "mode", "before", "after"}] with times in seconds.
    """
    label_w, plot_w, top, row_h = 250, 470, 104, 40
    height = top + row_h * len(rows) + 54
    c = Canvas(theme, label_w + plot_w + 120, height, title,
               f"Median time per case with {low} and {high} CPU threads. Further left is faster.")
    values = [row[key] for row in rows for key in ("before", "after")]
    x, ticks = log_scale(values, label_w, plot_w)
    legend_x = c.legend_dot(label_w, 80, c.t["ink2"], f"{low} threads", hollow=True)
    c.legend_dot(legend_x, 80, c.t["ink2"], f"{high} threads")
    bottom = top + row_h * len(rows) - 6
    for tick in ticks:
        c.line(x(tick), top - 6, x(tick), bottom)
        c.text(x(tick), bottom + 18, time_label(tick), fill="muted", anchor="middle")
    for index, row in enumerate(rows):
        y = top + row_h * index + row_h / 2 - 6
        c.dot(24 + 6, y - 4, 6, c.t[row["mode"]])
        c.text(42, y, row["label"], fill="ink")
        before, after = x(row["before"]), x(row["after"])
        c.line(before, y - 4, after, y - 4, stroke="axis", width=3)
        c.dot(before, y - 4, 7, c.t[row["mode"]], hollow=True)
        c.dot(after, y - 4, 7, c.t[row["mode"]])
        change = (row["after"] / row["before"] - 1) * 100
        word = "faster" if change < 0 else "slower"
        c.text(max(before, after) + 14, y, f"{abs(change):.0f}% {word}", fill="ink", weight="600")
    return c.svg()
