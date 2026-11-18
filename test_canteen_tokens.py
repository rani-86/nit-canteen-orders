"""
Tests for canteen_tokens.py.  Run with:   python -m unittest -v
Only uses the Python standard library.
"""

import os
import tempfile
import unittest
from collections import Counter

import canteen_tokens as ct

HEADER = ("row_id,source,date,raw_text,item,variant,portion,qty,modifications,parcel,"
          "group_id,group_total,time_ordered,time_served,how_identified,issue,notes")


def order(row_id="1", source="p1", item="maggi", variant="veg", portion="half",
          qty="1", modifications="", parcel="N", time_ordered="", time_served=""):
    """Build one order row with sensible defaults."""
    return dict(row_id=row_id, source=source, item=item, variant=variant,
                portion=portion, qty=qty, modifications=modifications,
                parcel=parcel, time_ordered=time_ordered, time_served=time_served)


class CsvFile:
    """Write some CSV text to a temporary file for one test."""
    def __init__(self, text):
        self.text = text

    def __enter__(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.dir.name, "test.csv")
        with open(self.path, "w", encoding="utf-8", newline="") as f:
            f.write(self.text)
        return self.path

    def __exit__(self, *args):
        self.dir.cleanup()


# ---------- validation of single rows ----------

class CheckRowTests(unittest.TestCase):
    def test_good_row_has_no_problems(self):
        self.assertEqual(ct.check_row(order(), set()), [])

    def test_bad_row_gets_one_warning_per_bad_value(self):
        bad = order(portion="large", qty="two", parcel="maybe", time_ordered="25:70")
        self.assertEqual(len(ct.check_row(bad, set())), 4)

    def test_duplicate_row_id_is_flagged(self):
        seen = set()
        ct.check_row(order(row_id="7"), seen)
        self.assertTrue(any("duplicate" in p for p in ct.check_row(order(row_id="7"), seen)))

    def test_unreadable_values_are_allowed(self):
        self.assertEqual(ct.check_row(order(portion="?", qty="?"), set()), [])

    def test_empty_item_is_flagged(self):
        self.assertIn("item is empty", ct.check_row(order(item=""), set()))

    def test_blank_variant_is_a_readable_dish_but_question_mark_is_not(self):
        self.assertTrue(ct.has_readable_dish(order(item="chai", variant="")))
        self.assertFalse(ct.has_readable_dish(order(item="chowmein", variant="?")))
        self.assertFalse(ct.has_readable_dish(order(item="?", variant="veg")))

    def test_zero_and_negative_qty_are_flagged(self):
        self.assertTrue(ct.check_row(order(qty="0"), set()))
        self.assertTrue(ct.check_row(order(qty="-1"), set()))

    def test_served_before_ordered_is_flagged(self):
        problems = ct.check_row(order(time_ordered="00:30", time_served="00:10"), set())
        self.assertIn("time_served is before time_ordered", problems)

    def test_blank_modifications_is_not_an_error(self):
        self.assertEqual(ct.check_row(order(modifications=""), set()), [])


# ---------- reading whole CSV files ----------

class ReadOrdersTests(unittest.TestCase):
    def test_malformed_rows_are_skipped_with_line_numbers(self):
        text = (HEADER + "\n"
                "1,p1,d,x,maggi,veg,half,1,corn,N,,,,,,,\n"          # fine
                "2,p1,d,x,maggi,veg,half,1,corn, garlic,N,,,,,,,\n"  # unquoted comma
                "3,p1,d,x,maggi\n")                                  # too short
        with CsvFile(text) as path:
            rows, issues, fatal = ct.read_orders(path)
        self.assertIsNone(fatal)
        self.assertEqual([r["row_id"] for r in rows], ["1"])
        self.assertEqual([(line, kind) for line, kind, _ in issues], [(3, "skipped"), (4, "skipped")])

    def test_missing_required_column_is_fatal_not_a_crash(self):
        with CsvFile("item,variant\nmaggi,veg\n") as path:
            rows, issues, fatal = ct.read_orders(path)
        self.assertEqual(rows, [])
        self.assertIn("source", fatal)

    def test_missing_optional_columns_are_treated_as_blank(self):
        with CsvFile("source,item,variant\np1,maggi,veg\n") as path:
            rows, issues, fatal = ct.read_orders(path)
        self.assertIsNone(fatal)
        self.assertEqual(rows[0]["portion"], "")
        self.assertEqual(issues[0][1], "note")

    def test_empty_dataset_reads_as_no_rows(self):
        with CsvFile(HEADER + "\n") as path:
            rows, issues, fatal = ct.read_orders(path)
        self.assertEqual((rows, issues, fatal), ([], [], None))

    def test_completely_empty_file_is_fatal(self):
        with CsvFile("") as path:
            _, _, fatal = ct.read_orders(path)
        self.assertIsNotNone(fatal)

    def test_missing_file_is_fatal(self):
        _, _, fatal = ct.read_orders("does_not_exist.csv")
        self.assertIn("Can't find", fatal)


