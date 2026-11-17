"""
canteen_tokens.py - NIT Canteen order mix-up checker

Reads data.csv (orders from Kamal Singh's notepad + my own observations) and:
  1. finds LOOK-ALIKE orders: the same dish written on the same notepad page,
     where Kamal has to remember who ordered which one;
  2. prints a numbered token slip for every order, marking the look-alikes;
  3. if rows have time_ordered + time_served, works out the busiest moment
     and how many tokens he would need.

Usage:
    python canteen_tokens.py              # reads data.csv
    python canteen_tokens.py other.csv    # reads another file

Only uses the Python standard library.
"""

import csv
import math
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta

# ---------------- settings you can change ----------------
MIN_LOOKALIKE = 2      # same dish appearing this many times on one page = look-alike group
TOKEN_BUFFER = 1.25    # spare tokens on top of the busiest moment (1.25 = 25% extra)
MIN_TIMED_ROWS = 5     # need at least this many timed orders before sizing tokens
NEXT_DAY_BEFORE = 12   # times before 12:00 count as "after midnight" (night canteen)
SLIPS_FILE = "slips.txt"
# ---------------------------------------------------------

REQUIRED_COLUMNS = ["row_id", "source", "item", "variant", "portion", "qty",
                    "modifications", "parcel", "time_ordered", "time_served"]


def clean(value):
    return (value or "").strip()


def unknown(value):
    """True if the value is blank or just question marks (couldn't read it)."""
    return value.replace("?", "").strip() == ""


def parse_time(text):
    """'23:05' -> datetime on a dummy day; '00:35' -> the next day.
    Returns None if blank or not HH:MM."""
    try:
        t = datetime.strptime(text, "%H:%M")
    except ValueError:
        return None
    if t.hour < NEXT_DAY_BEFORE:
        t += timedelta(days=1)
    return t


def load_rows(path):
    try:
        f = open(path, newline="", encoding="utf-8")
    except FileNotFoundError:
        sys.exit(f"Can't find {path}. Run this from the folder that has data.csv.")
    with f:
        reader = csv.DictReader(f)
        missing = [c for c in REQUIRED_COLUMNS if c not in (reader.fieldnames or [])]
        if missing:
            sys.exit(f"{path} is missing columns: {', '.join(missing)}")
        return [{k: clean(v) for k, v in row.items() if k} for row in reader]


def check_row(row, seen_ids):
    """Return a list of problems with this row (empty list = fine)."""
    problems = []
    rid = row["row_id"]
    if rid in seen_ids:
        problems.append("duplicate row_id")
    seen_ids.add(rid)
    if row["portion"] not in ("half", "full", "?", ""):
        problems.append(f"portion '{row['portion']}' should be half/full")
    if row["qty"] not in ("?", "") and not row["qty"].isdigit():
        problems.append(f"qty '{row['qty']}' is not a whole number")
    if row["parcel"] not in ("Y", "N", ""):
        problems.append(f"parcel '{row['parcel']}' should be Y/N")
    for col in ("time_ordered", "time_served"):
        if row[col] and parse_time(row[col]) is None:
            problems.append(f"{col} '{row[col]}' is not HH:MM")
    return problems


def dish_name(row):
    item = row["item"].replace("?", "").strip()
    variant = row["variant"].replace("?", "").strip()
    unsure = "?" in row["item"] or "?" in row["variant"]
    name = f"{variant} {item}".strip().upper()
    return name + (" (?)" if unsure else "")


