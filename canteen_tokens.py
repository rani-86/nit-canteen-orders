"""
canteen_tokens.py - NIT Canteen order mix-up checker

Supports a paper system for Kamal Singh at NIT Canteen: a numbered order pad
plus matching numbered tokens. It reads data.csv (orders from Kamal's notepad
and my own observations) and:

  1. checks every row and reports problems by line number, without crashing;
  2. finds POTENTIAL mix-up risks: the same dish written more than once on
     the same notepad page, and classifies each pair of such orders as a
     customization conflict, an identification/delivery ambiguity, or
     "can't classify" (details unreadable);
  3. writes a numbered token slip for every readable order (slips.txt);
  4. recommends how many reusable tokens to keep, from timestamped orders
     (busiest moment + 25%), or says clearly that it can't yet;
  5. suggests batch preparation using the canteen's cooking rule
     (order_clustering.py -> batch_report.csv, individual_orders.csv);
  6. makes a printable numbered order pad (order_pad.html).

Usage:
    python canteen_tokens.py              # reads data.csv
    python canteen_tokens.py other.csv    # reads another file

Only uses the Python standard library.
"""

import csv
import html
import json
import math
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from itertools import combinations

# ---------------- settings you can change ----------------
MIN_LOOKALIKE = 2      # same dish appearing this many times on one page = a risk group
TOKEN_BUFFER = 1.25    # safety buffer on top of the busiest moment (1.25 = 25% extra)
MIN_TIMED_ROWS = 5     # orders with both timestamps needed before recommending tokens
NEXT_DAY_BEFORE = 12   # times before 12:00 count as "after midnight" (night canteen)
PAD_ROWS = 25          # numbered rows on one printed order pad
MIN_DATA_EXTRA = 2     # an extra must appear this many times in data.csv to get a tick box
MAX_TICK_BOXES = 8     # most tick boxes on the pad

# Extras I heard students ask for at the canteen. Kamal mostly does NOT write
# these down, so they are not in data.csv - they come from my fieldwork.
OBSERVED_EXTRAS = ["Spicy+", "Less spicy", "Cheese+", "No onion", "No corn", "Less oil"]

SLIPS_FILE = "slips.txt"
PAD_FILE = "order_pad.html"
PAD_LAYOUT_FILE = "order_pad_layout.json"   # tells order_ocr.py which columns the printed pad has
# ---------------------------------------------------------

REQUIRED_COLUMNS = ["source", "item", "variant"]   # can't analyse orders without these
OPTIONAL_COLUMNS = ["row_id", "portion", "qty", "modifications", "parcel",
                    "time_ordered", "time_served"]  # treated as blank if the column is missing


# ---------------- reading and checking ----------------

def clean(value):
    return (value or "").strip()


def unknown(value):
    """True if the value is blank or just question marks (couldn't read it)."""
    return value.replace("?", "").strip() == ""


def has_readable_dish(row):
    """Item must be readable. Variant may be blank (e.g. 'chai'), but not just '?'."""
    variant = row.get("variant") or ""
    return not unknown(row.get("item") or "") and not (variant.strip() and unknown(variant))


def parse_time(text):
    """'23:05' -> datetime on a dummy day; '00:35' -> the next day.
    Returns None if blank or not a real HH:MM time."""
    try:
        t = datetime.strptime(text, "%H:%M")
    except ValueError:
        return None
    if t.hour < NEXT_DAY_BEFORE:
        t += timedelta(days=1)
    return t


def read_orders(path):
    """Read the CSV without crashing.
    Returns (rows, issues, fatal):
      rows   - cleaned order dicts; each has '_line' (its line in the file)
      issues - list of (line_number, kind, reason); kind is 'skipped' for a
               row that couldn't be used, 'note' for a missing optional column
      fatal  - an error message if nothing can be analysed, else None"""
    issues = []
    try:
        with open(path, newline="", encoding="utf-8-sig") as f:   # -sig: handles Excel's BOM
            reader = csv.DictReader(f)
            header = [clean(h) for h in (reader.fieldnames or [])]
            reader.fieldnames = header
            missing = [c for c in REQUIRED_COLUMNS if c not in header]
            if missing:
                return [], [], f"{path} is missing required column(s): {', '.join(missing)}"
            absent = [c for c in OPTIONAL_COLUMNS if c not in header]
            if absent:
                issues.append((1, "note", f"column(s) {', '.join(absent)} missing - treated as blank"))

            rows = []
            for raw in reader:
                line = reader.line_num
                if None in raw:                       # more values than the header
                    issues.append((line, "skipped", f"{len(raw[None])} more value(s) than the "
                                                    "header (unquoted comma?)"))
                    continue
                if any(v is None for v in raw.values()):   # fewer values than the header
                    issues.append((line, "skipped", "fewer values than the header"))
                    continue
                row = {c: clean(raw.get(c)) for c in REQUIRED_COLUMNS + OPTIONAL_COLUMNS}
                if not any(row.values()):
                    issues.append((line, "skipped", "empty row"))
                    continue
                row["_line"] = line
                row["_absent"] = tuple(absent)   # columns this file didn't have
                rows.append(row)
    except FileNotFoundError:
        return [], [], f"Can't find {path}. Run this from the folder that has data.csv."
    except UnicodeDecodeError:
        return [], [], f"{path} is not a UTF-8 text file. Save it as CSV (UTF-8)."
    except csv.Error as e:
        return [], [], f"Couldn't read {path} as CSV: {e}"
    return rows, issues, None


