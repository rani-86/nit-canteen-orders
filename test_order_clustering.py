"""
Tests for order_clustering.py (batch preparation).  Run with:   python -m unittest -v
They follow the canteen's rule: 4+ orders of the same dish with no
customization may be cooked together; everything else is prepared individually.
"""

import csv
import os
import tempfile
import unittest

import canteen_tokens as ct
import order_clustering as oc


def order(row_id, item="fried rice", variant="veg", modifications="", qty="1",
          portion="half", source="p1", time_ordered=""):
    return dict(row_id=row_id, source=source, item=item, variant=variant,
                portion=portion, qty=qty, modifications=modifications, parcel="N",
                time_ordered=time_ordered, time_served="")


def ids(entries):
    return [e["order_id"] for e in entries]


class BatchRuleTests(unittest.TestCase):
    # 1
    def test_exactly_four_plain_orders_make_one_batch(self):
        batches, individual = oc.cluster_orders([order(str(i)) for i in range(1, 5)])
        self.assertEqual(len(batches), 1)
        self.assertEqual(batches[0]["compatible_orders"], 4)
        self.assertEqual(individual, [])

    # 2
    def test_five_compatible_orders_make_one_batch_of_five(self):
        batches, _ = oc.cluster_orders([order(str(i)) for i in range(1, 6)])
        self.assertEqual(len(batches), 1)
        self.assertEqual(batches[0]["order_ids"], ["1", "2", "3", "4", "5"])

    # 3
    def test_three_compatible_orders_are_prepared_individually(self):
        batches, individual = oc.cluster_orders([order(str(i)) for i in range(1, 4)])
        self.assertEqual(batches, [])
        self.assertEqual(len(individual), 3)
        self.assertTrue(all("fewer than 4" in e["reason"] for e in individual))

    # 4
    def test_four_orders_with_one_customized_is_not_a_batch(self):
        rows = [order("1"), order("2"), order("3"), order("4", modifications="less oil")]
        batches, individual = oc.cluster_orders(rows)
        self.assertEqual(batches, [])
        reasons = {e["order_id"]: e["reason"] for e in individual}
        self.assertEqual(reasons["4"], "customized - prepared separately")
        self.assertIn("only 3 compatible", reasons["1"])

    # 5
    def test_different_dishes_are_never_combined(self):
        rows = [order("1"), order("2"), order("3", item="maggi"), order("4", item="maggi")]
        batches, _ = oc.cluster_orders(rows)
        self.assertEqual(batches, [])                       # 2 + 2 is not 4

    def test_only_the_dish_with_enough_orders_is_batched(self):
        rows = [order(str(i)) for i in range(1, 5)] + [order("5", item="maggi")]
        batches, individual = oc.cluster_orders(rows)
        self.assertEqual(batches[0]["dish"], "veg fried rice")
        self.assertEqual(ids(individual), ["5"])

    # 6
    def test_capitalization_and_spacing_do_not_split_a_dish(self):
        rows = [order("1", item="Fried Rice", variant="Veg"),
                order("2", item="fried  rice", variant="veg"),
                order("3", item=" FRIED RICE ", variant="VEG "),
                order("4", item="fried rice", variant=" veg")]
        batches, _ = oc.cluster_orders(rows)
        self.assertEqual(len(batches), 1)
        self.assertEqual(batches[0]["compatible_orders"], 4)

    def test_unsure_reading_never_merges_with_a_certain_one(self):
        self.assertNotEqual(oc.normalize_dish(order("1", item="noodles?")),
                            oc.normalize_dish(order("2", item="noodles")))

    # 7
    def test_quantities_are_added_but_the_rule_counts_orders(self):
        rows = [order("1", qty="2"), order("2", qty="1"), order("3", qty="1"), order("4", qty="1")]
        batches, _ = oc.cluster_orders(rows)
        self.assertEqual(batches[0]["total_quantity"], 5)
        self.assertEqual(batches[0]["compatible_orders"], 4)

    def test_unknown_quantity_is_not_batched(self):
        rows = [order(str(i)) for i in range(1, 5)] + [order("5", qty="?")]
        batches, individual = oc.cluster_orders(rows)
        self.assertEqual(batches[0]["order_ids"], ["1", "2", "3", "4"])
        self.assertEqual(individual[0]["reason"], "quantity unknown - can't check batch capacity")

    def test_one_student_ordering_four_plates_is_not_a_batch(self):
        batches, individual = oc.cluster_orders([order("1", qty="4")])
        self.assertEqual(batches, [])
        self.assertEqual(ids(individual), ["1"])

    # 8
    def test_empty_dataset(self):
        self.assertEqual(oc.cluster_orders([]), ([], []))

    # 9
    def test_missing_customization_field_is_never_batched(self):
        rows = [order(str(i)) for i in range(1, 5)]
        for r in rows:
            del r["modifications"]
        batches, individual = oc.cluster_orders(rows)
        self.assertEqual(batches, [])
        self.assertTrue(all("customization unknown" in e["reason"] for e in individual))

    def test_missing_modifications_column_in_csv_is_never_batched(self):
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "t.csv")
            with open(path, "w", encoding="utf-8") as f:
                f.write("row_id,source,item,variant\n" +
                        "".join(f"{i},p1,fried rice,veg\n" for i in range(1, 5)))
            rows, _, _ = ct.read_orders(path)
        batches, _ = oc.cluster_orders(rows)
        self.assertEqual(batches, [])

    # 10
    def test_missing_dish_name_goes_to_individual_list(self):
        rows = [order(str(i)) for i in range(1, 5)] + [order("5", item="")]
        batches, individual = oc.cluster_orders(rows)
        self.assertEqual(batches[0]["order_ids"], ["1", "2", "3", "4"])
        self.assertEqual(individual[0]["reason"], "dish name missing or unreadable")

    # 11
    def test_original_order_ids_stay_exactly_as_written(self):
        rows = [order("01"), order("03"), order("04"), order("05"), order("07", item="maggi")]
        batches, individual = oc.cluster_orders(rows)
        self.assertEqual(batches[0]["order_ids"], ["01", "03", "04", "05"])
        self.assertEqual(ids(individual), ["07"])

    # 12 - the worked example from the feature request
    def test_example_from_the_brief_separates_batch_and_individual(self):
        rows = [order("01"),
                order("02", item="maggi", modifications="Extra spicy"),
                order("03"), order("04"), order("05"),
                order("06", modifications="Less oil"),
                order("07", item="maggi")]
        batches, individual = oc.cluster_orders(rows)
        self.assertEqual(len(batches), 1)
        self.assertEqual(batches[0]["dish"], "veg fried rice")
        self.assertEqual(batches[0]["total_quantity"], 4)
        self.assertEqual(batches[0]["order_ids"], ["01", "03", "04", "05"])
        reasons = {e["order_id"]: e["reason"] for e in individual}
        self.assertEqual(list(reasons), ["02", "06", "07"])
        self.assertEqual(reasons["02"], "customized - prepared separately")
        self.assertEqual(reasons["06"], "customized - prepared separately")
        self.assertIn("fewer than 4", reasons["07"])
        # every order appears exactly once across the two lists
        all_ids = batches[0]["order_ids"] + list(reasons)
        self.assertEqual(sorted(all_ids), ["01", "02", "03", "04", "05", "06", "07"])


