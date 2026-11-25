"""Regenerate the challenge vision fixtures (Pillow and macOS fonts required).

The runner never imports this script; the PNG files are committed. The four
original control images (shapes, objects, label, badge) predate this script.
"""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

HERE = Path(__file__).resolve().parent
BACKGROUND = "#faf8f2"
COLORS = {"green": "#23843f", "red": "#c62828", "blue": "#1565c0", "yellow": "#f2b705", "orange": "#e37e1b"}
SANS = "/System/Library/Fonts/Supplemental/Arial Bold.ttf"
MONO = "/System/Library/Fonts/Menlo.ttc"


def shape(draw, kind, color, x, y, size=44):
    box = (x - size / 2, y - size / 2, x + size / 2, y + size / 2)
    if kind == "circle":
        draw.ellipse(box, fill=COLORS[color])
    elif kind == "square":
        draw.rectangle(box, fill=COLORS[color])
    else:
        draw.polygon([(x, box[1]), (box[2], box[3]), (box[0], box[3])], fill=COLORS[color])


def counting():
    """Five green triangles among green circles and red triangles."""
    items = [("triangle", "green", 60, 60), ("circle", "green", 150, 50), ("triangle", "red", 250, 70),
             ("triangle", "green", 360, 55), ("square", "blue", 430, 140), ("triangle", "red", 70, 160),
             ("triangle", "green", 175, 150), ("circle", "green", 290, 165), ("triangle", "green", 120, 260),
             ("triangle", "red", 230, 250), ("circle", "green", 340, 260), ("triangle", "green", 430, 255),
             ("triangle", "red", 380, 170), ("square", "blue", 30, 250)]
    image = Image.new("RGB", (480, 320), BACKGROUND)
    draw = ImageDraw.Draw(image)
    for item in items:
        shape(draw, *item)
    return image


def position():
    """Two rows of shapes; one red square, with a blue triangle to its right."""
    rows = [[("circle", "yellow"), ("square", "red"), ("triangle", "blue"), ("circle", "green")],
            [("square", "blue"), ("circle", "red"), ("triangle", "orange"), ("square", "green")]]
    image = Image.new("RGB", (480, 320), BACKGROUND)
    draw = ImageDraw.Draw(image)
    for row, y in zip(rows, (100, 220)):
        for index, (kind, color) in enumerate(row):
            shape(draw, kind, color, 75 + index * 110, y, size=64)
    return image


def chart():
    """Unnumbered bar chart; D is tallest and E is second tallest."""
    heights = {"A": 90, "B": 160, "C": 130, "D": 210, "E": 185}
    image = Image.new("RGB", (480, 320), BACKGROUND)
    draw = ImageDraw.Draw(image)
    font = ImageFont.truetype(SANS, 22)
    draw.line((40, 270, 450, 270), fill="#333333", width=2)
    for index, (label, height) in enumerate(heights.items()):
        left = 60 + index * 78
        draw.rectangle((left, 270 - height, left + 50, 270), fill=COLORS["blue"])
        draw.text((left + 25, 295), label, font=font, fill="#222222", anchor="mm")
    return image


def receipt():
    """Small monospaced receipt; the subtotal and total are arithmetically consistent."""
    lines = ["CORNER CAFE", "", "COFFEE        3.50", "BAGEL         2.75", "SALAD        12.40",
             "SUBTOTAL     18.65", "TAX           1.49", "TOTAL        20.14"]
    image = Image.new("RGB", (480, 320), "#ffffff")
    draw = ImageDraw.Draw(image)
    font = ImageFont.truetype(MONO, 20)
    for index, line in enumerate(lines):
        draw.text((140, 40 + index * 30), line, font=font, fill="#222222")
    return image


def table():
    """Parts table; QTY x PRICE summed is 1.00 + 1.00 + 6.80 + 3.45 = 12.25."""
    rows = [("ITEM", "QTY", "PRICE"), ("BOLT", "4", "0.25"), ("NUT", "10", "0.10"),
            ("HINGE", "2", "3.40"), ("SPRING", "3", "1.15")]
    image = Image.new("RGB", (480, 320), "#ffffff")
    draw = ImageDraw.Draw(image)
    font = ImageFont.truetype(SANS, 22)
    for index, row in enumerate(rows):
        top = 40 + index * 48
        draw.line((60, top + 44, 420, top + 44), fill="#bbbbbb", width=1)
        for text, x in zip(row, (80, 250, 340)):
            draw.text((x, top + 22), text, font=font, fill="#222222", anchor="lm")
    return image


def grid():
    """Four rows of five cells; rows have 2, 0, 4 and 3 red cells, so row 3 has the most."""
    rows = ["rbryb", "ybgbg", "rrgrr", "brryr"]
    names = {"r": "red", "b": "blue", "y": "yellow", "g": "green"}
    image = Image.new("RGB", (480, 320), BACKGROUND)
    draw = ImageDraw.Draw(image)
    for row, colors in enumerate(rows):
        for column, code in enumerate(colors):
            left, top = 65 + column * 72, 25 + row * 70
            draw.rectangle((left, top, left + 62, top + 60), fill=COLORS[names[code]])
    return image


def frame():
    """Four circles and two squares inside a black frame; three circles and a triangle outside."""
    image = Image.new("RGB", (480, 320), BACKGROUND)
    draw = ImageDraw.Draw(image)
    draw.rectangle((120, 70, 400, 250), outline="#111111", width=4)
    for item in [("circle", "blue", 170, 120), ("square", "orange", 260, 115), ("circle", "red", 350, 130),
                 ("circle", "green", 190, 205), ("square", "blue", 280, 200), ("circle", "yellow", 360, 205),
                 ("circle", "blue", 55, 90), ("circle", "green", 440, 285), ("circle", "red", 60, 260),
                 ("triangle", "orange", 260, 35)]:
        shape(draw, *item, size=40)
    return image


from suite_1_8_fixtures import SUITE_1_8  # noqa: E402  (drawings for the suite 1.8 vision track)

if __name__ == "__main__":
    for name, make in (("counting", counting), ("position", position), ("chart", chart), ("receipt", receipt),
                       ("table", table), ("grid", grid), ("frame", frame)):
        make().save(HERE / f"{name}.png", optimize=True)
        print(f"wrote fixtures/{name}.png")
    (HERE / "v1_8").mkdir(exist_ok=True)
    for name, make in SUITE_1_8:
        make().save(HERE / "v1_8" / f"{name}.png", optimize=True)
        print(f"wrote fixtures/v1_8/{name}.png")