def check_row(row, seen_ids):
    """Return a list of problems with this row (empty list = fine).
    A row with problems is still used for whatever can be read from it."""
    problems = []
    rid = row["row_id"]
    if rid and rid in seen_ids:
        problems.append(f"duplicate row_id '{rid}'")
    seen_ids.add(rid)
    if not row["item"]:
        problems.append("item is empty")
    if row["portion"] not in ("half", "full", "?", ""):
        problems.append(f"portion '{row['portion']}' should be half/full")
    qty = row["qty"]
    if qty not in ("?", ""):
        if not qty.isdigit():
            problems.append(f"qty '{qty}' is not a whole number")
        elif int(qty) < 1:
            problems.append(f"qty '{qty}' must be at least 1")
    if row["parcel"] not in ("Y", "N", ""):
        problems.append(f"parcel '{row['parcel']}' should be Y/N")
    t_in, t_out = parse_time(row["time_ordered"]), parse_time(row["time_served"])
    for col, parsed in (("time_ordered", t_in), ("time_served", t_out)):
        if row[col] and parsed is None:
            problems.append(f"{col} '{row[col]}' is not HH:MM")
    if t_in and t_out and t_out < t_in:
        problems.append("time_served is before time_ordered")
    if row["time_served"] and not row["time_ordered"]:
        problems.append("time_served without time_ordered")
    return problems


# ---------------- conflict analysis ----------------

def dish_name(row):
    item = row["item"].replace("?", "").strip()
    variant = row["variant"].replace("?", "").strip()
    unsure = "?" in row["item"] or "?" in row["variant"]
    name = f"{variant} {item}".strip().upper()
    return name + (" (?)" if unsure else "")


def extras(row):
    return row["modifications"] or "-"


def split_extras(text):
    """'corn+garlic; ? no' -> ['corn', 'garlic', '? no']"""
    return [p.strip() for p in text.replace("+", ";").split(";") if p.strip()]


def requirements(row):
    """What the student asked for, as far as it can be read.
    Blank modifications = no extras recorded (Kamal may not write spoken ones)."""
    readable = {p.lower() for p in split_extras(row["modifications"]) if "?" not in p}
    return {
        "portion": row["portion"] if row["portion"] in ("half", "full") else None,
        "parcel": row["parcel"] if row["parcel"] in ("Y", "N") else None,
        "extras": readable,
        "extras_unclear": any("?" in p for p in split_extras(row["modifications"])),
    }


def classify_pair(a, b):
    """Potential risk if orders a and b (same dish, same page) were confused.
    Returns (category, reason):
      'customization'  - a known difference in portion, parcel or extras:
                         the wrong plate would mean the wrong food
      'identification' - identical as recorded: the food is right, but it
                         could go to the wrong student or out of turn
      'unclassified'   - no known difference, but some details are unreadable"""
    ra, rb = requirements(a), requirements(b)
    reasons = []
    if ra["portion"] and rb["portion"] and ra["portion"] != rb["portion"]:
        reasons.append("portion")
    if ra["parcel"] and rb["parcel"] and ra["parcel"] != rb["parcel"]:
        reasons.append("parcel/dine-in")
    only_a, only_b = ra["extras"] - rb["extras"], rb["extras"] - ra["extras"]
    # an extra only counts as different if the other order's extras were fully readable
    if (only_a and not rb["extras_unclear"]) or (only_b and not ra["extras_unclear"]):
        reasons.append("extras")
    if reasons:
        return "customization", "different " + " & ".join(reasons)
    unreadable = [name for name, missing in (
        ("portion", ra["portion"] is None or rb["portion"] is None),
        ("parcel", ra["parcel"] is None or rb["parcel"] is None),
        ("extras", ra["extras_unclear"] or rb["extras_unclear"])) if missing]
    if unreadable:
        return "unclassified", f"{' & '.join(unreadable)} unreadable"
    return "identification", "identical as recorded"


