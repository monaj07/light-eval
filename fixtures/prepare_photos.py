"""Prepare the suite 1.8 vision photos from the author's originals (Pillow required).

The originals are not committed: they carry camera metadata, including GPS
location. This script records exactly what was done to each one, so the
committed JPEGs can be checked against it:

- rotate upright from the EXIF orientation, then drop all metadata;
- crop where the detail a question needs would be unreadable at the final size;
- blur faces of people who did not agree to be photographed;
- scale so the longer side is at most 1024 pixels, and save as JPEG quality 90.

Usage: python3 fixtures/prepare_photos.py ~/path/to/originals
"""
import sys
from pathlib import Path

from PIL import Image, ImageFilter, ImageOps

OUT = Path(__file__).resolve().parent / "v1_8" / "photos"
LONG_SIDE = 1024

# Original file -> (published name, crop box as fractions of the upright image or None,
#                   face boxes to blur as fractions of the cropped image).
PHOTOS = {
    "01.jpg": ("kangaroos.jpg", None, ()),
    "02.jpg": ("supermarket.jpg", (0.40, 0.10, 1.00, 0.70),
               ((0.075, 0.42, 0.125, 0.49), (0.19, 0.43, 0.24, 0.50), (0.28, 0.435, 0.32, 0.50),
                (0.43, 0.42, 0.47, 0.475), (0.47, 0.42, 0.51, 0.475))),
    "03.jpg": ("park-map.jpg", (0.00, 0.28, 0.45, 0.58), ()),
    "04.jpg": ("pi-gpio.jpg", (0.50, 0.22, 0.92, 0.78), ()),
    "05.jpg": ("ice-cream-menu.jpg", None, ()),
    "06.jpg": ("fruit-tree.jpg", None, ()),
    "07.jpg": ("television.jpg", (0.25, 0.28, 0.80, 0.75), ()),
    "08.jpg": ("welcome-sign.jpg", None,  # faces in the sign's printed photo
               ((0.61, 0.315, 0.66, 0.37), (0.66, 0.315, 0.71, 0.37), (0.70, 0.32, 0.75, 0.375))),
    "09.jpg": ("penguins.jpg", None, ()),
    "10.jpg": ("restaurant-receipt.jpg", None, ()),
}


def fraction_box(box, size):
    width, height = size
    left, top, right, bottom = box
    return round(left * width), round(top * height), round(right * width), round(bottom * height)


def prepare(source, crop, faces):
    image = ImageOps.exif_transpose(Image.open(source)).convert("RGB")
    if crop:
        image = image.crop(fraction_box(crop, image.size))
    for face in faces:
        box = fraction_box(face, image.size)
        region = image.crop(box)
        radius = max(region.size) // 3
        image.paste(region.filter(ImageFilter.GaussianBlur(radius)), box)
    scale = LONG_SIDE / max(image.size)
    if scale < 1:
        image = image.resize((round(image.width * scale), round(image.height * scale)), Image.LANCZOS)
    return image


def main(originals):
    OUT.mkdir(parents=True, exist_ok=True)
    for original, (name, crop, faces) in PHOTOS.items():
        image = prepare(Path(originals).expanduser() / original, crop, faces)
        image.save(OUT / name, "JPEG", quality=90, optimize=True)  # no exif= argument: metadata is dropped
        print(f"wrote fixtures/v1_8/photos/{name} {image.size[0]}x{image.size[1]}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    main(sys.argv[1])