class CapacityTests(unittest.TestCase):
    """The cook makes at most 6 plates in one batch; half and full can be combined."""
    def test_six_plates_fit_in_one_batch(self):
        rows = [order(str(i)) for i in range(1, 7)]
        batches, individual = oc.cluster_orders(rows)
        self.assertEqual(batches[0]["total_quantity"], 6)
        self.assertEqual(individual, [])

    def test_nine_orders_fill_one_batch_and_leftovers_follow_the_4_rule(self):
        batches, individual = oc.cluster_orders([order(str(i)) for i in range(1, 10)])
        self.assertEqual([b["order_ids"] for b in batches], [["1", "2", "3", "4", "5", "6"]])
        self.assertEqual(ids(individual), ["7", "8", "9"])
        self.assertTrue(all("left over" in e["reason"] for e in individual))

    def test_ten_orders_make_two_batches_in_order_received(self):
        batches, _ = oc.cluster_orders([order(str(i)) for i in range(1, 11)])
        self.assertEqual([b["order_ids"] for b in batches],
                         [["1", "2", "3", "4", "5", "6"], ["7", "8", "9", "10"]])

    def test_no_batch_ever_goes_over_six_plates(self):
        rows = [order(str(i), qty=q) for i, q in enumerate(["2", "2", "1", "2", "1", "1", "1"], 1)]
        batches, _ = oc.cluster_orders(rows)
        self.assertTrue(all(b["total_quantity"] <= 6 for b in batches))

    def test_one_order_bigger_than_a_batch_is_prepared_individually(self):
        rows = [order(str(i)) for i in range(1, 5)] + [order("5", qty="7")]
        batches, individual = oc.cluster_orders(rows)
        self.assertEqual(batches[0]["order_ids"], ["1", "2", "3", "4"])
        self.assertIn("more than one batch holds", individual[0]["reason"])

    def test_half_and_full_plates_are_combined(self):
        rows = [order("1", portion="half"), order("2", portion="full"),
                order("3", portion="half"), order("4", portion="full")]
        batches, _ = oc.cluster_orders(rows)
        self.assertEqual(batches[0]["portions"], {"half": 2, "full": 2})

    def test_capacity_smaller_than_min_batch_is_rejected(self):
        with self.assertRaises(ValueError):
            oc.cluster_orders([order("1")], max_plates=3)


