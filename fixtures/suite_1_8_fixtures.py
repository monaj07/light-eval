"""Drawings for the suite 1.8 vision track (Pillow and macOS fonts required).

Each image shows something people photograph or screenshot, and each question
needs reading plus at least one step of reasoning. The data behind every image
is a constant here, and each docstring works out the expected answer from it,
so a change to the data shows which answer in `suite_1_8.py` must change too.
Called from make_fixtures.py, which writes the PNGs to fixtures/v1_8/.
"""
import math

from PIL import Image, ImageDraw, ImageFont

FONTS = "/System/Library/Fonts/Supplemental/"
INK, MUTED, LINE, PAPER = "#1f2328", "#5d6670", "#d0d4d9", "#ffffff"


def font(size, bold=False, mono=False):
    if mono:
        return ImageFont.truetype(FONTS + ("Courier New Bold.ttf" if bold else "Courier New.ttf"), size)
    return ImageFont.truetype(FONTS + ("Arial Bold.ttf" if bold else "Arial.ttf"), size)


def canvas(width, height, color=PAPER):
    image = Image.new("RGB", (width, height), color)
    return image, ImageDraw.Draw(image)


# ---------------------------------------------------------------- challenge

VISITORS_2025 = (40, 45, 55, 60, 70, 80, 85, 80, 65, 55, 45, 50)
VISITORS_2026 = (50, 55, 50, 70, 65, 95, 100, 70, 75, 50, 55, 45)
MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def visitors_lines():
    """Two-series line chart. 2026 is above 2025 in Jan, Feb, Apr, Jun, Jul, Sep and Nov: 7 months.
    Every monthly gap is at least 5 (a third of a gridline step)."""
    image, draw = canvas(820, 520)
    left, right, top, bottom, high = 80, 790, 90, 450, 120
    y = lambda value: bottom - (bottom - top) * value / high
    x = lambda index: left + 25 + index * (right - left - 50) / 11
    draw.text((left, 22), "Park visitors per month (thousands)", font=font(24, True), fill=INK)
    for value in range(0, high + 1, 10):
        draw.line((left, y(value), right, y(value)), fill=LINE if value else MUTED, width=1)
        draw.text((left - 12, y(value)), str(value), font=font(15), fill=MUTED, anchor="rm")
    for index, month in enumerate(MONTHS):
        draw.text((x(index), bottom + 18), month, font=font(15), fill=MUTED, anchor="mm")
    for series, color, marker in ((VISITORS_2025, "#2f6db5", "circle"), (VISITORS_2026, "#e07b1f", "square")):
        points = [(x(i), y(v)) for i, v in enumerate(series)]
        draw.line(points, fill=color, width=3)
        for px, py in points:
            box = (px - 5, py - 5, px + 5, py + 5)
            draw.ellipse(box, fill=color) if marker == "circle" else draw.rectangle(box, fill=color)
    for index, (label, color) in enumerate((("2025", "#2f6db5"), ("2026", "#e07b1f"))):
        lx = 560 + index * 110
        draw.line((lx, 62, lx + 34, 62), fill=color, width=3)
        draw.text((lx + 42, 62), label, font=font(17), fill=INK, anchor="lm")
    return image


DEPARTURES = (("10:12", "Northport", "2", "Departed"), ("10:52", "Eastfield", "1", "On time"),
              ("11:05", "Northport", "3", "Cancelled"), ("11:20", "Northport", "2", "On time"),
              ("11:34", "Eastfield", "1", "On time"), ("11:50", "Northport", "3", "On time"))


