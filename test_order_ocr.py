"""
Tests for order_ocr.py.  Run with:   python -m unittest -v

They use SYNTHETIC test photos in tests/fixtures (made by
tests/make_synthetic_fixtures.py from the real printed pad). These are not
real orders - they check that the grid, ticks and rows are read correctly
from a tilted, unevenly lit photo. They never touch the real data.csv.
"""

import json
import os
import shutil
import tempfile
import unittest

try:
    import cv2
    import numpy as np
    import order_ocr as ocr
except ImportError as e:                     # opencv not installed
    raise unittest.SkipTest(f"OCR tests need the packages in requirements.txt ({e})")

import order_clustering as oc

HERE = os.path.dirname(os.path.abspath(__file__))
FIXTURES = os.path.join(HERE, "tests", "fixtures")
with open(os.path.join(FIXTURES, "synthetic_expected.json")) as f:
    EXPECTED = json.load(f)
LAYOUT = EXPECTED["layout"]                  # the layout the synthetic pad was printed with
FILLED = {int(k): v for k, v in EXPECTED["filled"].items()}


def photo(name):
    with open(os.path.join(FIXTURES, name), "rb") as f:
        return f.read()


def read_text(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def ticked(row):
    return sorted(t for t in LAYOUT["ticks"] if row[t])


class ReadPhotoTests(unittest.TestCase):
    def check_reading(self, data):
        result = ocr.read_order_pad(data, LAYOUT)
        rows = {r["No."]: r for r in result["rows"]}
        self.assertEqual(sorted(rows), sorted(FILLED))            # empty rows ignored
        for no, order in FILLED.items():
            self.assertEqual(ticked(rows[no]), sorted(order.get("ticks", [])), f"row {no}")
            self.assertTrue(result["ink"][no]["dish"], f"row {no} dish ink")
            self.assertEqual(result["ink"][no]["qty"], bool(order.get("qty")), f"row {no} qty")
            self.assertEqual(result["ink"][no]["other"], bool(order.get("other")), f"row {no}")
        self.assertIn("Cheese+: faint mark", rows[8]["Check"])     # faint mark highlighted
        return result

    def test_straight_scan(self):
        self.check_reading(photo("synthetic_scan.png"))

    def test_tilted_phone_photo_with_uneven_light(self):
        self.check_reading(photo("synthetic_photo.jpg"))

    def test_upside_down_photo(self):
        self.check_reading(photo("synthetic_photo_upside_down.jpg"))

    def test_sideways_photo(self):
        image = cv2.imdecode(np.frombuffer(photo("synthetic_photo.jpg"), np.uint8), cv2.IMREAD_COLOR)
        ok, encoded = cv2.imencode(".jpg", cv2.rotate(image, cv2.ROTATE_90_CLOCKWISE))
        self.check_reading(encoded.tobytes())

    def test_empty_pad_gives_no_rows(self):
        self.assertEqual(ocr.read_order_pad(photo("synthetic_empty_pad.jpg"), LAYOUT)["rows"], [])

    def test_photo_without_a_table_is_a_clear_error(self):
        with self.assertRaises(ocr.OcrError):
            ocr.read_order_pad(photo("not_a_pad.jpg"), LAYOUT)

    def test_file_that_is_not_an_image_is_a_clear_error(self):
        with self.assertRaises(ocr.OcrError):
            ocr.read_order_pad(b"not an image", LAYOUT)

    def test_without_api_key_text_is_left_for_the_user(self):
        result = ocr.read_order_pad(photo("synthetic_scan.png"), LAYOUT)
        self.assertTrue(all(r["Dish"] == "" for r in result["rows"]))
        self.assertFalse(result["used_api"])


class HandwritingTests(unittest.TestCase):
    def test_reply_is_parsed_and_unrequested_rows_ignored(self):
        reply = 'Sure: {"rows": [{"no": 1, "dish": "veg maggi", "qty": "", "other": "", ' \
                '"unsure": []}, {"no": 9, "dish": "made up"}]}'
        self.assertEqual(ocr.parse_handwriting_reply(reply, [1, 2]),
                         {1: {"dish": "veg maggi", "qty": "", "other": "", "unsure": []}})

    def test_unreadable_reply_is_an_error(self):
        with self.assertRaises(ocr.OcrError):
            ocr.parse_handwriting_reply("no json here", [1])

    def test_api_text_fills_the_table_and_unsure_is_highlighted(self):
        original = ocr.read_handwriting
        ocr.read_handwriting = lambda table, rows, layout, key, provider: {
            1: {"dish": "veg maggi", "qty": "", "other": "", "unsure": []},
            7: {"dish": "chicken ?", "qty": "", "other": "extra sauce", "unsure": ["dish"]}}
        try:
            result = ocr.read_order_pad(photo("synthetic_scan.png"), LAYOUT, api_key="test")
        finally:
            ocr.read_handwriting = original
        rows = {r["No."]: r for r in result["rows"]}
        self.assertEqual(rows[1]["Dish"], "veg maggi")
        self.assertIn("Dish: unsure reading", rows[7]["Check"])
        self.assertTrue(result["used_api"])

    def test_api_failure_becomes_a_warning_not_a_crash(self):
        original = ocr.read_handwriting

        def fail(*args):
            raise ocr.OcrError("Couldn't reach the handwriting service (no internet?).")
        ocr.read_handwriting = fail
        try:
            result = ocr.read_order_pad(photo("synthetic_scan.png"), LAYOUT, api_key="test")
        finally:
            ocr.read_handwriting = original
        self.assertEqual(len(result["rows"]), 8)
        self.assertTrue(result["warnings"])


class GeminiTests(unittest.TestCase):
    def test_gemini_request_and_reply(self):
        """Fake the network: check what is sent to Gemini and that its reply is read."""
        import io as _io
        import urllib.request
        sent = {}

        class FakeResponse(_io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        def fake_urlopen(request, timeout):
            sent["url"], sent["headers"] = request.full_url, dict(request.header_items())
            reply = {"candidates": [{"content": {"parts": [{"text":
                     '{"rows": [{"no": 2, "dish": "veg maggi", "qty": "", "other": "", "unsure": []}]}'}]}}]}
            return FakeResponse(json.dumps(reply).encode())
        original = urllib.request.urlopen
        urllib.request.urlopen = fake_urlopen
        try:
            table = np.full((100, 200, 3), 255, np.uint8)
            found = ocr.read_handwriting(table, [2], LAYOUT, "free-key", provider="gemini")
        finally:
            urllib.request.urlopen = original
        self.assertIn("generativelanguage.googleapis.com", sent["url"])
        self.assertEqual(sent["headers"].get("X-goog-api-key"), "free-key")
        self.assertEqual(found[2]["dish"], "veg maggi")

    def test_gemini_key_is_preferred_because_it_is_free(self):
        old = {k: os.environ.get(k) for k in ("GEMINI_API_KEY", "ANTHROPIC_API_KEY")}
        try:
            os.environ["GEMINI_API_KEY"], os.environ["ANTHROPIC_API_KEY"] = "g", "a"
            self.assertEqual(ocr.handwriting_key(), ("gemini", "g"))
            del os.environ["GEMINI_API_KEY"], os.environ["ANTHROPIC_API_KEY"]
            self.assertEqual(ocr.handwriting_key(), (None, ""))
        finally:
            for k, v in old.items():
                if v is not None:
                    os.environ[k] = v


class ValidateTests(unittest.TestCase):
    def row(self, no=1, dish="veg maggi", qty="", other="", **ticks):
        r = {"No.": no, "Dish": dish, "Qty": qty, "Other": other}
        r.update({t: ticks.get(t.replace("+", "_plus").replace(" ", "_"), False)
                  for t in LAYOUT["ticks"]})
        return r

    def test_good_row_and_blank_qty_means_one(self):
        clean, errors = ocr.validate_rows([self.row()], {}, LAYOUT)
        self.assertEqual(errors, [])
        self.assertEqual(clean[0]["qty"], "1")

    def test_empty_dish_blocks_saving(self):
        _, errors = ocr.validate_rows([self.row(dish="")], {}, LAYOUT)
        self.assertIn("Dish is empty", errors[0])

    def test_writing_seen_on_photo_cannot_be_saved_blank(self):
        flags = {1: {"dish": True, "qty": True, "other": True}}
        _, errors = ocr.validate_rows([self.row()], flags, LAYOUT)
        self.assertEqual(len(errors), 2)                     # Qty and Other

    def test_bad_qty_and_duplicate_numbers_are_errors(self):
        _, errors = ocr.validate_rows([self.row(qty="two"), self.row(no=1)], {}, LAYOUT)
        self.assertEqual(len(errors), 2)


class SaveTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.mkdtemp()
        self.path = os.path.join(self.folder, "data.csv")
        if os.path.exists("data.csv"):
            shutil.copy("data.csv", self.path)

    def tearDown(self):
        shutil.rmtree(self.folder)

    def csv_rows(self, numbers):
        clean = [{"no": n, "dish": "Veg Maggi", "qty": "1", "other": "",
                  "ticks": {t: t == "Half" for t in LAYOUT["ticks"]}} for n in numbers]
        return ocr.to_csv_rows(clean, "2026-11-19", 1, "pad.jpg", LAYOUT, used_api=False)

    def test_order_numbers_and_fields_are_kept(self):
        row = self.csv_rows([7])[0]
        self.assertEqual(row["row_id"], "2026-11-19/p1/07")
        self.assertEqual((row["variant"], row["item"], row["portion"], row["parcel"]),
                         ("veg", "maggi", "half", "N"))

    def test_customizations_are_joined(self):
        ticks = {t: t in ("Spicy+", "No onion") for t in LAYOUT["ticks"]}
        clean = [{"no": 1, "dish": "egg fried rice", "qty": "1", "other": "extra sauce",
                  "ticks": ticks}]
        row = ocr.to_csv_rows(clean, "2026-11-19", 1, "p.jpg", LAYOUT, False)[0]
        self.assertEqual(row["modifications"], "Spicy+; No onion; extra sauce")

    def test_save_appends_and_never_duplicates(self):
        before = read_text(self.path) if os.path.exists(self.path) else ""
        saved, dupes = ocr.save_verified(self.csv_rows([1, 2]), self.path)
        self.assertEqual((len(saved), dupes), (2, []))
        saved, dupes = ocr.save_verified(self.csv_rows([2, 3]), self.path)
        self.assertEqual([r["row_id"] for r in saved], ["2026-11-19/p1/03"])
        self.assertEqual(dupes, ["2026-11-19/p1/02"])
        text = read_text(self.path)
        self.assertTrue(text.startswith(before))              # original rows untouched

    def test_saved_file_still_reads_with_the_existing_tools(self):
        import canteen_tokens as ct
        ocr.save_verified(self.csv_rows([1]), self.path)
        rows, issues, fatal = ct.read_orders(self.path)
        self.assertIsNone(fatal)
        self.assertEqual([i for i in issues if i[1] == "skipped"], [])

    def test_new_file_gets_a_header(self):
        path = os.path.join(self.folder, "new.csv")
        ocr.save_verified(self.csv_rows([1]), path)
        self.assertTrue(read_text(path).startswith("row_id,source,date"))


class EndToEndTests(unittest.TestCase):
    """Photo -> reading -> (simulated) user checks and types dishes -> save -> clustering."""
    def test_photo_to_batch_suggestions(self):
        result = ocr.read_order_pad(photo("synthetic_photo.jpg"), LAYOUT)
        for row in result["rows"]:                            # what the user types in
            order = FILLED[row["No."]]
            row["Dish"], row["Qty"], row["Other"] = (order["dish"], order.get("qty", ""),
                                                     order.get("other", ""))
        clean, errors = ocr.validate_rows(result["rows"], result["ink"], LAYOUT)
        self.assertEqual(errors, [])
        folder = tempfile.mkdtemp()
        try:
            path = os.path.join(folder, "data.csv")
            rows = ocr.to_csv_rows(clean, "2026-11-19", 1, "pad.jpg", LAYOUT, False)
            saved, _ = ocr.save_verified(rows, path)
        finally:
            shutil.rmtree(folder)
        batches, individual = oc.cluster_orders(saved)
        # veg maggi rows 1, 2, 3, 4, 6 (6 plates - row 6 is 2 plates) fill one batch;
        # row 8 is left over; rows 5 and 7 have customizations
        self.assertEqual([[i[-2:] for i in b["order_ids"]] for b in batches],
                         [["01", "02", "03", "04", "06"]])
        self.assertEqual(batches[0]["total_quantity"], 6)
        reasons = {e["order_id"][-2:]: e["reason"] for e in individual}
        self.assertEqual(sorted(reasons), ["05", "07", "08"])
        self.assertIn("customized", reasons["05"])
        self.assertIn("left over", reasons["08"])


if __name__ == "__main__":
    unittest.main()