def find_lookalikes(rows, min_size=MIN_LOOKALIKE):
    """Group orders by (page, dish). Returns only groups with min_size+ orders."""
    groups = defaultdict(list)
    for r in rows:
        groups[(r["source"], dish_name(r))].append(r)
    return {key: g for key, g in sorted(groups.items()) if len(g) >= min_size}


def summarise_risks(readable_rows, min_size=MIN_LOOKALIKE):
    """Count orders by the kind of potential risk they are part of.
    exposed       - orders sharing their (page, dish) with another order
    customization - orders in at least one customization-conflict pair
    identification- orders in at least one identification-ambiguity pair
    unclassified  - exposed orders whose pairs could not be classified at all"""
    groups = find_lookalikes(readable_rows, min_size)
    pairs = []
    in_cat = {"customization": set(), "identification": set()}
    exposed = set()
    for group in groups.values():
        exposed.update(r["row_id"] for r in group)
        for a, b in combinations(group, 2):
            category, reason = classify_pair(a, b)
            pairs.append((a, b, category, reason))
            if category in in_cat:
                in_cat[category].update((a["row_id"], b["row_id"]))
    unclassified = exposed - in_cat["customization"] - in_cat["identification"]
    total = len(readable_rows)
    return {
        "groups": groups, "pairs": pairs, "total": total,
        "exposed": exposed, "customization": in_cat["customization"],
        "identification": in_cat["identification"], "unclassified": unclassified,
        "share": 100 * len(exposed) / total if total else 0.0,
    }


# ---------------- tokens ----------------

def busiest_moment(timed):
    """timed = list of (time_ordered, time_served). Returns (most_open, when)."""
    events = [(t, 1) for t, _ in timed] + [(t, -1) for _, t in timed]
    open_now = busiest = 0
    busiest_at = None
    # at the same minute, count serves (-1) before new orders (+1)
    for t, change in sorted(events, key=lambda e: (e[0], e[1])):
        open_now += change
        if open_now > busiest:
            busiest, busiest_at = open_now, t
    return busiest, busiest_at


def recommended_tokens(peak, buffer=TOKEN_BUFFER):
    """Busiest moment x buffer, always rounded UP.
    round(..., 6) first so float noise (100 x 1.1 = 110.00000000000001)
    doesn't push an exact answer up by one."""
    return math.ceil(round(peak * buffer, 6))


def split_by_timestamps(rows):
    """Sort rows by what timestamps they have.
    Returns (timed, outstanding, incomplete):
      timed       - [(time_ordered, time_served)] where both are valid and in order
      outstanding - row_ids ordered but not served when recording stopped
      incomplete  - row_ids with a served time but no valid ordered time,
                    or served before ordered"""
    timed, outstanding, incomplete = [], [], []
    for r in rows:
        t_in, t_out = parse_time(r["time_ordered"]), parse_time(r["time_served"])
        if t_in and t_out:
            if t_out >= t_in:
                timed.append((t_in, t_out))
            else:
                incomplete.append(r["row_id"])
        elif t_in and not r["time_served"]:
            outstanding.append(r["row_id"])
        elif r["time_served"] or r["time_ordered"]:
            incomplete.append(r["row_id"])
    return timed, outstanding, incomplete


# ---------------- order pad ----------------

def count_extras(rows):
    """Count single extras in the modifications column ('?' parts skipped)."""
    counts = Counter()
    for r in rows:
        for part in split_extras(r["modifications"]):
            if "?" not in part:
                counts[part.capitalize()] += 1
    return counts


def choose_tick_boxes(extra_counts, observed=OBSERVED_EXTRAS, limit=MAX_TICK_BOXES,
                      min_count=MIN_DATA_EXTRA):
    """Extras common in the data first (seen min_count+ times), then the ones I observed."""
    from_data = [n for n, c in extra_counts.most_common() if c >= min_count]
    boxes = []
    for name in from_data + list(observed):
        if name.lower() not in (b.lower() for b in boxes):
            boxes.append(name)
    return boxes[:limit]