def clock_departures():
    """Wall clock at 10:40 beside a departures board. The next Northport train that will run is
    11:20 (11:05 is cancelled), so it leaves in 40 minutes."""
    image, draw = canvas(900, 500, "#e9e5dc")
    cx, cy, radius = 190, 250, 150
    draw.ellipse((cx - radius - 10, cy - radius - 10, cx + radius + 10, cy + radius + 10), fill="#3a3a3a")
    draw.ellipse((cx - radius, cy - radius, cx + radius, cy + radius), fill="#fbfaf6")
    for tick in range(60):
        angle = math.radians(tick * 6 - 90)
        inner = radius - (18 if tick % 5 == 0 else 8)
        draw.line((cx + inner * math.cos(angle), cy + inner * math.sin(angle),
                   cx + (radius - 4) * math.cos(angle), cy + (radius - 4) * math.sin(angle)),
                  fill=INK, width=4 if tick % 5 == 0 else 1)
    for hour in range(1, 13):
        angle = math.radians(hour * 30 - 90)
        draw.text((cx + (radius - 38) * math.cos(angle), cy + (radius - 38) * math.sin(angle)), str(hour),
                  font=font(24, True), fill=INK, anchor="mm")
    hours, minutes = 10, 40
    for value, length, width in ((((hours % 12) + minutes / 60) * 30, 78, 9), (minutes * 6, 118, 5)):
        angle = math.radians(value - 90)
        draw.line((cx, cy, cx + length * math.cos(angle), cy + length * math.sin(angle)), fill=INK, width=width)
    draw.ellipse((cx - 8, cy - 8, cx + 8, cy + 8), fill="#b3261e")
    bx, by = 400, 60
    draw.rounded_rectangle((bx, by, 870, 450), 10, fill="#14181f")
    draw.text((bx + 22, by + 20), "DEPARTURES", font=font(26, True), fill="#ffd54a")
    for column, heading in zip((22, 120, 300, 360), ("Time", "To", "Plat", "Status")):
        draw.text((bx + column, by + 70), heading, font=font(17), fill="#9aa4b2")
    for row, (time, place, platform, status) in enumerate(DEPARTURES):
        top = by + 104 + row * 46
        color = "#ff7a6b" if status == "Cancelled" else "#7d8794" if status == "Departed" else "#f3f5f7"
        for column, text in zip((22, 120, 300, 360), (time, place, platform, status)):
            draw.text((bx + column, top), text, font=font(21, True), fill=color)
    return image


# ---------------------------------------------------------------- hard

RECEIPT = (("MILK 2L", "3.60", None), ("SOURDOUGH LOAF", "6.50", "1.50"), ("FREE RANGE EGGS 12PK", "7.20", None),
           ("BANANAS 1.24kg @ 3.50/kg", "4.34", None), ("OLIVE OIL 1L", "14.00", "3.50"),
           ("COFFEE BEANS 500G", "18.00", "4.50"), ("TOMATOES 0.86kg @ 6.00/kg", "5.16", None))


def grocery_receipt():
    """Receipt with three promotions. The discounted items cost (6.50 - 1.50) + (14.00 - 3.50)
    + (18.00 - 4.50) = 29.00 after their discounts. Subtotal 58.80, savings 9.50, total 49.30."""
    image, draw = canvas(560, 760, "#8a8f96")
    draw.rectangle((40, 20, 520, 740), fill="#fdfcf8")
    mono, bold = font(19, mono=True), font(19, True, mono=True)
    lines = [("GREENLEAF GROCER", "", True), ("14 Station St", "", False), ("", "", False),
             ("TAX INVOICE  #20418", "", False), ("", "", False)]
    for name, price, promo in RECEIPT:
        lines.append((name, price, False))
        if promo:
            lines.append(("  PROMO SAVING", "-" + promo, False))
    lines += [("", "", False), ("SUBTOTAL", "58.80", False), ("TOTAL SAVINGS", "9.50", False),
              ("TOTAL", "49.30", True), ("GST INCLUDED", "4.48", False), ("", "", False),
              ("EFTPOS", "49.30", False), ("", "", False), ("THANK YOU", "", False)]
    for index, (text, price, strong) in enumerate(lines):
        top = 50 + index * 28
        face = bold if strong else mono
        if not price and index < 5 or text == "THANK YOU":
            draw.text((280, top), text, font=face, fill=INK, anchor="ma")
        else:
            draw.text((66, top), text, font=face, fill=INK)
            draw.text((494, top), price, font=face, fill=INK, anchor="ra")
    return image


