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
# Sequential blue ramp (validated palette steps 100-700) for the heatmap, light to dark.
BLUES = ("#cde2fb", "#b7d3f6", "#9ec5f4", "#86b6ef", "#6da7ec", "#5598e7", "#3987e5",
         "#2a78d6", "#256abf", "#1c5cab", "#184f95", "#104281", "#0d366b")
# Average character width at 13 px, for laying out legends and badges.
CHAR_W = 7.4
# Round values for log-scale time axes, in seconds.
TIME_TICKS = (0.1, 0.3, 1, 3, 10, 30, 60, 180, 600, 1800)


class Canvas:
    def __init__(self, theme, width, height, title, subtitle=None):
        self.t = THEMES[theme]
        self.width, self.height = width, height
        self.parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
                      f'viewBox="0 0 {width} {height}" role="img" aria-label="{title}">',
                      f'<rect width="{width}" height="{height}" rx="8" fill="{self.t["surface"]}"/>']
        self.text(24, 36, title, size=19, fill="ink", weight="700")
        if subtitle:
            self.text(24, 60, subtitle, size=13.5)

    def text(self, x, y, content, size=13, fill="ink2", anchor="start", weight="normal", halo=False, color=None):
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
        self.dot(x + 7, y - 4, 7, color, hollow)
        self.text(x + 20, y, label, fill="ink")
        return x + 34 + CHAR_W * len(label)

    def legend_width(self, *labels):
        return sum(34 + CHAR_W * len(label) for label in labels) - 14

    def tick(self, x, y, label):
        """Axis tick labels in the secondary ink: readable, but quieter than the data."""
        self.text(x, y, label, size=12.5, anchor="middle")

    def svg(self):
        return "\n".join(self.parts + ["</svg>"]) + "\n"


def whole(value):
    """A percentage or count rounded half up, as the site's JavaScript rounds (42.5 -> 43), never to the
    even neighbour as Python's formatting does (42.5 -> 42), so the README and the site agree."""
    return f"{math.floor(value + 0.5):.0f}"


def label_column(labels, minimum):
    """Width of a column of 14 px semibold labels after a mode dot (text starts at x = 44), wide
    enough for the longest label, so a long model name never runs into the plot."""
    return max(minimum, round(44 + 8.3 * max((len(label) for label in labels), default=0) + 18))


