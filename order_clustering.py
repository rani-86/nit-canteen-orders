"""
order_clustering.py - Smart batch preparation for NIT Canteen

Applies the canteen's own cooking rule (from my conversation with the staff,
see fieldwork.md) to the orders in data.csv:

  - 4 or more students order the same dish with NO customization
        -> the cook may prepare those orders together in one batch
  - fewer than 4 such orders            -> prepared individually
  - any order with a customization      -> prepared separately, never combined
  - different dishes                    -> never grouped together

Batching means COOKING together only. Every portion keeps its own order /
token number and is plated, packed and handed over separately.

Usage:
    python order_clustering.py            # reads data.csv
    python order_clustering.py other.csv

It also runs automatically as part of canteen_tokens.py.
Only uses the Python standard library.
"""

import csv
import sys
from collections import Counter

from canteen_tokens import parse_time, read_orders, unknown

# ---------------- batching rules (change them here only) ----------------
MIN_BATCH_SIZE = 4               # orders (students) needed for a batch suggestion
ALLOW_CUSTOMIZED_IN_BATCH = False  # the canteen's rule: customized orders stay separate
SEPARATE_PACKING = True          # ALWAYS True: batching is cooking together, not packing
PRESERVE_ORDER_NUMBERS = True    # ALWAYS True: every portion keeps its own order/token no.

GROUP_BY_PAGE = True             # only batch orders written on the same notepad page
SPLIT_BY_PORTION = False         # True = half and full plates batched separately
REVIEW_WINDOW_MIN = 10           # used ONLY to comment on timestamped batches;
                                 # not a rule, not validated by the cook

BATCH_REPORT = "batch_report.csv"
INDIVIDUAL_REPORT = "individual_orders.csv"
# -------------------------------------------------------------------------


def normalize_dish(row):
    """Dish name used for grouping: variant + item, lower case, single spaces.
    ' Veg ', 'Fried   Rice' -> 'veg fried rice'
    A reading I wasn't sure of keeps a '(?)' marker so it never merges with a
    certain reading. Returns '' if the dish can't be read."""
    item = row.get("item") or ""
    variant = row.get("variant") or ""
    if unknown(item) or (variant.strip() and unknown(variant)):
        return ""
    unsure = "?" in item or "?" in variant
    name = " ".join(f"{variant} {item}".replace("?", " ").lower().split())
    return name + (" (?)" if unsure else "")


def customization(row):
    """('none' | 'custom' | 'unknown', text).
    Blank modifications = no customization recorded.
    A missing modifications column = unknown, which is NOT batched."""
    if "modifications" in row.get("_absent", ()) or row.get("modifications") is None:
        return "unknown", "not recorded (no modifications column)"
    mods = row["modifications"].strip()
    if not mods:
        return "none", "none recorded"
    return "custom", mods


def quantity(row):
    """qty as a whole number >= 1, or None if missing / unreadable / invalid."""
    q = (row.get("qty") or "").strip()
    return int(q) if q.isdigit() and int(q) > 0 else None


def check_rules(min_batch):
    if not (SEPARATE_PACKING and PRESERVE_ORDER_NUMBERS):
        raise ValueError("SEPARATE_PACKING and PRESERVE_ORDER_NUMBERS must stay True: "
                         "a batch is cooked together but each order is packed and "
                         "handed over separately under its own number.")
    if min_batch < 2:
        raise ValueError("MIN_BATCH_SIZE must be at least 2.")