def flowchart():
    """Irrigation controller. With soil moisture 24%, no rain forecast, tank 15% and 38 °C:
    moisture below 30%? Yes -> rain forecast? No -> tank above 20%? No -> box D (water from mains).
    The Yes and No exits point in different directions at each diamond, and the temperature is a
    distractor: it matters only when the soil is not dry."""
    image, draw = canvas(960, 660)
    small = font(16)

    def diamond(cx, cy, text):
        draw.polygon([(cx, cy - 52), (cx + 120, cy), (cx, cy + 52), (cx - 120, cy)],
                     fill="#eef3fb", outline="#2f6db5", width=2)
        draw.multiline_text((cx, cy), text, font=small, fill=INK, anchor="mm", align="center")

    def box(cx, cy, letter, text):
        draw.rounded_rectangle((cx - 90, cy - 34, cx + 90, cy + 34), 8, fill="#f6f1e7", outline="#8a6d3b", width=2)
        draw.text((cx - 80, cy - 26), letter, font=font(18, True), fill="#8a6d3b")
        draw.multiline_text((cx + 8, cy), text, font=small, fill=INK, anchor="mm", align="center")

    def arrow(points, text=None, where=None):
        draw.line(points, fill=INK, width=2, joint="curve")
        (x1, y1), (x2, y2) = points[-2], points[-1]
        angle = math.atan2(y2 - y1, x2 - x1)
        draw.polygon([(x2, y2), (x2 - 12 * math.cos(angle - .4), y2 - 12 * math.sin(angle - .4)),
                      (x2 - 12 * math.cos(angle + .4), y2 - 12 * math.sin(angle + .4))], fill=INK)
        if text:
            draw.text(where, text, font=font(17, True), fill="#23843f" if text == "Yes" else "#b3261e")

    draw.rounded_rectangle((420, 14, 540, 54), 20, fill="#2f6db5")
    draw.text((480, 34), "Start", font=font(18, True), fill="white", anchor="mm")
    diamond(480, 130, "Soil moisture\nbelow 30%?")
    diamond(230, 300, "Rain forecast\nin next 6 h?")
    diamond(700, 300, "Temperature\nabove 35 °C?")
    diamond(390, 460, "Tank level\nabove 20%?")
    box(100, 460, "A", "Wait 6 hours")
    box(700, 460, "E", "Do nothing")
    box(860, 590, "B", "Mist for\n5 minutes")
    box(250, 590, "C", "Water from\ntank")
    box(600, 590, "D", "Water from\nmains")
    arrow([(480, 54), (480, 78)])
    arrow([(360, 130), (230, 130), (230, 248)], "Yes", (300, 104))
    arrow([(600, 130), (700, 130), (700, 248)], "No", (612, 104))
    arrow([(110, 300), (80, 300), (80, 426)], "Yes", (52, 270))
    arrow([(230, 352), (230, 460), (270, 460)], "No", (240, 370))
    arrow([(820, 300), (900, 300), (900, 556)], "Yes", (832, 270))
    arrow([(700, 352), (700, 426)], "No", (710, 372))
    arrow([(390, 512), (390, 540), (250, 540), (250, 556)], "Yes", (340, 516))
    arrow([(510, 460), (540, 460), (540, 556)], "No", (520, 432))
    return image


ROOMS = (("Bedroom 1", "Bathroom", "Study"), ("Living room", "Hallway", "Laundry"),
         ("Kitchen", "Entrance hall", "Garage"))
# Doorways between neighbouring rooms, as ((row, column), (row, column)).
DOORS = (((2, 1), (1, 1)), ((2, 1), (2, 0)), ((2, 0), (1, 0)), ((1, 0), (0, 0)), ((0, 0), (0, 1)),
         ((0, 1), (0, 2)), ((1, 1), (1, 0)), ((2, 2), (1, 2)))


