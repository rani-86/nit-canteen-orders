"""
Builds SYNTHETIC test photos of a filled order pad for test_order_ocr.py.
These are not real orders and must never be copied into data.csv - they only
check that the grid, tick and row reading work on a tilted, unevenly lit photo.

Needs Google Chrome (to print the real order_pad.html) and pdftoppm. Run from the
project folder:   python tests/make_synthetic_fixtures.py
"""
import json
import os
import subprocess
import sys
import tempfile

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import canteen_tokens as ct   # noqa: E402
import order_ocr as ocr       # noqa: E402

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")
FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Oblique.ttf"

# what we write on the synthetic pad (made up, for testing only)
FILLED = {
    1: {"dish": "veg maggi"},
    2: {"dish": "veg maggi", "ticks": ["Half"]},
    3: {"dish": "veg maggi"},
    4: {"dish": "veg maggi", "ticks": ["Parcel"]},
    5: {"dish": "egg fried rice", "ticks": ["Spicy+", "No onion"]},
    6: {"dish": "veg maggi", "qty": "2"},
    7: {"dish": "chicken noodles", "other": "extra sauce"},
    8: {"dish": "veg maggi", "faint": ["Cheese+"]},
}


def render_pad(layout):
    with tempfile.TemporaryDirectory() as tmp:
        html = os.path.join(tmp, "pad.html")
        ct.write_order_pad(html, layout["ticks"][2:], rows=layout["rows"])
        pdf = os.path.join(tmp, "pad.pdf")
        subprocess.run(["google-chrome", "--headless", "--no-sandbox", "--disable-gpu",
                        "--no-pdf-header-footer", f"--print-to-pdf={pdf}", f"file://{html}"],
                       check=True, capture_output=True)
        subprocess.run(["pdftoppm", "-png", "-r", "150", "-singlefile", pdf,
                        os.path.join(tmp, "pad")], check=True)
        return np.array(Image.open(os.path.join(tmp, "pad.png")).convert("RGB"))


def tick(draw, x0, x1, y0, y1, color, width):
    w, h = x1 - x0, y1 - y0
    draw.line([(x0 + w * .25, y0 + h * .55), (x0 + w * .45, y0 + h * .8),
               (x0 + w * .78, y0 + h * .2)], fill=color, width=width)


def fill(page, layout):
    rows, cols = ocr.find_grid(page, layout)
    rows = rows[-(layout["rows"] + 1):]
    col = {name: (cols[i], cols[i + 1]) for i, name in enumerate(layout["columns"])}
    image = Image.fromarray(page)
    draw = ImageDraw.Draw(image)
    font = ImageFont.truetype(FONT, int((rows[1] - rows[0]) * 0.62))
    ink = (25, 35, 110)
    for no, order in FILLED.items():
        y0, y1 = rows[no - 1], rows[no]
        for key, column in (("dish", "Dish"), ("qty", "Qty"), ("other", "Other instructions")):
            if order.get(key):
                draw.text((col[column][0] + 8, y0 + 3), order[key], font=font, fill=ink)
        for label in order.get("ticks", []):
            tick(draw, *col[label], y0, y1, ink, 4)
        for label in order.get("faint", []):
            x0, x1 = col[label]
            draw.line([(x0 + (x1 - x0) * .4, y0 + (y1 - y0) * .5),
                       (x0 + (x1 - x0) * .55, y0 + (y1 - y0) * .6)], fill=(150, 150, 150), width=2)
    return np.array(image)


def as_phone_photo(page, angle=6, seed=1):
    rng = np.random.default_rng(seed)
    h, w = page.shape[:2]
    pad = 160
    desk = np.full((h + 2 * pad, w + 2 * pad, 3), (70, 55, 45), np.uint8)
    desk[pad:pad + h, pad:pad + w] = page
    H, W = desk.shape[:2]
    src = np.float32([[0, 0], [W, 0], [W, H], [0, H]])
    dst = np.float32([[40, 30], [W - 10, 70], [W - 60, H - 20], [10, H - 60]])
    photo = cv2.warpPerspective(desk, cv2.getPerspectiveTransform(src, dst), (W, H),
                                borderValue=(70, 55, 45))
    rot = cv2.getRotationMatrix2D((W / 2, H / 2), angle, 1.0)
    photo = cv2.warpAffine(photo, rot, (W, H), borderValue=(70, 55, 45))
    light = np.linspace(0.72, 1.04, W)[None, :, None] * np.linspace(0.9, 1.0, H)[:, None, None]
    photo = np.clip(photo * light + rng.normal(0, 6, photo.shape), 0, 255).astype(np.uint8)
    return cv2.GaussianBlur(photo, (3, 3), 0)


def save(name, rgb):
    Image.fromarray(rgb).save(os.path.join(OUT, name), quality=82)


def main():
    os.makedirs(OUT, exist_ok=True)
    layout = ocr.load_layout()
    blank = render_pad(layout)
    filled = fill(blank, layout)
    save("synthetic_scan.png", filled)
    save("synthetic_photo.jpg", as_phone_photo(filled, angle=6))
    save("synthetic_photo_upside_down.jpg", cv2.rotate(as_phone_photo(filled, -4, 2), cv2.ROTATE_180))
    save("synthetic_empty_pad.jpg", as_phone_photo(blank, angle=3, seed=3))
    save("not_a_pad.jpg", np.full((900, 1200, 3), 235, np.uint8))
    with open(os.path.join(OUT, "synthetic_expected.json"), "w") as f:
        json.dump({"note": "SYNTHETIC test fixture - not real orders", "layout": layout,
                   "filled": FILLED}, f, indent=2)
    print("fixtures written to", OUT)


if __name__ == "__main__":
    main()