def luminance(hex_colour):
    """Relative luminance (WCAG) of a #rrggbb colour."""
    def channel(value):
        value /= 255
        return value / 12.92 if value <= 0.03928 else ((value + 0.055) / 1.055) ** 2.4
    r, g, b = (int(hex_colour[i:i + 2], 16) for i in (1, 3, 5))
    return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)


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

    rows: [{"model", "badges": [short labels], "off": {"accuracy", "median_s"} or None, "on": {...} or None}]
    """
    # The name column fits the longest name (14.5 px bold), and the two panels share what is left of
    # the 920 px, so a long name never runs into a low score's dot.
    gap = 50
    label_w = max(250, round(24 + 8.6 * max((len(row["model"]) for row in rows), default=0) + 16))
    panel_w = min(280, (920 - label_w - gap - 40) // 2)
    top, row_h = 104, 52
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
    legend_x = c.width - 24 - c.legend_width("thinking off", "thinking on")
    legend_x = c.legend_dot(legend_x, 36, c.t["off"], "thinking off")
    c.legend_dot(legend_x, 36, c.t["on"], "thinking on")
    bottom = top + row_h * len(rows) - 10
    c.text(x0_acc, top - 10, "Accuracy (cases passed)", size=14, fill="ink", weight="700")
    c.text(x0_time, top - 10, "Median time per case (log scale)", size=14, fill="ink", weight="700")
    for percent in range(0, 101, 25):
        c.line(acc_x(percent), top + 2, acc_x(percent), bottom)
        c.tick(acc_x(percent), bottom + 20, f"{percent}%")
    for tick in ticks:
        c.line(time_x(tick), top + 2, time_x(tick), bottom)
        c.tick(time_x(tick), bottom + 20, time_label(tick))
    for index, row in enumerate(rows):
        y = top + row_h * index + row_h / 2
        # Left-aligned from the frame edge, so a long note can never run off the left side.
        c.text(24, y + (-3 if row["badges"] else 5), row["model"], size=14.5, fill="ink", weight="700")
        # Missing capabilities and incidents as small outlined badges, so they read as labels, not captions.
        bx = 24
        for badge in row["badges"]:
            w = 6.6 * len(badge) + 14
            c.parts.append(f'<rect x="{bx:.1f}" y="{y + 5:.1f}" width="{w:.1f}" height="19" rx="9.5" fill="none" '
                           f'stroke="{c.t["ink2"]}" stroke-width="1.25"/>')
            c.text(bx + w / 2, y + 18.5, badge, size=11.5, fill="ink", anchor="middle", weight="600")
            bx += w + 6
        for scale, key, fmt in ((acc_x, "accuracy", lambda v: f"{whole(v)}%"), (time_x, "median_s", duration)):
            points = [(mode, row[mode][key]) for mode in ("off", "on") if row[mode]]
            if len(points) == 2:
                c.line(scale(points[0][1]), y, scale(points[1][1]), y, stroke="ink2", width=2.5,
                       extra=' stroke-opacity="0.45"')
            for mode, value in points:
                c.dot(scale(value), y, 8.5, c.t[mode])
            # Value labels sit outside the pair: the smaller value to the left, the larger to the right.
            ordered = sorted(points, key=lambda item: item[1])
            for position, (mode, value) in enumerate(ordered):
                outer_right = position == len(ordered) - 1
                c.text(scale(value) + (14 if outer_right else -14), y + 5, fmt(value), size=14, fill="ink",
                       weight="600", anchor="start" if outer_right else "end", halo=True)
    return c.svg()


def heatmap(rows, columns, theme, title, per_track=13):
    """Rows of models and modes, columns of tracks; each cell is shaded by the share passed.

    rows: [{"label", "mode", "values": [percent per column]}]
    """
    # Narrower cells once there are more tracks, so the chart stays near the README's width.
    many = len(columns) > 6
    label_w = label_column([row["label"] for row in rows], 270)
    # With more tracks, the cells shrink so the chart keeps the README's 920 px width, and the
    # headers use short names.
    cell_w, cell_h, top = (max(56, (920 - label_w - 30) // len(columns)) if many else 104), 44, 112
    short = {"Arithmetic": "Arith", "Structured": "Struct", "Instructions": "Instr"} if many else {}
    width = label_w + cell_w * len(columns) + 30
    height = top + cell_h * len(rows) + 46
    better = "Darker" if theme == "light" else "Brighter"
    c = Canvas(theme, width, height, title, f"Share of each track's cases passed. {better} cells are better.")
    # The dot before each name gives the thinking mode, so names can stay short.
    legend_x = c.width - 24 - c.legend_width("thinking off", "thinking on")
    legend_x = c.legend_dot(legend_x, 36, c.t["off"], "thinking off")
    c.legend_dot(legend_x, 36, c.t["on"], "thinking on")
    for index, name in enumerate(columns):
        c.text(label_w + cell_w * index + cell_w / 2, top - 14, short.get(name, name), size=13 if many else 14, fill="ink",
               anchor="middle", weight="700")
    for r, row in enumerate(rows):
        y = top + cell_h * r
        c.dot(24 + 7, y + cell_h / 2, 7, c.t[row["mode"]])
        c.text(44, y + cell_h / 2 + 5, row["label"], size=14, fill="ink", weight="600")
        for col, value in enumerate(row["values"]):
            x = label_w + cell_w * col
            if value is None:
                # Not applicable: the model can't attempt this track. Neutral, never shaded as a score.
                c.parts.append(f'<rect x="{x + 2}" y="{y + 2}" width="{cell_w - 4}" height="{cell_h - 4}" rx="5" '
                               f'fill="none" stroke="{c.t["ink2"]}" stroke-opacity="0.6" stroke-dasharray="4 3"/>')
                c.text(x + cell_w / 2, y + cell_h / 2 + 5, "n/a", size=13, anchor="middle")
                continue
            # Discrete steps of one blue ramp. Better scores are darker in light mode and brighter in
            # dark mode, so the strongest cells stand out from the background in both.
            ramp = BLUES if c.t is THEMES["light"] else BLUES[::-1][2:]
            fill = ramp[min(len(ramp) - 1, round(value / 100 * (len(ramp) - 1)))]
            c.parts.append(f'<rect x="{x + 2}" y="{y + 2}" width="{cell_w - 4}" height="{cell_h - 4}" rx="5" '
                           f'fill="{fill}"/>')
            # Text inside a fill is white or near-black, chosen by the fill's brightness.
            c.text(x + cell_w / 2, y + cell_h / 2 + 5.5, f"{whole(value)}%", size=14.5, anchor="middle",
                   weight="700" if col == len(columns) - 1 else "600",
                   color="#ffffff" if luminance(fill) < 0.3 else "#0b0b0b")
    # The overall column is set apart by a gap line.
    x = label_w + cell_w * (len(columns) - 1)
    c.line(x, top - 4, x, top + cell_h * len(rows), stroke="ink2", width=1.5)
    c.text(24, height - 16, f"Each track has {per_track} cases. n/a: the model can't attempt that track (for example, "
           "no vision). The last column covers the tracks each model supports.", size=12)
    return c.svg()


def threads(rows, theme, title, low, high, primary):
    """Time per case at two thread counts, with an arrow from fewer threads to more.

    The main thread count (primary) is drawn filled, like every other chart; the other as an outline.

    rows: [{"label", "mode", "before", "after"}] with times in seconds.
    """
    # Names only: the dot before each name gives the thinking mode, as in the heatmap.
    top, row_h = 116, 46
    label_w = label_column([row["name"] for row in rows], 200)
    plot_w = max(380, 720 - label_w)
    height = top + row_h * len(rows) + 54
    c = Canvas(theme, label_w + plot_w + 200, height, title,
               f"Median time per case. Each arrow goes from {low} to {high} threads; filled is the {primary}-thread "
               "standard used in the other charts. Left is faster.")
    values = [row[key] for row in rows for key in ("before", "after")]
    x, ticks = log_scale(values, label_w, plot_w)
    legend_x = c.legend_dot(label_w, 90, c.t["ink2"], f"{low} threads", hollow=primary != low)
    c.legend_dot(legend_x, 90, c.t["ink2"], f"{high} threads", hollow=primary != high)
    modes = [mode for mode in ("off", "on") if any(row["mode"] == mode for row in rows)]
    legend_x = c.width - 24 - c.legend_width(*(f"thinking {mode}" for mode in modes))
    for mode in modes:
        legend_x = c.legend_dot(legend_x, 36, c.t[mode], f"thinking {mode}")
    bottom = top + row_h * len(rows) - 6
    for tick in ticks:
        c.line(x(tick), top - 6, x(tick), bottom)
        c.tick(x(tick), bottom + 20, time_label(tick))
    for index, row in enumerate(rows):
        y = top + row_h * index + row_h / 2 - 6
        c.dot(24 + 7, y - 4, 7, c.t[row["mode"]])
        c.text(44, y + 1, row["name"], size=14, fill="ink", weight="600")
        before, after = x(row["before"]), x(row["after"])
        cy = y - 4
        # A thin arrow joins the two dots, edge to edge, pointing at the higher thread count.
        direction = 1 if after > before else -1
        start_x, tip = before + direction * 8, after - direction * 9
        if (tip - start_x) * direction > 6:
            c.line(start_x, cy, tip - direction * 4, cy, stroke="ink", width=1.25)
            c.parts.append(f'<path d="M{tip:.1f},{cy:.1f} l{-direction * 6:.1f},-3.5 v7 z" fill="{c.t["ink"]}"/>')
        # Main thread count filled, like every other chart; the other count as an outline.
        c.dot(before, cy, 8.5, c.t[row["mode"]], hollow=primary != low)
        c.dot(after, cy, 8.5, c.t[row["mode"]], hollow=primary != high)
        change = (row["after"] / row["before"] - 1) * 100
        word = "faster" if change < 0 else "slower"
        c.text(max(before, after) + 18, y + 1, f"{high} threads: {whole(abs(change))}% {word}", size=14, fill="ink",
               weight="700")
    return c.svg()


# Phone versions. GitHub serves them to screens narrower than 600 px (see publish.picture), where
# the desktop charts would be scaled to under half size. They are drawn about 440 px wide, one
# column, with larger type, so they appear at roughly their drawn size on a phone.
MOBILE_W = 440


def bold_width(content, size):
    """Generous width estimate for a short bold label such as '94%' or '5.1 min'."""
    narrow, wide = set(".,:1il "), set("mw%")
    return sum(0.36 if ch in narrow else 0.9 if ch in wide else 0.64 for ch in content) * size


def _mobile_canvas(theme, height, title, subtitle_lines):
    c = Canvas(theme, MOBILE_W, height, title)
    c.parts[-1] = c.parts[-1].replace('font-size="19"', 'font-size="20"')
    for index, line in enumerate(subtitle_lines):
        c.text(24, 62 + 19 * index, line, size=14.5)
    return c


def _mobile_legend(c, y, items):
    x = 24
    for colour, label, hollow in items:
        c.dot(x + 7, y - 5, 7.5, colour, hollow)
        c.text(x + 21, y, label, size=15, fill="ink")
        x += 40 + 8.2 * len(label)


def dumbbell_mobile(rows, theme, title):
    """Accuracy and time stacked; each model's name on its own line above its dots."""
    row_h, panel_gap = 58, 70
    top = 172
    panel_h = row_h * len(rows)
    height = top + 2 * panel_h + panel_gap + 70
    c = _mobile_canvas(theme, height, title, ["Accuracy thinking adds, and the time", "per case it costs, per model."])
    _mobile_legend(c, 118, [(c.t["off"], "thinking off", False), (c.t["on"], "thinking on", False)])
    left, right = 34, MOBILE_W - 34
    times = [mode["median_s"] for row in rows for mode in (row["off"], row["on"]) if mode]
    time_x, ticks = log_scale(times, left, right - left)
    acc_x = lambda percent: left + (right - left) * percent / 100
    panels = ((top, "Accuracy (cases passed)", acc_x, "accuracy", lambda v: f"{whole(v)}%",
               [(acc_x(p), f"{p}%") for p in (0, 50, 100)]),
              (top + panel_h + panel_gap, "Median time per case (log scale)", time_x, "median_s", duration,
               [(time_x(t), time_label(t)) for t in ticks if t in (1, 10, 60, 600) or len(ticks) <= 4]))
    for y0, heading, scale, key, fmt, tick_marks in panels:
        c.text(24, y0 - 6, heading, size=16, fill="ink", weight="700")
        bottom = y0 + panel_h
        for x, label in tick_marks:
            c.line(x, y0 + 8, x, bottom)
            c.text(x, bottom + 20, label, size=14, anchor="middle")
        for index, row in enumerate(rows):
            y = y0 + row_h * index
            label = row["model"] + ("  · " + ", ".join(row["badges"]) if row["badges"] else "")
            c.text(24, y + 24, label if len(label) <= 44 else row["model"], size=15, fill="ink", weight="700")
            dots_y = y + 44
            points = [(mode, row[mode][key]) for mode in ("off", "on") if row[mode]]
            if len(points) == 2:
                c.line(scale(points[0][1]), dots_y, scale(points[1][1]), dots_y, stroke="ink2", width=2.5,
                       extra=' stroke-opacity="0.45"')
            for mode, value in points:
                c.dot(scale(value), dots_y, 8.5, c.t[mode])
            ordered = sorted(points, key=lambda item: item[1])
            for position, (mode, value) in enumerate(ordered):
                outer_right = position == len(ordered) - 1
                text = fmt(value)
                # The right-hand label goes right of its dot if its own width fits inside the frame;
                # otherwise left of it, anchored at its end so it never covers the dot.
                fits = scale(value) + 13 + bold_width(text, 15) <= MOBILE_W - 8
                if outer_right and fits:
                    x, anchor = scale(value) + 13, "start"
                else:
                    x, anchor = scale(value) - 13, "end"
                c.text(x, dots_y + 5.5, text, size=15, fill="ink", weight="700", anchor=anchor, halo=True)
    return c.svg()