def floor_plan():
    """3 x 3 floor plan. From the Entrance hall the only way to the Study is through Hallway (or
    Kitchen), Living room, Bedroom 1 and Bathroom: 4 rooms in between. The Hallway, Laundry and
    Garage all sit next to the Study or Bathroom without a doorway to them."""
    image, draw = canvas(820, 640, "#f4f1ea")
    left, top, cell_w, cell_h, wall = 60, 40, 230, 180, 8
    doors = {frozenset(pair) for pair in DOORS}
    draw.rectangle((left, top, left + 3 * cell_w, top + 3 * cell_h), fill="#fffdf8")
    for row in range(3):
        for column in range(3):
            x0, y0 = left + column * cell_w, top + row * cell_h
            draw.text((x0 + cell_w / 2, y0 + cell_h / 2), ROOMS[row][column], font=font(20, True), fill=INK, anchor="mm")
    for row in range(3):
        for column in range(3):
            x0, y0 = left + column * cell_w, top + row * cell_h
            if column < 2:  # wall to the right
                x = x0 + cell_w
                if frozenset({(row, column), (row, column + 1)}) in doors:
                    mid = y0 + cell_h / 2 + 50
                    draw.line((x, y0, x, mid - 30), fill=INK, width=wall)
                    draw.line((x, mid + 30, x, y0 + cell_h), fill=INK, width=wall)
                    draw.arc((x - 60, mid - 30, x + 60, mid + 90), 270, 360, fill=MUTED, width=2)
                else:
                    draw.line((x, y0, x, y0 + cell_h), fill=INK, width=wall)
            if row < 2:  # wall below
                y = y0 + cell_h
                if frozenset({(row, column), (row + 1, column)}) in doors:
                    mid = x0 + cell_w / 2 + 60
                    draw.line((x0, y, mid - 32, y), fill=INK, width=wall)
                    draw.line((mid + 32, y, x0 + cell_w, y), fill=INK, width=wall)
                    draw.arc((mid - 32, y - 64, mid + 96, y + 64), 180, 270, fill=MUTED, width=2)
                else:
                    draw.line((x0, y, x0 + cell_w, y), fill=INK, width=wall)
    draw.rectangle((left, top, left + 3 * cell_w, top + 3 * cell_h), outline=INK, width=wall + 4)
    gap = left + cell_w * 1.5
    draw.line((gap - 34, top + 3 * cell_h, gap + 34, top + 3 * cell_h), fill="#fffdf8", width=wall + 6)
    draw.text((gap, top + 3 * cell_h + 26), "Front door", font=font(16), fill=MUTED, anchor="mm")
    return image


STORES = (("Northgate", 120, 8, 131, 6), ("Riverside", 95, 4, 110, 12), ("Harbour", 140, 15, 152, 9),
          ("Westmoor", 88, 3, 104, 5), ("Hilltop", 132, 10, 139, 2), ("Lakeside", 76, 6, 90, 4))


def store_table():
    """Sales and returns by quarter, under merged headers. Net (sales - returns) rises from Q1 to Q2
    by 13, 7, 18, 14, 15 and 16: Harbour (+18) is largest. Sales alone rise most at Westmoor (+16)."""
    image, draw = canvas(820, 460)
    draw.text((40, 24), "Store results, $ thousands", font=font(22, True), fill=INK)
    columns = (40, 230, 380, 530, 680, 790)
    top, header = 80, 44
    draw.rectangle((columns[0], top, columns[-1], top + 2 * header), fill="#e8eef6")
    draw.text(((columns[0] + columns[1]) / 2, top + header), "Store", font=font(19, True), fill=INK, anchor="mm")
    for quarter, (a, b) in (("Q1", (1, 3)), ("Q2", (3, 5))):
        draw.text(((columns[a] + columns[b]) / 2, top + header / 2), quarter, font=font(19, True), fill=INK, anchor="mm")
        draw.line((columns[a], top + header, columns[b], top + header), fill="#9aa7b6", width=1)
    for index, name in enumerate(("Sales", "Returns", "Sales", "Returns")):
        draw.text(((columns[index + 1] + columns[index + 2]) / 2, top + header * 1.5), name, font=font(17), fill=INK, anchor="mm")
    for row, values in enumerate(STORES):
        y = top + 2 * header + row * 46
        if row % 2:
            draw.rectangle((columns[0], y, columns[-1], y + 46), fill="#f6f8fa")
        for index, value in enumerate(values):
            x = columns[0] + 16 if index == 0 else (columns[index] + columns[index + 1]) / 2
            draw.text((x, y + 23), str(value), font=font(18), fill=INK, anchor="lm" if index == 0 else "mm")
    bottom = top + 2 * header + len(STORES) * 46
    for x in columns:
        span_top = top + header if x in (columns[2], columns[4]) else top
        draw.line((x, span_top, x, bottom), fill="#9aa7b6", width=1 if x not in (columns[0], columns[-1]) else 2)
    draw.line((columns[0], top, columns[-1], top), fill="#9aa7b6", width=2)
    draw.line((columns[0], top + 2 * header, columns[-1], top + 2 * header), fill="#9aa7b6", width=2)
    draw.line((columns[0], bottom, columns[-1], bottom), fill="#9aa7b6", width=2)
    return image