def ordered_span_minutes(group):
    """Minutes between the first and last time_ordered in a batch,
    or None if any order in it has no valid time_ordered."""
    times = [parse_time(r.get("time_ordered") or "") for r in group]
    if not times or None in times:
        return None
    return int((max(times) - min(times)).total_seconds() // 60)


def cluster_orders(rows, min_batch=None, allow_customized=None,
                   group_by_page=None, split_by_portion=None):
    """Split orders into batch suggestions and individual preparation.
    Returns (batches, individual). Original order IDs are kept unchanged and
    in their original order. Arguments default to the settings above."""
    min_batch = MIN_BATCH_SIZE if min_batch is None else min_batch
    allow_customized = ALLOW_CUSTOMIZED_IN_BATCH if allow_customized is None else allow_customized
    group_by_page = GROUP_BY_PAGE if group_by_page is None else group_by_page
    split_by_portion = SPLIT_BY_PORTION if split_by_portion is None else split_by_portion
    check_rules(min_batch)

    def entry(position, row, dish, reason):
        return {"_pos": position, "order_id": row.get("row_id", ""),
                "page": row.get("source", ""), "dish": dish,
                "portion": row.get("portion", ""), "qty": row.get("qty", ""),
                "customization": customization(row)[1], "reason": reason}

    groups = {}                     # (page, dish, portion) -> [(position, row)]
    individual = []
    for position, row in enumerate(rows):
        dish = normalize_dish(row)
        if not dish:
            individual.append(entry(position, row, "", "dish name missing or unreadable"))
            continue
        status, _ = customization(row)
        if status == "unknown":
            individual.append(entry(position, row, dish,
                                    "customization unknown - prepared separately"))
            continue
        if status == "custom" and not allow_customized:
            individual.append(entry(position, row, dish, "customized - prepared separately"))
            continue
        key = (row.get("source", "") if group_by_page else "",
               dish,
               row.get("portion", "") if split_by_portion else "")
        groups.setdefault(key, []).append((position, row))

    batches = []
    for (page, dish, portion), members in groups.items():   # keeps first-seen order
        group = [row for _, row in members]
        if len(group) < min_batch:
            for position, row in members:
                individual.append(entry(
                    position, row, dish,
                    f"no customization, but only {len(group)} compatible order(s) "
                    f"- fewer than {min_batch}"))
            continue
        ids = [row.get("row_id", "") for row in group]
        known_qty = [quantity(row) for row in group]
        span = ordered_span_minutes(group)
        recommendation = (f"Cook these {len(group)} orders together as one batch; "
                          f"plate, pack and hand over separately by order number "
                          f"({', '.join(ids)}).")
        if span is not None and span > REVIEW_WINDOW_MIN:
            recommendation += (f" Note: ordered {span} min apart - check they were "
                               "actually waiting at the same time.")
        batches.append({
            "batch_id": f"B{len(batches) + 1}",
            "page": page if group_by_page else "all pages",
            "dish": dish + (f" ({portion})" if split_by_portion and portion else ""),
            "total_quantity": sum(q for q in known_qty if q),
            "orders_with_unknown_qty": sum(1 for q in known_qty if q is None),
            "compatible_orders": len(group),
            "portions": dict(Counter(row.get("portion") or "?" for row in group)),
            "order_ids": ids,
            "ordered_span_min": span,
            "recommendation": recommendation,
        })

    individual.sort(key=lambda e: e["_pos"])
    for e in individual:
        del e["_pos"]
    return batches, individual


def write_reports(batches, individual, batch_path=BATCH_REPORT,
                  individual_path=INDIVIDUAL_REPORT):
    with open(batch_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["batch_id", "page", "dish", "total_quantity", "orders_with_unknown_qty",
                    "compatible_orders", "portions", "order_ids",
                    "ordered_time_span_min", "recommendation"])
        for b in batches:
            portions = "; ".join(f"{k} x{v}" for k, v in b["portions"].items())
            span = "" if b["ordered_span_min"] is None else b["ordered_span_min"]
            w.writerow([b["batch_id"], b["page"], b["dish"], b["total_quantity"],
                        b["orders_with_unknown_qty"], b["compatible_orders"], portions,
                        " ".join(b["order_ids"]), span, b["recommendation"]])
    with open(individual_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["order_id", "page", "dish", "portion", "qty", "customization", "reason"])
        for e in individual:
            w.writerow([e["order_id"], e["page"], e["dish"], e["portion"], e["qty"],
                        e["customization"], e["reason"]])


def print_summary(batches, individual):
    scope = "same notepad page" if GROUP_BY_PAGE else "whole dataset"
    print(f"\nBATCH PREPARATION (rule: {MIN_BATCH_SIZE}+ orders of the same dish, no "
          f"customization; scope: {scope})")
    print("Batch = cook together only. Every order is still packed and handed over")
    print("separately under its own number.")
    print("-" * 66)
    if batches:
        for b in batches:
            qty = f"{b['total_quantity']}"
            if b["orders_with_unknown_qty"]:
                qty += f" + {b['orders_with_unknown_qty']} order(s) with unknown qty"
            span = ("no timestamps" if b["ordered_span_min"] is None
                    else f"ordered within {b['ordered_span_min']} min")
            print(f"Suggested batch {b['batch_id']}: {b['dish'].upper()} ({b['page']})")
            print(f"   orders {', '.join(b['order_ids'])} | quantity {qty} | {span}")
    else:
        print("Suggested batches: none - no dish had "
              f"{MIN_BATCH_SIZE}+ uncustomized orders in the same group.")
    reasons = Counter(e["reason"].split(" - ")[0] if "only" not in e["reason"]
                      else "too few compatible orders" for e in individual)
    print(f"Individual preparation: {len(individual)} orders  "
          f"({'; '.join(f'{k}: {v}' for k, v in reasons.items())})")
    timed = sum(1 for b in batches if b["ordered_span_min"] is not None)
    if batches and not timed:
        print("Time window: not analysed - these orders have no time_ordered values.")


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else "data.csv"
    rows, issues, fatal = read_orders(path)
    if fatal:
        print(f"ERROR: {fatal}")
        sys.exit(1)
    for line, kind, reason in issues:
        print(f"  {kind.upper()} line {line}: {reason}")
    batches, individual = cluster_orders(rows)
    print_summary(batches, individual)
    write_reports(batches, individual)
    print(f"Reports written to {BATCH_REPORT} and {INDIVIDUAL_REPORT}.")


if __name__ == "__main__":
    main()