# ---------- times ----------

class TimeTests(unittest.TestCase):
    def test_after_midnight_counts_as_later(self):
        self.assertGreater(ct.parse_time("00:05"), ct.parse_time("23:50"))

    def test_bad_time_returns_none(self):
        self.assertIsNone(ct.parse_time("25:70"))
        self.assertIsNone(ct.parse_time(""))

    def test_busiest_moment_across_midnight(self):
        # worked out by hand: at 00:05 orders 1, 2, 3 and 4 are all open
        times = [("23:50", "00:20"), ("23:55", "00:10"), ("00:00", "00:30"),
                 ("00:05", "00:15"), ("00:25", "00:40")]
        timed = [(ct.parse_time(a), ct.parse_time(b)) for a, b in times]
        busiest, when = ct.busiest_moment(timed)
        self.assertEqual(busiest, 4)
        self.assertEqual(when.strftime("%H:%M"), "00:05")


# ---------- token inventory ----------

class TokenTests(unittest.TestCase):
    def test_tokens_add_buffer_and_round_up(self):
        self.assertEqual(ct.recommended_tokens(4, 1.25), 5)    # exactly 5.0
        self.assertEqual(ct.recommended_tokens(3, 1.25), 4)    # 3.75 -> 4
        self.assertEqual(ct.recommended_tokens(7, 1.25), 9)    # 8.75 -> 9

    def test_float_noise_does_not_add_an_extra_token(self):
        # 100 x 1.1 = 110.00000000000001 in floating point; answer must be 110
        self.assertEqual(ct.recommended_tokens(100, 1.1), 110)

    def test_missing_timestamps_are_sorted_out_not_guessed(self):
        rows = [order("1", time_ordered="23:50", time_served="00:10"),   # complete
                order("2", time_ordered="00:35"),                         # never served
                order("3", time_served="00:20"),                          # served only
                order("4", time_ordered="00:30", time_served="00:10"),   # backwards
                order("5")]                                               # no times
        timed, outstanding, incomplete = ct.split_by_timestamps(rows)
        self.assertEqual(len(timed), 1)
        self.assertEqual(outstanding, ["2"])
        self.assertEqual(incomplete, ["3", "4"])

    def test_no_timestamps_means_no_token_count(self):
        timed, _, _ = ct.split_by_timestamps([order(str(i)) for i in range(10)])
        self.assertLess(len(timed), ct.MIN_TIMED_ROWS)


# ---------- potential mix-up risks ----------