def write_order_pad(path, boxes, rows=PAD_ROWS):
    """A printable A4 (landscape) order pad. Row number = token number.
    Kamal writes only the dish on each row; everything else is ticked ONLY
    when it differs from the usual order (blank = full plate, dine-in,
    no extras, qty 1)."""
    ticks = ["Half", "Parcel"] + list(boxes)
    sub_head = "".join(f"<th class='tick'>{html.escape(t)}</th>" for t in ticks)
    empty = "<td class='tick'></td>" * len(ticks)
    body = "".join(
        f"<tr><td class='num'>{n:02d}</td><td class='dish'></td><td class='qty'></td>"
        f"{empty}<td class='other'></td></tr>"
        for n in range(1, rows + 1))
    page = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>NIT Canteen order pad</title>
<style>
  @page {{ size: A4 landscape; margin: 8mm; }}
  body {{ font-family: Arial, sans-serif; margin: 0; }}
  h1 {{ font-size: 15px; margin: 0 0 3px; }}
  .meta {{ font-size: 11px; margin-bottom: 5px; }}
  table {{ border-collapse: collapse; width: 100%; font-size: 10px; }}
  th, td {{ border: 1px solid #555; padding: 0 3px; height: 6.1mm; }}
  th {{ background: #e8e8e8; font-size: 9px; height: 5mm; }}
  th.group {{ background: #d6d6d6; font-size: 10px; }}
  tr:nth-child(even) td {{ background: #f4f4f4; }}
  td.num {{ width: 8mm; font-weight: bold; text-align: center; font-size: 12px; }}
  td.dish {{ width: 62mm; }}
  td.qty {{ width: 9mm; }}
  th.tick {{ width: 12mm; }}
  .note {{ font-size: 9px; margin-top: 3px; color: #444; }}
</style></head><body>
<h1>NIT Canteen &mdash; order pad</h1>
<div class="meta">Date: ____________ &nbsp; Page: ____ &nbsp;&nbsp;
Give the student the token with the row number. Call the number when it's ready.</div>
<table>
<tr><th rowspan="2">No.</th><th rowspan="2">Dish</th><th rowspan="2">Qty</th>
<th class="group" colspan="{len(ticks)}">Tick only if needed</th>
<th rowspan="2">Other instructions</th></tr>
<tr>{sub_head}</tr>
{body}
</table>
<div class="note">Blank means the usual: full plate, dine-in, no extras, qty 1.
Write only the dish; tick or write anything that's different.</div>
</body></html>"""
    with open(path, "w", encoding="utf-8") as out:
        out.write(page)


def write_pad_layout(path, boxes, rows=PAD_ROWS):
    """Save the printed pad's columns so order_ocr.py reads the right tick boxes.
    If the tick boxes ever change, reprint the pad."""
    ticks = ["Half", "Parcel"] + list(boxes)
    layout = {"rows": rows, "ticks": ticks,
              "columns": ["No.", "Dish", "Qty"] + ticks + ["Other instructions"]}
    with open(path, "w", encoding="utf-8") as out:
        json.dump(layout, out, indent=2)


def write_slips(path, rows, lookalike_ids):
    with open(path, "w", encoding="utf-8") as out:
        for n, r in enumerate(rows, start=1):
            parcel = "PARCEL" if r["parcel"] == "Y" else "DINE-IN"
            flag = "  !! SAME DISH ON PAGE" if r["row_id"] in lookalike_ids else ""
            out.write(f"#{n:02d} | {dish_name(r):26} | {(r['portion'] or '?').upper():4} "
                      f"x{r['qty'] or '?'} | {extras(r):24} | {parcel:7} | row {r['row_id']}{flag}\n")


# ---------------- main ----------------

def main():
    path = sys.argv[1] if len(sys.argv) > 1 else "data.csv"
    rows, issues, fatal = read_orders(path)

    print("=" * 66)
    print(f" NIT Canteen - order mix-up check   ({path})")
    print("=" * 66)
    if fatal:
        print(f"ERROR: {fatal}")
        sys.exit(1)

    # ---- 1. validation: report by line number, keep going ----
    seen_ids = set()
    for line, kind, reason in issues:
        print(f"  {kind.upper()} line {line}: {reason}")
    for r in rows:
        if not r["row_id"]:
            r["row_id"] = f"line{r['_line']}"
        problems = check_row(r, seen_ids)
        if problems:
            print(f"  WARNING line {r['_line']} (row {r['row_id']}): " + "; ".join(problems))

    readable = [r for r in rows if has_readable_dish(r)]
    unreadable = [r["row_id"] for r in rows if r not in readable]
    print(f"\nRead {len(rows)} orders. {len(readable)} with a readable dish, "
          f"{len(unreadable)} without (rows {', '.join(unreadable) or '-'}).")
    if not rows:
        print("No valid orders to analyse.")

    # ---- 2. what gets ordered ----
    if readable:
        print("\nMost written dishes:")
        for name, n in Counter(dish_name(r) for r in readable).most_common(5):
            print(f"  {n} x {name}")
        print(f"Orders with extras written down: "
              f"{sum(1 for r in readable if r['modifications'])} of {len(readable)}")
        print(f"Parcel orders: {sum(1 for r in readable if r['parcel'] == 'Y')}   "
              f"Half plates: {sum(1 for r in readable if r['portion'] == 'half')}")

    # ---- 3. potential mix-up risks ----
    risk = summarise_risks(readable)
    labels = {"customization": "CUSTOMIZATION CONFLICT",
              "identification": "IDENTIFICATION/DELIVERY AMBIGUITY",
              "unclassified": "can't classify"}
    print(f"\nPOTENTIAL MIX-UP RISKS (same dish {MIN_LOOKALIKE}+ times on one page)")
    print("These are risks if two orders were confused - not confirmed mistakes.")
    print("-" * 66)
    for (page, name), group in risk["groups"].items():
        print(f"{page}: {len(group)} x {name}")
        for r in group:
            parcel = "PARCEL" if r["parcel"] == "Y" else "dine-in"
            print(f"   row {r['row_id']:>2}: {r['portion'] or '?':5} | {extras(r):22} | {parcel}")
        for a, b, category, reason in risk["pairs"]:
            if a in group:
                print(f"   row {a['row_id']} vs {b['row_id']}: {labels[category]} ({reason})")
    if not risk["groups"]:
        print("No dish appears twice on the same page.")

    t = risk["total"]
    print(f"\nOrders sharing a dish with another order on the same page: "
          f"{len(risk['exposed'])} of {t} ({risk['share']:.0f}%)")
    print(f"  in a customization conflict:             {len(risk['customization'])}")
    print(f"  in an identification/delivery ambiguity: {len(risk['identification'])}")
    print(f"  can't classify (details unreadable):     {len(risk['unclassified'])}")
    print("  (% = orders whose page+dish appears 2+ times / orders with a readable dish.\n"
          "   An order can be in both kinds of pair, so the two middle lines can overlap.)")

    # ---- 4. token inventory ----
    timed, outstanding, incomplete = split_by_timestamps(rows)
    print("\nTOKEN INVENTORY")
    print("-" * 66)
    print(f"Orders with both valid timestamps: {len(timed)}")
    if outstanding:
        print(f"Ordered but not served when recording stopped (not counted): "
              f"rows {', '.join(outstanding)}")
    if incomplete:
        print(f"Incomplete or inconsistent timestamps (not counted): rows {', '.join(incomplete)}")
    if len(timed) < MIN_TIMED_ROWS:
        print(f"Token requirement can't be calculated yet: need at least {MIN_TIMED_ROWS} "
              "orders with\ntime_ordered and time_served. No timestamps are assumed or generated.")
    else:
        waits = [(out - t_in).seconds / 60 for t_in, out in timed]
        peak, peak_at = busiest_moment(timed)
        print(f"Average wait: {sum(waits) / len(waits):.0f} min   longest: {max(waits):.0f} min")
        print(f"Most orders outstanding at once: {peak} (at {peak_at:%H:%M})")
        print(f"Recommended tokens: {recommended_tokens(peak)} "
              f"({peak} x {TOKEN_BUFFER}, rounded up)")

    # ---- 5. batch preparation (rules live in order_clustering.py) ----
    import order_clustering as oc        # imported here to avoid a circular import
    batches, individual = oc.cluster_orders(rows)
    oc.print_summary(batches, individual)
    oc.write_reports(batches, individual)
    print(f"Batch reports written to {oc.BATCH_REPORT} and {oc.INDIVIDUAL_REPORT}.")

    # ---- 6. outputs ----
    write_slips(SLIPS_FILE, readable, risk["exposed"])
    print(f"\nToken slips for {len(readable)} orders written to {SLIPS_FILE}.")
    extra_counts = count_extras(rows)
    boxes = choose_tick_boxes(extra_counts)
    write_order_pad(PAD_FILE, boxes)
    write_pad_layout(PAD_LAYOUT_FILE, boxes)
    seen = ", ".join(f"{n} ({c})" for n, c in extra_counts.most_common()) or "none"
    print(f"Order pad with {PAD_ROWS} numbered rows written to {PAD_FILE}.")
    print(f"  Extras in the data: {seen}  -> tick box if seen {MIN_DATA_EXTRA}+ times")
    print(f"  Tick boxes: {', '.join(boxes)} + 'Other instructions'")


if __name__ == "__main__":
    main()