HEATING_TARGET = 21.0
ROOM_TEMPERATURES = (("Living room", 19.2, False), ("Kitchen", 20.1, False), ("Bedroom", 18.9, True),
                     ("Study", 19.6, False), ("Nursery", 17.8, False), ("Bathroom", 22.3, False),
                     ("Guest room", 19.4, False), ("Laundry", 18.0, True))


def thermostat_dashboard():
    """Home heating app. Target 21.0 °C. More than 1.5 °C below target: Living room (1.8), Bedroom
    (2.1), Nursery (3.2), Guest room (1.6), Laundry (3.0); of those, heating is off in Living room,
    Nursery and Guest room: 3. Study (1.4 below) is the near miss. On/off is shown only by the switch."""
    image, draw = canvas(780, 620, "#eef1f5")
    draw.rounded_rectangle((30, 24, 750, 112), 14, fill="#1f3b5c")
    draw.text((56, 44), "Home heating", font=font(26, True), fill="white")
    draw.text((56, 80), "Target temperature, all rooms", font=font(16), fill="#c8d4e3")
    draw.text((724, 68), f"{HEATING_TARGET:.1f} °C", font=font(40, True), fill="white", anchor="rm")
    for index, (room, temperature, heating) in enumerate(ROOM_TEMPERATURES):
        column, row = index % 2, index // 2
        x0, y0 = 30 + column * 368, 132 + row * 118
        draw.rounded_rectangle((x0, y0, x0 + 352, y0 + 104), 12, fill="white")
        draw.text((x0 + 22, y0 + 20), room, font=font(19, True), fill=INK)
        draw.text((x0 + 22, y0 + 52), f"{temperature:.1f} °C", font=font(30), fill=INK)
        draw.text((x0 + 232, y0 + 24), "Heating", font=font(14), fill=MUTED)
        sx, sy = x0 + 240, y0 + 52
        draw.rounded_rectangle((sx, sy, sx + 70, sy + 34), 17, fill="#2e9d5b" if heating else "#c3c8cf")
        knob = sx + 53 if heating else sx + 17
        draw.ellipse((knob - 14, sy + 3, knob + 14, sy + 31), fill="white")
    return image


ENERGY = (("Jan", 300, 150, 100), ("Feb", 250, 100, 50), ("Mar", 350, 250, 100),
          ("Apr", 200, 250, 50), ("May", 150, 300, 50), ("Jun", 100, 350, 50))


def energy_stacked():
    """Stacked bars of household electricity by source (solar, grid, battery). Solar's share of each
    month: 55%, 62.5%, 50%, 40%, 30%, 20%, so February is highest; March has the most solar in kWh."""
    image, draw = canvas(820, 560)
    left, right, top, bottom, high = 90, 790, 100, 490, 750
    y = lambda value: bottom - (bottom - top) * value / high
    draw.text((left, 22), "Household electricity by source (kWh)", font=font(24, True), fill=INK)
    for value in range(0, high + 1, 50):
        draw.line((left, y(value), right, y(value)), fill=LINE if value else MUTED, width=1)
        if value % 100 == 0:
            draw.text((left - 12, y(value)), str(value), font=font(15), fill=MUTED, anchor="rm")
    colors = (("Solar", "#f2b705"), ("Grid", "#5b6b7d"), ("Battery", "#2e9d5b"))
    for index, (name, color) in enumerate(colors):
        lx = 470 + index * 108
        draw.rectangle((lx, 62, lx + 18, 80), fill=color)
        draw.text((lx + 26, 71), name, font=font(17), fill=INK, anchor="lm")
    for index, (month, *values) in enumerate(ENERGY):
        x0 = left + 40 + index * 112
        base = 0
        for value, (_, color) in zip(values, colors):
            draw.rectangle((x0, y(base + value) + 1, x0 + 62, y(base)), fill=color)
            base += value
        draw.text((x0 + 31, bottom + 20), month, font=font(16), fill=MUTED, anchor="mm")
    return image