class ConflictTests(unittest.TestCase):
    def test_same_dish_same_page_is_a_group(self):
        rows = [order("1"), order("2"), order("3", item="chowmein")]
        self.assertEqual(len(ct.find_lookalikes(rows, min_size=2)), 1)

    def test_same_dish_on_different_pages_is_not_a_group(self):
        rows = [order("1", source="p1"), order("2", source="p2")]
        self.assertEqual(ct.find_lookalikes(rows, min_size=2), {})

    def test_different_extras_is_a_customization_conflict(self):
        category, reason = ct.classify_pair(order("1", modifications="corn"),
                                            order("2", modifications="no onion"))
        self.assertEqual(category, "customization")
        self.assertIn("extras", reason)

    def test_different_portion_or_parcel_is_a_customization_conflict(self):
        self.assertEqual(ct.classify_pair(order("1", portion="half"),
                                          order("2", portion="full"))[0], "customization")
        self.assertEqual(ct.classify_pair(order("1", parcel="Y"),
                                          order("2", parcel="N"))[0], "customization")

    def test_identical_orders_are_an_identification_ambiguity(self):
        self.assertEqual(ct.classify_pair(order("1"), order("2"))[0], "identification")

    def test_unreadable_portion_cannot_be_classified(self):
        self.assertEqual(ct.classify_pair(order("1"), order("2", portion="?"))[0],
                         "unclassified")

    def test_unreadable_extra_is_not_counted_as_a_difference(self):
        # '? ' could be anything, so 'corn' vs 'corn; ?' is not a known difference
        self.assertEqual(ct.classify_pair(order("1", modifications="corn"),
                                          order("2", modifications="corn; ?")),
                         ("unclassified", "extras unreadable"))

    def test_risk_summary_counts_and_percentage(self):
        rows = [order("1", modifications="corn"), order("2"),            # customization
                order("3", item="roll"), order("4", item="roll"),         # identical
                order("5", item="omelette")]                              # no twin
        risk = ct.summarise_risks(rows)
        self.assertEqual(risk["customization"], {"1", "2"})
        self.assertEqual(risk["identification"], {"3", "4"})
        self.assertEqual(risk["unclassified"], set())
        self.assertEqual(risk["share"], 80.0)                             # 4 of 5

    def test_empty_dataset_gives_zero_not_a_crash(self):
        self.assertEqual(ct.summarise_risks([])["share"], 0.0)


# ---------- extras and the order pad ----------

class ExtrasTests(unittest.TestCase):
    def test_extras_are_split_and_unreadable_ones_skipped(self):
        rows = [order(modifications="corn+garlic; ? no"), order(modifications="corn")]
        self.assertEqual(ct.count_extras(rows), Counter({"Corn": 2, "Garlic": 1}))

    def test_only_common_data_extras_get_a_box_then_observed(self):
        boxes = ct.choose_tick_boxes(Counter({"Corn": 2, "Garlic": 1}),
                                     observed=["Spicy+", "corn"], limit=10, min_count=2)
        self.assertEqual(boxes, ["Corn", "Spicy+"])

    def test_tick_boxes_respect_the_limit(self):
        boxes = ct.choose_tick_boxes(Counter(), observed=["a", "b", "c"], limit=2)
        self.assertEqual(len(boxes), 2)


class OrderPadTests(unittest.TestCase):
    def test_pad_has_every_required_field(self):
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "pad.html")
            ct.write_order_pad(path, ["Spicy+", "No onion"], rows=3)
            with open(path, encoding="utf-8") as f:
                text = f.read()
        for expected in ("No.", "Dish", "Qty", "Tick only if needed", ">Half<", ">Parcel<",
                         "Spicy+", "No onion", "Other instructions",
                         "Blank means the usual", ">01<", ">03<"):
            self.assertIn(expected, text)
        self.assertNotIn(">04<", text)


# ---------- the real data.csv ----------

@unittest.skipUnless(os.path.exists("data.csv"), "data.csv not in this folder")
class RealDataTests(unittest.TestCase):
    """Guards the published numbers: if data.csv or the logic changes, these fail."""
    def test_real_data_numbers_match_the_readme(self):
        rows, issues, fatal = ct.read_orders("data.csv")
        self.assertIsNone(fatal)
        # only my fieldwork rows - orders imported later from pad photos are excluded
        rows = [r for r in rows if r["source"].startswith("notepad_")]
        readable = [r for r in rows if ct.has_readable_dish(r)]
        risk = ct.summarise_risks(readable)
        self.assertEqual((len(rows), len(readable)), (21, 18))
        self.assertEqual(len(risk["exposed"]), 8)                        # 8 of 18 = 44%
        self.assertEqual(round(risk["share"]), 44)
        self.assertEqual(risk["customization"], {"11", "13", "18", "20"})
        self.assertEqual(risk["identification"], set())
        self.assertEqual(risk["unclassified"], {"1", "8", "15", "17"})


if __name__ == "__main__":
    unittest.main()