def heatmap_mobile(rows, columns, theme, title):
    """One block per model: its name, then a row of cells, one per track."""
    short = {"Arithmetic": "Arith", "Structured": "Struct", "Context": "Ctx", "Instructions": "Instr"}
    if len(columns) > 6:
        short["Vision"] = "Vis"
    small = len(columns) > 6  # suite 1.8's nine columns need smaller text to fit a phone
    cell_w = (MOBILE_W - 48) / len(columns)
    block_h, top = 70, 150
    height = top + block_h * len(rows) + 70
    better = "Darker" if theme == "light" else "Brighter"
    c = _mobile_canvas(theme, height, title, ["Share of each track's cases passed.", f"{better} cells are better."])
    _mobile_legend(c, 112, [(c.t["off"], "thinking off", False), (c.t["on"], "thinking on", False)])
    for index, name in enumerate(columns):
        c.text(24 + cell_w * index + cell_w / 2, top - 6, short.get(name, name), size=12.5 if small else 14, fill="ink",
               anchor="middle", weight="700")
    ramp = BLUES if c.t is THEMES["light"] else BLUES[::-1][2:]
    for r, row in enumerate(rows):
        y = top + block_h * r
        c.dot(24 + 7, y + 20, 7, c.t[row["mode"]])
        c.text(44, y + 25, row["label"], size=15, fill="ink", weight="700")
        for col, value in enumerate(row["values"]):
            x = 24 + cell_w * col
            cy = y + 34
            if value is None:
                c.parts.append(f'<rect x="{x + 2:.1f}" y="{cy}" width="{cell_w - 4:.1f}" height="30" rx="5" fill="none" '
                               f'stroke="{c.t["ink2"]}" stroke-opacity="0.6" stroke-dasharray="4 3"/>')
                c.text(x + cell_w / 2, cy + 20, "n/a", size=14, anchor="middle")
                continue
            fill = ramp[min(len(ramp) - 1, round(value / 100 * (len(ramp) - 1)))]
            c.parts.append(f'<rect x="{x + 2:.1f}" y="{cy}" width="{cell_w - 4:.1f}" height="30" rx="5" fill="{fill}"/>')
            c.text(x + cell_w / 2, cy + 20.5, f"{whole(value)}%", size=12.5 if small else 14.5, anchor="middle",
                   weight="700", color="#ffffff" if luminance(fill) < 0.3 else "#0b0b0b")
    c.text(24, height - 40, "n/a: the model can't attempt that track.", size=13.5)
    c.text(24, height - 20, "All: the tracks each model supports.", size=13.5)
    return c.svg()