STOPS = ("Hill Road", "Park Street", "Market Square", "Central Station")
TRIPS = (("13:50", "14:02", "14:11", "14:25", ""), ("14:05", "14:16", "14:25", "14:38", "*"),
         ("14:20", "14:31", "14:40", "14:55", ""), ("14:35", "—", "14:50", "15:02", ""),
         ("14:50", "15:01", "15:10", "15:24", ""))


def bus_timetable():
    """Route 12 timetable. On a Saturday at Park Street at 14:10: the 14:02 has gone, the 14:16 trip is
    marked * (weekdays only), so the first bus is the 14:31, reaching Central Station at 14:55.
    The 14:35 express does not stop at Park Street."""
    image, draw = canvas(860, 440)
    draw.rectangle((0, 0, 860, 70), fill="#0f6b5c")
    draw.text((30, 35), "Route 12  ·  Hill Road to Central Station", font=font(26, True), fill="white", anchor="lm")
    draw.text((30, 96), "Afternoon departures", font=font(18, True), fill=INK)
    first, width, top = 250, 118, 130
    for index, (*_, mark) in enumerate(TRIPS):
        draw.text((first + index * width + width / 2, top), f"Trip {index + 1}{mark}", font=font(17, True), fill=INK, anchor="mm")
    for row, stop in enumerate(STOPS):
        y = top + 50 + row * 50
        if row % 2 == 0:
            draw.rectangle((24, y - 25, 836, y + 25), fill="#eef5f3")
        draw.text((36, y), stop, font=font(19), fill=INK, anchor="lm")
        for index, trip in enumerate(TRIPS):
            draw.text((first + index * width + width / 2, y), trip[row], font=font(19), fill=INK, anchor="mm")
    draw.text((30, 380), "* Runs Monday to Friday only.   — Does not stop here.", font=font(16), fill=MUTED)
    return image


def parcel_label():
    """Shipping label photographed sideways (rotated 90°). Tracking number TRK-58K2-9940; the order
    number ORD-58K2-9904 is a near-identical decoy, and the reference code sits beside them."""
    label, draw = canvas(620, 400)
    draw.rectangle((4, 4, 616, 396), outline=INK, width=3)
    draw.text((24, 20), "PARCEL EXPRESS", font=font(26, True), fill=INK)
    draw.text((24, 58), "SHIP TO: R. Okafor, 22 Wattle Lane, Bendleigh 3204", font=font(15), fill=INK)
    draw.line((4, 88, 616, 88), fill=INK, width=2)
    rows = (("ORDER NO", "ORD-58K2-9904"), ("REF", "B-2209-58K"), ("TRACKING NO", "TRK-58K2-9940"))
    for index, (key, value) in enumerate(rows):
        y = 104 + index * 42
        draw.text((24, y), key, font=font(15, True), fill=MUTED)
        draw.text((190, y - 4), value, font=font(24, True, mono=True), fill=INK)
    for index in range(70):  # a deterministic barcode-like pattern
        width = (1, 2, 3, 1, 4, 2)[(index * 7) % 6]
        x = 40 + index * 7.6
        draw.rectangle((x, 250, x + width, 350), fill=INK)
    draw.text((310, 372), "TRK-58K2-9940", font=font(14, mono=True), fill=INK, anchor="mm")
    rotated = label.rotate(90, expand=True)
    image, _ = canvas(560, 760, "#b98d5b")
    image.paste(rotated, (80, 70))
    return image


SUITE_1_8 = (("visitors-lines", visitors_lines), ("clock-departures", clock_departures),
             ("grocery-receipt", grocery_receipt), ("flowchart", flowchart), ("floor-plan", floor_plan),
             ("store-table", store_table), ("thermostat-dashboard", thermostat_dashboard),
             ("energy-stacked", energy_stacked), ("bus-timetable", bus_timetable), ("parcel-label", parcel_label))