def extras(row):
    return row["modifications"] or "-"


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else "data.csv"
    rows = load_rows(path)

    print("=" * 64)
    print(f" NIT Canteen - order mix-up check   ({path})")
    print("=" * 64)

    # ---- 1. check every row; keep going even if some are bad ----
    seen_ids, usable, unreadable = set(), [], []
    for row in rows:
        problems = check_row(row, seen_ids)
        if problems:
            print(f"  WARNING row {row['row_id'] or '?'}: " + "; ".join(problems))
        if unknown(row["item"]) or unknown(row["variant"]):
            unreadable.append(row["row_id"])
        else:
            usable.append(row)

    print(f"\nRead {len(rows)} orders. {len(usable)} readable, "
          f"{len(unreadable)} with item/variant I couldn't read "
          f"(rows {', '.join(unreadable) or '-'}).")

    # ---- 2. what gets ordered ----
    dishes = Counter(dish_name(r) for r in usable)
    with_extras = [r for r in usable if r["modifications"]]
    parcels = [r for r in usable if r["parcel"] == "Y"]
    halves = [r for r in usable if r["portion"] == "half"]
    print("\nMost written dishes:")
    for name, n in dishes.most_common(5):
        print(f"  {n} x {name}")
    print(f"Orders with extras/changes: {len(with_extras)} of {len(usable)}")
    print(f"Parcel orders: {len(parcels)}   Half plates: {len(halves)}")

    # ---- 3. look-alike orders: same dish on the same page ----
    groups = defaultdict(list)
    for r in usable:
        groups[(r["source"], dish_name(r))].append(r)

    lookalike_ids = set()
    print(f"\nLOOK-ALIKE ORDERS (same dish {MIN_LOOKALIKE}+ times on one page)")
    print("-" * 64)
    for (page, name), group in sorted(groups.items()):
        if len(group) < MIN_LOOKALIKE:
            continue
        lookalike_ids.update(r["row_id"] for r in group)
        print(f"{page}: {len(group)} x {name}")
        for r in group:
            parcel = "PARCEL" if r["parcel"] == "Y" else "dine-in"
            print(f"   row {r['row_id']:>2}: {r['portion'] or '?':5} | {extras(r):24} | {parcel}")
        readable = [r for r in group if r["portion"] in ("half", "full")]
        details = {(r["portion"], r["modifications"], r["parcel"]) for r in readable}
        if len(details) > 1:
            print("   -> they differ: a swap gives someone the WRONG food")
        elif len(readable) < len(group):
            print("   -> same dish, some portions unreadable: a swap is possible either way")
        else:
            print("   -> identical: a swap means someone is served OUT OF TURN")
    share = 100 * len(lookalike_ids) / len(usable) if usable else 0
    print(f"\n{len(lookalike_ids)} of {len(usable)} readable orders ({share:.0f}%) "
          f"had a look-alike on the same page -> these need a token most.")

    # ---- 4. timed rows: busiest moment and token count ----
    timed, waiting = [], []
    for r in usable + [x for x in rows if x["row_id"] in unreadable]:
        t_in, t_out = parse_time(r["time_ordered"]), parse_time(r["time_served"])
        if t_in and t_out:
            if t_out < t_in:              # served after midnight
                t_out += timedelta(days=1)
            timed.append((t_in, t_out))
        elif t_in:
            waiting.append(r["row_id"])

    print("\nWAIT TIMES AND TOKENS")
    print("-" * 64)
    if waiting:
        print(f"Ordered but not served when I stopped recording: rows {', '.join(waiting)}")
    if len(timed) < MIN_TIMED_ROWS:
        print(f"Only {len(timed)} orders have both times (need {MIN_TIMED_ROWS}). "
              "Can't size tokens yet -\nrecord time_ordered and time_served during the rush.")
    else:
        waits = [(out - t_in).seconds / 60 for t_in, out in timed]
        events = [(t, 1) for t, _ in timed] + [(t, -1) for _, t in timed]
        open_now = busiest = 0
        busiest_at = None
        for t, change in sorted(events, key=lambda e: (e[0], e[1])):
            open_now += change
            if open_now > busiest:
                busiest, busiest_at = open_now, t
        print(f"Timed orders: {len(timed)}   average wait: {sum(waits)/len(waits):.0f} min   "
              f"longest: {max(waits):.0f} min")
        print(f"Busiest moment: {busiest} orders open at {busiest_at:%H:%M}")
        print(f"Tokens to keep: {math.ceil(busiest * TOKEN_BUFFER)} "
              f"(busiest x {TOKEN_BUFFER})")

    # ---- 5. token slips ----
    with open(SLIPS_FILE, "w", encoding="utf-8") as out:
        for n, r in enumerate(usable, start=1):
            parcel = "PARCEL" if r["parcel"] == "Y" else "DINE-IN"
            flag = "  !! LOOK-ALIKE" if r["row_id"] in lookalike_ids else ""
            out.write(f"#{n:02d} | {dish_name(r):26} | {(r['portion'] or '?').upper():4} "
                      f"x{r['qty'] or '?'} | {extras(r):24} | {parcel:7} | row {r['row_id']}{flag}\n")
    print(f"\nToken slips for {len(usable)} orders written to {SLIPS_FILE}.")


if __name__ == "__main__":
    main()