def threads_mobile(rows, theme, title, low, high, primary):
    """Each model and mode on its own line, then the 2-thread and 4-thread dots with the change."""
    row_h, top = 62, 160
    height = top + row_h * len(rows) + 60
    c = _mobile_canvas(theme, height, title, ["Median time per case. Arrows go from", f"{low} threads to {high} threads."])
    _mobile_legend(c, 118, [(c.t["ink2"], f"{low} threads", primary != low),
                            (c.t["ink2"], f"{high} threads", primary != high)])
    left, right = 34, MOBILE_W - 150
    values = [row[key] for row in rows for key in ("before", "after")]
    x, ticks = log_scale(values, left, right - left)
    bottom = top + row_h * len(rows)
    for tick in ticks:
        if tick in (3, 10, 60, 600) or len(ticks) <= 4:
            c.line(x(tick), top + 4, x(tick), bottom)
            c.text(x(tick), bottom + 20, time_label(tick), size=14, anchor="middle")
    for index, row in enumerate(rows):
        y = top + row_h * index
        c.dot(24 + 7, y + 19, 7, c.t[row["mode"]])
        c.text(44, y + 24, row["label"], size=15, fill="ink", weight="700")
        cy = y + 46
        before, after = x(row["before"]), x(row["after"])
        direction = 1 if after > before else -1
        start_x, tip = before + direction * 8, after - direction * 9
        if (tip - start_x) * direction > 6:
            c.line(start_x, cy, tip - direction * 4, cy, stroke="ink", width=1.25)
            c.parts.append(f'<path d="M{tip:.1f},{cy:.1f} l{-direction * 6:.1f},-3.5 v7 z" fill="{c.t["ink"]}"/>')
        c.dot(before, cy, 8.5, c.t[row["mode"]], hollow=primary != low)
        c.dot(after, cy, 8.5, c.t[row["mode"]], hollow=primary != high)
        change = (row["after"] / row["before"] - 1) * 100
        c.text(right + 24, cy + 5.5, f"{whole(abs(change))}% {'faster' if change < 0 else 'slower'}", size=15,
               fill="ink", weight="700")
    return c.svg()
