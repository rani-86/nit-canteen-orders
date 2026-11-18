"""
order_ocr.py - read a photo of a filled NIT Canteen order pad

The pad is printed by canteen_tokens.py (order_pad.html) with a fixed grid,
and its columns are saved in order_pad_layout.json. This module:

  1. finds the pad's table in the photo and straightens it
     (handles tilt, perspective, sideways / upside-down photos, uneven light);
  2. finds the grid lines, so every cell is located by its PRINTED row
     number - the order number is never "read", it comes from the row position;
  3. reads the tick boxes (Half, Parcel, extras) with plain image processing:
     the share of dark pixels in each cell. Faint marks are flagged for checking;
  4. reads the handwriting (Dish, Qty, Other instructions) with a vision API
     ONLY if a key is set: GEMINI_API_KEY (Google's free tier) or
     ANTHROPIC_API_KEY. Without a key - or if the API fails -
     those fields stay blank and the user types them from the row pictures.
     Standard Tesseract OCR is not used: it is not reliable on handwriting;
  5. after the user has checked everything (see ocr_app.py), validates the
     rows, converts them to data.csv's columns, skips duplicates and appends.

Nothing in this module saves anything on its own: ocr_app.py only calls
save_verified() after the user presses "Confirm and Save".
"""

import base64
import csv
import io
import json
import os
import re
import urllib.error
import urllib.request

import cv2
import numpy as np
from PIL import Image, ImageOps

# ---------------- settings you can change ----------------
TICK_INK = 0.07                # share of dark pixels above which a box counts as ticked
UNSURE_BAND = (0.025, 0.12)    # tick ink in this range is highlighted for checking
TEXT_INK = 0.012               # a Dish / Qty / Other cell with more ink than this has writing
TABLE_WIDTH = 2400             # width of the straightened table, in pixels
EDGE_MARGIN = 20               # the table's outer border lands this far inside the straightened image
LAYOUT_FILE = "order_pad_layout.json"
DATA_FILE = "data.csv"

API_TIMEOUT = 60               # seconds
# Google Gemini - has a free tier (rate-limited)
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
GEMINI_MODEL = os.environ.get("CANTEEN_GEMINI_MODEL", "gemini-2.5-flash")
# Anthropic Claude - paid
API_URL = "https://api.anthropic.com/v1/messages"
API_VERSION = "2023-06-01"
API_MODEL = os.environ.get("CANTEEN_OCR_MODEL", "claude-haiku-5-5")

# variant words at the start of a dish name ("veg maggi" -> variant veg, item maggi)
VARIANTS = ["mix non veg", "mix non-veg", "mix veg", "chicken egg", "veg", "egg",
            "chicken", "paneer", "mushroom", "masala", "plain"]

DATA_COLUMNS = ["row_id", "source", "date", "raw_text", "item", "variant", "portion",
                "qty", "modifications", "parcel", "group_id", "group_total",
                "time_ordered", "time_served", "how_identified", "issue", "notes"]
# ---------------------------------------------------------


class OcrError(Exception):
    """A problem the user can fix (retake the photo, reprint the pad, ...)."""


# ---------------- layout and image loading ----------------

def load_layout(path=LAYOUT_FILE):
    try:
        with open(path, encoding="utf-8") as f:
            layout = json.load(f)
    except FileNotFoundError:
        raise OcrError(f"{path} not found. Run canteen_tokens.py first - it prints the pad "
                       "and saves its layout.")
    except json.JSONDecodeError:
        raise OcrError(f"{path} is damaged. Run canteen_tokens.py again to rebuild it.")
    if not {"rows", "ticks", "columns"} <= set(layout):
        raise OcrError(f"{path} is missing rows/ticks/columns. Run canteen_tokens.py again.")
    return layout


