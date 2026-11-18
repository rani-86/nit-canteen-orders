"""
ocr_app.py - upload a photo of a filled order pad, check it, save it, see batches

Run:   streamlit run ocr_app.py

Upload photo -> read the form -> CHECK AND CORRECT every row -> Confirm and Save
-> data.csv -> existing cluster_orders() -> batch + individual preparation.

Nothing is written to data.csv until the user presses "Confirm and Save".
Handwriting is read automatically only if ANTHROPIC_API_KEY is set; otherwise
the ticks are read automatically and the user types the dish names.
"""

import datetime
import hashlib
import os

import pandas as pd
import streamlit as st

import order_clustering as oc
import order_ocr as ocr

st.set_page_config(page_title="NIT Canteen - read an order pad", layout="wide")
st.title("NIT Canteen - read a filled order pad")
st.caption("Photo of the pad -> check every row -> Confirm and Save -> batch suggestions. "
           "Nothing is saved until you press Confirm and Save.")

try:
    layout = ocr.load_layout()
except ocr.OcrError as e:
    st.error(str(e))
    st.stop()

api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
if api_key:
    st.info("Handwriting will be read automatically. Check every value before saving.")
else:
    st.info("No API key set: tick boxes are read automatically, and you type the dish "
            "names from the row pictures.")

# ---------- Step 1: upload ----------
col_date, col_page = st.columns(2)
pad_date = col_date.date_input("Date written on the pad", datetime.date.today())
pad_page = col_page.number_input("Page number on the pad", min_value=1, step=1, value=1)
upload = st.file_uploader("Step 1 - photo or scan of the filled pad (JPG, JPEG or PNG)",
                          type=["jpg", "jpeg", "png"])
if upload is None:
    st.stop()

data = upload.getvalue()
key = hashlib.sha1(data).hexdigest()
state = st.session_state
if state.get("key") != key:                       # a new photo: start again
    state.update(key=key, result=None, error=None, saved=None)

# ---------- Step 2: read the form ----------
if state.result is None and state.error is None:
    with st.spinner("Reading the form..."):
        try:
            state.result = ocr.read_order_pad(data, layout, api_key or None)
        except ocr.OcrError as e:
            state.error = str(e)

left, right = st.columns([2, 3])
left.image(data, caption=f"Uploaded photo: {upload.name}", use_container_width=True)

if state.error:
    right.error(f"Couldn't read this photo: {state.error}")
    right.write("Try again with the whole page in the photo, flat, in good light, with no "
                "shadow across the table - then upload it again.")
    if right.button("Type the rows by hand instead"):
        state.result = {"rows": [], "ink": {}, "crops": {}, "used_api": False, "warnings": []}
        state.error = None
        st.rerun()
    st.stop()

result = state.result
for warning in result["warnings"]:
    right.warning(warning)

# ---------- Step 3: human verification ----------
right.subheader(f"Step 2 - check every row ({len(result['rows'])} read from the photo)")
right.write("Fix anything that's wrong, type anything missing, delete rows that aren't "
            "orders, or add rows. Rows marked in **Check** need a closer look.")
columns = ["No.", "Dish", "Qty"] + layout["ticks"] + ["Other", "Check"]
table = pd.DataFrame(result["rows"], columns=columns)
config = {"No.": st.column_config.NumberColumn("No.", min_value=1, step=1),
          "Dish": st.column_config.TextColumn("Dish"),
          "Qty": st.column_config.TextColumn("Qty", help="Blank = 1"),
          "Other": st.column_config.TextColumn("Other instructions"),
          "Check": st.column_config.TextColumn("Check (needs a look)", disabled=True)}
for tick in layout["ticks"]:
    config[tick] = st.column_config.CheckboxColumn(tick, default=False)
edited = right.data_editor(table, column_config=config, num_rows="dynamic",
                           hide_index=True, key=f"editor_{key}", use_container_width=True)

flagged = [row for row in result["rows"] if row["Check"]]
if flagged:
    with st.expander(f"Rows to double-check ({len(flagged)}) - picture of each row", expanded=True):
        for row in flagged:
            st.image(result["crops"][row["No."]],
                     caption=f"Row {row['No.']:02d}: {row['Check']}", use_container_width=True)

# ---------- Step 4: confirm and save ----------
if state.saved is None:
    if st.button("Confirm and Save", type="primary"):
        rows = edited.to_dict("records")
        clean, errors = ocr.validate_rows(rows, result["ink"], layout)
        if errors:
            st.error("Not saved - please fix these first:")
            for err in errors:
                st.write(f"- {err}")
        elif not clean:
            st.warning("There are no rows to save.")
        else:
            csv_rows = ocr.to_csv_rows(clean, pad_date.isoformat(), int(pad_page),
                                       upload.name, layout, result["used_api"])
            try:
                saved, duplicates = ocr.save_verified(csv_rows)
            except OSError as e:
                st.error(f"Couldn't write data.csv: {e}")
                st.stop()
            state.saved = {"saved": saved, "duplicates": duplicates,
                           "extracted": len(result["rows"]), "verified": len(clean)}
            st.rerun()
    st.stop()

# ---------- Step 5 and 6: clustering and results ----------
summary = state.saved
saved, duplicates = summary["saved"], summary["duplicates"]
if duplicates:
    st.warning(f"{len(duplicates)} order(s) were already in data.csv and were not saved "
               f"again: {', '.join(duplicates)}")
if saved:
    st.success(f"Saved {len(saved)} checked order(s) to data.csv.")
batches, individual = oc.cluster_orders(saved) if saved else ([], [])
batched = sum(b["compatible_orders"] for b in batches)

counts = st.columns(5)
counts[0].metric("Extracted", summary["extracted"])
counts[1].metric("Verified", summary["verified"])
counts[2].metric("Saved (new)", len(saved))
counts[3].metric("Batched", batched)
counts[4].metric("Individual", len(individual))


def order_no(row_id):
    return row_id.rsplit("/", 1)[-1]


st.subheader("Batch Preparation")
st.caption("A batch is cooked together only. Every order is still packed and handed over "
           "separately under its own token number.")
if batches:
    batch_table = pd.DataFrame([{
        "Batch ID": b["batch_id"], "Dish": b["dish"],
        "Order numbers": ", ".join(order_no(i) for i in b["order_ids"]),
        "Total plates": b["total_quantity"], "Recommendation": b["recommendation"]}
        for b in batches])
    st.dataframe(batch_table, hide_index=True, use_container_width=True)
    st.download_button("Download batch report (CSV)", batch_table.to_csv(index=False),
                       "batch_report_page.csv", "text/csv")
else:
    st.info(f"No batch: no dish had {oc.MIN_BATCH_SIZE}+ uncustomized orders on this page.")

st.subheader("Individual Preparation")
if individual:
    individual_table = pd.DataFrame([{
        "Order number": order_no(e["order_id"]), "Dish": e["dish"],
        "Customization": e["customization"], "Reason": e["reason"]} for e in individual])
    st.dataframe(individual_table, hide_index=True, use_container_width=True)
    st.download_button("Download individual list (CSV)", individual_table.to_csv(index=False),
                       "individual_orders_page.csv", "text/csv")
else:
    st.info("No orders need individual preparation.")

if st.button("Read another photo"):
    for name in ("key", "result", "error", "saved"):
        state.pop(name, None)
    st.rerun()
