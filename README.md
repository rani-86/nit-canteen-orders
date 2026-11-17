# NIT Canteen: order mix-up checker

**Place:** NIT Canteen, NIT Jamshedpur campus (night canteen)
**Person:** Kamal Singh ("Canteen Bhaiya"), order-taker. He takes orders, packs, garnishes and calls out names; a second person cooks.
**Dates:** 17–18 Nov 2026
**By:** Rani

## The problem
During the 10 pm – 1 am rush, students order from the sitting area, and almost every order is customised (half/full, parcel, corn, no onion, less spicy). Kamal writes each order in shorthand on a notepad but **never writes who it's for**. He keeps track of the matching in his head and calls out names at hand-over. He's surprisingly good at it, but when two people order the same dish, plates can go to the wrong person or be served out of turn.

More detail: [research.md](research.md) · [fieldwork.md](fieldwork.md) · raw data: [data.csv](data.csv)

## What the tool does
`canteen_tokens.py` reads `data.csv` and gives **one useful output: which orders are look-alikes, plus a numbered token slip for every order.**

1. **Checks every row** and prints a warning for bad values (wrong portion, non-number qty, bad time) instead of crashing.
2. **Finds look-alike orders:** the same dish written on the same notepad page. These are the orders Kamal has to tell apart from memory. For each group it says whether a swap would give someone the **wrong food** (the orders differ in portion/extras/parcel) or just serve someone **out of turn** (identical orders).
3. **Writes token slips** to `slips.txt`, one per order, with look-alikes marked `!! LOOK-ALIKE`. Each slip shows its `data.csv` row so it can be traced back to the notepad photo.
4. **Sizes the token set** when there's enough timed data: it replays the night from `time_ordered` / `time_served`, finds the busiest moment (most orders open at once) and adds a 25% buffer.

## How to run
Needs Python 3.8+. No packages to install.

```bash
python canteen_tokens.py            # reads data.csv in this folder
python canteen_tokens.py other.csv  # or any CSV with the same columns
```

Settings are at the top of the script:

| Setting | Default | Meaning |
|---|---|---|
| `MIN_LOOKALIKE` | 2 | how many times the same dish must appear on one page to count as look-alikes |
| `TOKEN_BUFFER` | 1.25 | spare tokens on top of the busiest moment |
| `MIN_TIMED_ROWS` | 5 | timed orders needed before it will size tokens |
| `NEXT_DAY_BEFORE` | 12 | times before 12:00 are treated as after midnight |

## Real output (on my data.csv)
```
================================================================
 NIT Canteen - order mix-up check   (data.csv)
================================================================

Read 21 orders. 18 readable, 3 with item/variant I couldn't read (rows 4, 9, 19).

Most written dishes:
  6 x VEG NOODLES (?)
  2 x MASALA OMELETTE
  2 x CHICKEN NOODLES (?)
  2 x VEG MAGGI
  2 x VEG CHOWMEIN
Orders with extras/changes: 3 of 18
Parcel orders: 3   Half plates: 10

LOOK-ALIKE ORDERS (same dish 2+ times on one page)
----------------------------------------------------------------
notepad_p1: 2 x VEG NOODLES (?)
   row  1: half  | -                        | dine-in
   row  8: ?     | -                        | dine-in
   -> same dish, some portions unreadable: a swap is possible either way
notepad_p2: 2 x VEG CHOWMEIN
   row 15: ?     | -                        | dine-in
   row 17: half  | -                        | dine-in
   -> same dish, some portions unreadable: a swap is possible either way
notepad_p2: 4 x VEG NOODLES (?)
   row 11: half  | corn                     | dine-in
   row 13: half  | corn+garlic; ? no        | dine-in
   row 18: half  | -                        | dine-in
   row 20: ?     | -                        | dine-in
   -> they differ: a swap gives someone the WRONG food

8 of 18 readable orders (44%) had a look-alike on the same page -> these need a token most.

WAIT TIMES AND TOKENS
----------------------------------------------------------------
Only 0 orders have both times (need 5). Can't size tokens yet -
record time_ordered and time_served during the rush.

Token slips for 18 orders written to slips.txt.
```

First lines of `slips.txt`:
```
#01 | VEG NOODLES (?)            | HALF x1 | -                        | DINE-IN | row 1  !! LOOK-ALIKE
#02 | EGG NOODLES (?)            | HALF x1 | -                        | DINE-IN | row 2
#03 | MASALA OMELETTE            | FULL x2 | -                        | DINE-IN | row 3
#04 | EGG CHOWMEIN (?)           | HALF x1 | -                        | DINE-IN | row 5
#05 | CHICKEN NOODLES (?)        | HALF x1 | -                        | PARCEL  | row 6
```

**What this means for Kamal:** almost half the readable orders (8 of 18) had a look-alike on the same page. The worst case is page 2: four half-plate veg noodles at once, three with different extras. If he hands the "corn" plate to the "corn + garlic" student, someone gets the wrong food. A numbered token for each order removes the need to remember who's who.

## What it gets wrong / shouldn't be trusted for
- **A notepad page is not a time window.** The script treats orders on the same page as "open together", but a page could cover 10 minutes or an hour. Real overlap needs times, which the notepad doesn't have.
- **It can't size the token set yet.** No order in my data has both times. The only order I timed was my own on visit 2, and I left without receiving it. The token-count part works (I tested the logic on a throwaway file), but there's no real result for it.
- **It relies on my reading of his handwriting.** The most common word, which I read as "noodles?", appears in most of the look-alike groups. If that reading is wrong, the groups and the 44% change.
- **"Same dish" is decided by text, not by how the food looks.** Veg noodles and veg chowmein are different rows here, but they may look identical on a plate, so the real risk could be higher than shown.
- **It only sees changes Kamal wrote down.** Changes said out loud but not written (less spicy, less salt) don't appear, so "orders with extras" (3 of 18) is an undercount.
- **It covers one evening, not exam week.** The rush during exams is much heavier.
- **Tokens are only a fix if students keep them.** The tool doesn't measure that.

## Where else this could work
Other campus counters with customised orders and long waits, like Dosa Corner or Nescafe.

## Demo video
3-minute screen recording of the tool running: [add link]

## AI use
I used AI for research, the first transcription of the notepad photos (which I then checked) and the code. The chats are exported in [`ai-chats/`](ai-chats/). The fieldwork visits, the conversation with Kamal and `reflection.md` are my own.