class SettingsTests(unittest.TestCase):
    def test_min_batch_size_is_configurable(self):
        rows = [order(str(i)) for i in range(1, 4)]
        self.assertEqual(len(oc.cluster_orders(rows, min_batch=3)[0]), 1)

    def test_packing_and_order_numbers_cannot_be_switched_off(self):
        original = oc.SEPARATE_PACKING
        try:
            oc.SEPARATE_PACKING = False
            with self.assertRaises(ValueError):
                oc.cluster_orders([order("1")])
        finally:
            oc.SEPARATE_PACKING = original

    def test_page_scope_keeps_pages_apart(self):
        rows = [order("1"), order("2"), order("3", source="p2"), order("4", source="p2")]
        self.assertEqual(oc.cluster_orders(rows)[0], [])
        self.assertEqual(len(oc.cluster_orders(rows, group_by_page=False)[0]), 1)

    def test_timestamps_only_add_a_note_never_split_a_batch(self):
        rows = [order(str(i), time_ordered=t) for i, t in
                enumerate(["23:50", "23:55", "00:20", "00:30"], start=1)]
        batch = oc.cluster_orders(rows)[0][0]
        self.assertEqual(batch["ordered_span_min"], 40)
        self.assertIn("40 min apart", batch["recommendation"])

    def test_reports_are_written_as_csv(self):
        batches, individual = oc.cluster_orders([order(str(i)) for i in range(1, 6)])
        with tempfile.TemporaryDirectory() as folder:
            b_path, i_path = os.path.join(folder, "b.csv"), os.path.join(folder, "i.csv")
            oc.write_reports(batches, individual, b_path, i_path)
            with open(b_path, encoding="utf-8") as f:
                report = list(csv.DictReader(f))
        self.assertEqual(report[0]["batch_id"], "B1")
        self.assertEqual(report[0]["order_ids"], "1 2 3 4 5")


@unittest.skipUnless(os.path.exists("data.csv"), "data.csv not in this folder")
class RealDataTests(unittest.TestCase):
    """Guards the numbers published in the README."""
    def setUp(self):
        rows, _, _ = ct.read_orders("data.csv")
        # only my fieldwork rows - orders imported later from pad photos are excluded
        self.rows = [r for r in rows if r["source"].startswith("notepad_")]

    def test_no_batch_on_the_same_page(self):
        batches, individual = oc.cluster_orders(self.rows, group_by_page=True)
        self.assertEqual(batches, [])
        self.assertEqual(len(individual), 21)

    def test_still_no_batch_if_all_pages_were_open_together(self):
        # veg noodles rows 1, 8, 18, 20 would match, but 8 and 20 have unreadable qty
        batches, individual = oc.cluster_orders(self.rows, group_by_page=False)
        self.assertEqual(batches, [])
        reasons = {e["order_id"]: e["reason"] for e in individual}
        self.assertIn("quantity unknown", reasons["8"])
        self.assertIn("quantity unknown", reasons["20"])


if __name__ == "__main__":
    unittest.main()