def load_image(data):
    """Bytes of a JPG/PNG -> RGB numpy array, turned upright using the photo's EXIF."""
    try:
        image = ImageOps.exif_transpose(Image.open(io.BytesIO(data))).convert("RGB")
    except Exception:
        raise OcrError("This file couldn't be opened as an image. Upload a JPG or PNG photo.")
    return np.array(image)


def ink_mask(gray):
    """Dark ink -> 255, paper -> 0. Adaptive, so shadows and uneven light matter less."""
    blur = cv2.GaussianBlur(gray, (5, 5), 0)
    return cv2.adaptiveThreshold(blur, 255, cv2.ADAPTIVE_THRESH_MEAN_C,
                                 cv2.THRESH_BINARY_INV, 51, 12)


# ---------------- 1. find and straighten the table ----------------

def order_corners(pts):
    """4 points -> top-left, top-right, bottom-right, bottom-left."""
    pts = np.array(pts, dtype="float32")
    s, d = pts.sum(axis=1), np.diff(pts, axis=1).ravel()
    return np.array([pts[s.argmin()], pts[d.argmin()], pts[s.argmax()], pts[d.argmax()]],
                    dtype="float32")


def find_table_corners(gray):
    """Corners of the order-pad table. All grid lines are joined, so the table is
    the biggest connected block of ink that doesn't touch the photo's edge (the
    desk or page edge usually does). Printed text near the table isn't joined
    to it, so it doesn't pull the corners out of place."""
    mask = cv2.morphologyEx(ink_mask(gray), cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
    h, w = mask.shape
    count, labels, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    best, best_pixels = 0, 0
    for i in range(1, count):
        x, y, bw, bh, pixels = stats[i]
        touches_edge = x == 0 or y == 0 or x + bw >= w or y + bh >= h
        if not touches_edge and pixels > best_pixels:
            best, best_pixels = i, pixels
    if not best:
        raise OcrError("No table found in the photo.")
    x, y, bw, bh = stats[best, :4]
    if bw * bh < 0.15 * h * w:
        raise OcrError("The table is too small in the photo, or not in it. Take the photo "
                       "closer, with the whole table visible.")
    component = (labels == best).astype(np.uint8) * 255
    contours, _ = cv2.findContours(component, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    hull = cv2.convexHull(max(contours, key=cv2.contourArea))
    peri = cv2.arcLength(hull, True)
    for eps in (0.01, 0.02, 0.03, 0.05):
        approx = cv2.approxPolyDP(hull, eps * peri, True)
        if len(approx) == 4:
            return order_corners(approx.reshape(4, 2))
    return order_corners(cv2.boxPoints(cv2.minAreaRect(hull)))


def straighten(rgb, corners, margin=EDGE_MARGIN):
    tl, tr, br, bl = corners
    width = (np.linalg.norm(tr - tl) + np.linalg.norm(br - bl)) / 2
    height = (np.linalg.norm(bl - tl) + np.linalg.norm(br - tr)) / 2
    out_w = TABLE_WIDTH
    out_h = max(int(TABLE_WIDTH * height / max(width, 1)), 50)
    dst = np.array([[margin, margin], [out_w - margin, margin],
                    [out_w - margin, out_h - margin], [margin, out_h - margin]], dtype="float32")
    matrix = cv2.getPerspectiveTransform(corners, dst)
    return cv2.warpPerspective(rgb, matrix, (out_w, out_h), borderValue=(255, 255, 255))


# ---------------- 2. find the grid ----------------

def line_positions(profile, min_length):
    """Centres of the runs where a line-profile is at least min_length pixels long."""
    above = np.where(profile >= min_length)[0]
    runs, start = [], None
    for i, idx in enumerate(above):
        if start is None:
            start = idx
        if i == len(above) - 1 or above[i + 1] - idx > 6:
            runs.append((start + idx) // 2)
            start = None
    return runs


def with_edges(lines, first, last, near=15):
    """Add the table's outer border lines if the photo made them too faint to find.
    After straightening, the outer border is always at first/last."""
    lines = list(lines)
    if not any(abs(x - first) <= near for x in lines):
        lines.insert(0, first)
    if not any(abs(x - last) <= near for x in lines):
        lines.append(last)
    return lines


def find_grid(table_rgb, layout, add_edges=True):
    """Return (row_lines, col_lines) in the straightened table, or raise OcrError.
    A line counts if it runs across at least half the table. Lines are thickened
    a little first, so a slightly wavy line in a photo still counts as one line."""
    gray = cv2.cvtColor(table_rgb, cv2.COLOR_RGB2GRAY)
    mask = ink_mask(gray)
    h, w = mask.shape
    thick_h = cv2.dilate(mask, np.ones((5, 1), np.uint8))
    thick_v = cv2.dilate(mask, np.ones((1, 5), np.uint8))
    horiz = cv2.morphologyEx(thick_h, cv2.MORPH_OPEN, np.ones((1, max(w // 20, 10)), np.uint8))
    vert = cv2.morphologyEx(thick_v, cv2.MORPH_OPEN, np.ones((max(h // 20, 10), 1), np.uint8))
    rows = line_positions((horiz > 0).sum(axis=1), 0.5 * w)
    cols = line_positions((vert > 0).sum(axis=0), 0.6 * h)
    if add_edges:                     # only valid on a straightened table
        rows = with_edges(rows, EDGE_MARGIN, h - EDGE_MARGIN)
        cols = with_edges(cols, EDGE_MARGIN, w - EDGE_MARGIN)
    need_cols, need_rows = len(layout["columns"]) + 1, layout["rows"] + 1
    if len(cols) != need_cols:
        raise OcrError(f"Couldn't read the grid: found {len(cols)} column lines, expected "
                       f"{need_cols}. Retake the photo flat, in good light, with the whole "
                       "table visible - or check it's the pad printed with the current layout.")
    if len(rows) < need_rows:
        raise OcrError(f"Couldn't read the grid: found {len(rows)} row lines, expected at "
                       f"least {need_rows}. Retake the photo with all {layout['rows']} rows visible.")
    return rows[-need_rows:], cols


def upright_table(rgb, layout):
    """Straighten the table and turn it the right way round.
    The 'Other instructions' column (last) is wider than the 'No.' column (first),
    which tells us if the photo is upside down."""
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    table = straighten(rgb, find_table_corners(gray))
    if table.shape[0] > table.shape[1]:                      # photo taken sideways
        table = cv2.resize(cv2.rotate(table, cv2.ROTATE_90_CLOCKWISE),
                           (TABLE_WIDTH, int(TABLE_WIDTH * table.shape[1] / table.shape[0])))
    rows, cols = find_grid(table, layout)
    if cols[1] - cols[0] > cols[-1] - cols[-2]:              # upside down
        table = cv2.rotate(table, cv2.ROTATE_180)
        rows, cols = find_grid(table, layout)
    return table, rows, cols


# ---------------- 3. read ink in every cell ----------------

def cell_ink(mask, x0, x1, y0, y1, margin=0.18):
    """Share of dark pixels inside a cell, ignoring its borders."""
    dx, dy = int((x1 - x0) * margin) + 2, int((y1 - y0) * margin) + 2
    inner = mask[y0 + dy:y1 - dy, x0 + dx:x1 - dx]
    return float((inner > 0).mean()) if inner.size else 0.0


def measure_rows(table, rows, cols, layout):
    """Ink in every cell of every row: {no: {"Dish": ink, "Qty": ink, ..., ticks..., "Other instructions": ink}}"""
    mask = ink_mask(cv2.cvtColor(table, cv2.COLOR_RGB2GRAY))
    measured = {}
    for r in range(layout["rows"]):
        y0, y1 = rows[r], rows[r + 1]
        measured[r + 1] = {name: cell_ink(mask, cols[c], cols[c + 1], y0, y1)
                           for c, name in enumerate(layout["columns"]) if name != "No."}
    return measured


def is_empty_row(ink, layout):
    texts = ("Dish", "Qty", "Other instructions")
    return (all(ink[t] <= TEXT_INK for t in texts)
            and all(ink[t] <= UNSURE_BAND[0] for t in layout["ticks"]))


# ---------------- 4. optional handwriting reading ----------------

def parse_handwriting_reply(text, row_numbers):
    """The model's JSON reply -> {no: {"dish", "qty", "other", "unsure"}}.
    Rows that weren't asked for are ignored, so nothing is invented."""
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < start:
        raise OcrError("The handwriting service didn't return a readable answer.")
    try:
        reply = json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        raise OcrError("The handwriting service didn't return a readable answer.")
    found = {}
    for item in reply.get("rows", []):
        try:
            no = int(item.get("no"))
        except (TypeError, ValueError):
            continue
        if no in row_numbers:
            found[no] = {"dish": str(item.get("dish") or "").strip(),
                         "qty": str(item.get("qty") or "").strip(),
                         "other": str(item.get("other") or "").strip(),
                         "unsure": [str(u) for u in item.get("unsure") or []]}
    return found


def handwriting_key():
    """(provider, key) from the environment. Gemini first, because it has a free tier."""
    gemini = os.environ.get("GEMINI_API_KEY", "").strip()
    claude = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if gemini:
        return "gemini", gemini
    if claude:
        return "anthropic", claude
    return None, ""


def handwriting_prompt(row_numbers, layout):
    return (
        "This is a straightened photo of a canteen order pad. Rows are numbered "
        f"01 to {layout['rows']:02d}. Columns, left to right: {', '.join(layout['columns'])}. "
        f"For rows {', '.join(f'{n:02d}' for n in row_numbers)} only, read the handwritten "
        "text in Dish, Qty and Other instructions. Ignore the tick-box columns. Copy exactly "
        "what is written - don't correct spelling and don't guess. Use an empty string for a "
        "blank cell. Write ? for any word you can't read and list that field (dish, qty or "
        "other) in \"unsure\". Reply with JSON only, for example: "
        '{"rows": [{"no": 1, "dish": "veg maggi", "qty": "", "other": "", "unsure": []}]}')


def post_json(url, body, headers):
    request = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"), method="POST",
                                     headers={"content-type": "application/json", **headers})
    try:
        with urllib.request.urlopen(request, timeout=API_TIMEOUT) as response:
            return json.load(response)
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            raise OcrError(f"The API key was rejected ({e.code}) - check it, or remove it and "
                           "type the dishes.")
        if e.code == 429:
            raise OcrError("The free limit for now has been reached (429) - wait a minute "
                           "and upload again, or type the dishes.")
        raise OcrError(f"The handwriting service returned an error ({e.code}).")
    except (urllib.error.URLError, TimeoutError, OSError):
        raise OcrError("Couldn't reach the handwriting service (no internet?).")


def read_handwriting(table, row_numbers, layout, api_key, provider="anthropic"):
    """Ask a vision model to read Dish, Qty and Other for the given rows only."""
    if not row_numbers:
        return {}
    image = Image.fromarray(table)
    image.thumbnail((1600, 1600))
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=85)
    picture = base64.b64encode(buffer.getvalue()).decode()
    prompt = handwriting_prompt(row_numbers, layout)
    if provider == "gemini":
        payload = post_json(
            GEMINI_URL.format(model=GEMINI_MODEL),
            {"contents": [{"parts": [{"inline_data": {"mime_type": "image/jpeg", "data": picture}},
                                     {"text": prompt}]}],
             "generationConfig": {"temperature": 0, "responseMimeType": "application/json"}},
            {"x-goog-api-key": api_key})
        parts = ((payload.get("candidates") or [{}])[0].get("content") or {}).get("parts") or []
        text = "".join(part.get("text", "") for part in parts)
    else:
        payload = post_json(
            API_URL,
            {"model": API_MODEL, "max_tokens": 2000, "messages": [{"role": "user", "content": [
                {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                             "data": picture}},
                {"type": "text", "text": prompt}]}]},
            {"x-api-key": api_key, "anthropic-version": API_VERSION})
        text = "".join(block.get("text", "") for block in payload.get("content", [])
                       if block.get("type") == "text")
    return parse_handwriting_reply(text, row_numbers)


# ---------------- the whole reading step ----------------

def build_rows(measured, handwriting, layout):
    """Rows for the verification table, with a 'Check' note for anything doubtful."""
    rows, ink_flags = [], {}
    for no, ink in measured.items():
        if is_empty_row(ink, layout):
            continue
        text = handwriting.get(no, {"dish": "", "qty": "", "other": "", "unsure": []})
        checks = []
        for label in layout["ticks"]:
            if UNSURE_BAND[0] < ink[label] < UNSURE_BAND[1]:
                checks.append(f"{label}: faint mark")
        has = {"dish": ink["Dish"] > TEXT_INK, "qty": ink["Qty"] > TEXT_INK,
               "other": ink["Other instructions"] > TEXT_INK}
        for field, column in (("dish", "Dish"), ("qty", "Qty"), ("other", "Other")):
            if has[field] and not text[field]:
                checks.append(f"{column}: type it from the picture")
            elif field in text["unsure"] or "?" in text[field]:
                checks.append(f"{column}: unsure reading")
        if not has["dish"]:
            checks.append("Dish: looks empty")
        row = {"No.": no, "Dish": text["dish"], "Qty": text["qty"]}
        for label in layout["ticks"]:
            row[label] = ink[label] >= TICK_INK
        row["Other"] = text["other"]
        row["Check"] = "; ".join(checks)
        rows.append(row)
        ink_flags[no] = has
    return rows, ink_flags


def read_order_pad(data, layout, api_key=None, provider="anthropic"):
    """Photo bytes -> everything the verification screen needs.
    Raises OcrError if the table can't be found; handwriting failures are
    reported as warnings and the user types those fields instead."""
    table, row_lines, col_lines = upright_table(load_image(data), layout)
    measured = measure_rows(table, row_lines, col_lines, layout)
    filled = [no for no, ink in measured.items() if not is_empty_row(ink, layout)]
    warnings, handwriting, used_api = [], {}, False
    if api_key and filled:
        try:
            handwriting = read_handwriting(table, filled, layout, api_key, provider)
            used_api = True
        except OcrError as e:
            warnings.append(f"Handwriting wasn't read automatically: {e} "
                            "Type the dish names from the row pictures.")
    rows, ink_flags = build_rows(measured, handwriting, layout)
    crops = {no: table[max(row_lines[no - 1] - 4, 0):row_lines[no] + 4, :]
             for no in filled}
    return {"rows": rows, "ink": ink_flags, "crops": crops, "table": table,
            "used_api": used_api, "warnings": warnings}


# ---------------- 5. validate, convert, save ----------------

def validate_rows(rows, ink_flags, layout):
    """Check the rows the user confirmed. Returns (clean_rows, errors).
    Writing that was seen on the photo can't be saved as blank: unknown
    information must not turn into 'no customization'."""
    clean, errors, seen = [], [], set()
    for row in rows:
        try:
            no = int(row.get("No."))
        except (TypeError, ValueError):
            errors.append(f"A row has no valid order number ('{row.get('No.')}').")
            continue
        label = f"Row {no:02d}"
        if no < 1:
            errors.append(f"{label}: the order number must be 1 or more.")
            continue
        if no in seen:
            errors.append(f"{label}: this order number appears twice.")
            continue
        seen.add(no)
        dish = str(row.get("Dish") or "").strip()
        qty = str(row.get("Qty") or "").strip()
        other = str(row.get("Other") or "").strip()
        flags = ink_flags.get(no, {})
        if not dish:
            errors.append(f"{label}: Dish is empty - type it, or remove the row if it isn't an order.")
        if not qty:
            if flags.get("qty"):
                errors.append(f"{label}: something is written in Qty - type it.")
            qty = "1"                                     # the pad's rule: blank = qty 1
        elif not qty.isdigit() or int(qty) < 1:
            errors.append(f"{label}: Qty '{qty}' must be a whole number, 1 or more.")
        if not other and flags.get("other"):
            errors.append(f"{label}: something is written in Other instructions - type it, "
                          "or write ? if it can't be read.")
        ticks = {t: bool(row.get(t)) for t in layout["ticks"]}
        clean.append({"no": no, "dish": dish, "qty": qty, "other": other, "ticks": ticks})
    return clean, errors


def split_dish(dish):
    """'Veg  Maggi' -> ('veg', 'maggi'); 'chai' -> ('', 'chai')."""
    text = " ".join(dish.lower().split())
    for variant in VARIANTS:
        if text.startswith(variant + " "):
            return variant, text[len(variant) + 1:]
    return "", text


def to_csv_rows(clean, date, page, filename, layout, used_api):
    """Convert confirmed rows to data.csv's columns. The pad's order number is
    kept in row_id (date/page/number), so it is never lost or renumbered."""
    extras = [t for t in layout["ticks"] if t not in ("Half", "Parcel")]
    out = []
    for r in sorted(clean, key=lambda r: r["no"]):
        variant, item = split_dish(r["dish"])
        mods = [e for e in extras if r["ticks"].get(e)] + ([r["other"]] if r["other"] else [])
        out.append({
            "row_id": f"{date}/p{page}/{r['no']:02d}",
            "source": f"orderpad_{date}_p{page}",
            "date": date,
            "raw_text": f"order pad photo {filename}, row {r['no']:02d}: {r['dish']}",
            "item": item, "variant": variant,
            "portion": "half" if r["ticks"].get("Half") else "full",
            "qty": r["qty"],
            "modifications": "; ".join(mods),
            "parcel": "Y" if r["ticks"].get("Parcel") else "N",
            "group_id": "", "group_total": "", "time_ordered": "", "time_served": "",
            "how_identified": "token", "issue": "",
            "notes": ("read from photo - handwriting by API" if used_api else
                      "read from photo - ticks automatic, text typed") + "; checked and confirmed by user",
        })
    return out


def save_verified(csv_rows, path=DATA_FILE):
    """Append confirmed rows to data.csv in its existing column order.
    Rows whose row_id is already in the file are skipped, never duplicated.
    Returns (saved_rows, duplicate_row_ids)."""
    header, existing = list(DATA_COLUMNS), set()
    if os.path.exists(path) and os.path.getsize(path) > 0:
        with open(path, newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            header = [h.strip() for h in (reader.fieldnames or DATA_COLUMNS)]
            existing = {(row.get("row_id") or "").strip() for row in reader}
        with open(path, "rb") as f:
            f.seek(-1, os.SEEK_END)
            needs_newline = f.read(1) not in (b"\n", b"\r")
    else:
        needs_newline = None                               # new file: write the header
    saved = [r for r in csv_rows if r["row_id"] not in existing]
    duplicates = [r["row_id"] for r in csv_rows if r["row_id"] in existing]
    if not saved:
        return [], duplicates
    with open(path, "a", newline="", encoding="utf-8") as f:
        if needs_newline:
            f.write("\n")
        writer = csv.DictWriter(f, fieldnames=header, extrasaction="ignore", lineterminator="\n")
        if needs_newline is None:
            writer.writeheader()
        for row in saved:
            writer.writerow({col: row.get(col, "") for col in header})
    return saved, duplicates
