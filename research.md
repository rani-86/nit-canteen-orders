# Research — Order mix-ups at the NIT Canteen night counter

**Place:** NIT Canteen, NIT Jamshedpur campus — within walking distance of my hostel
**Person:** Canteen Bhaiya, the order-taker at the counter. He takes orders, packs them, does the garnishing and calls out names at hand-over. A second person does the cooking.
**Date observed:** 17 Nov 2026, ~11 pm
**Author:** Rani

---

## The problem (5 lines)

1. NIT Canteen has two people at night: one cooks, and Canteen Bhaiya takes orders, packs them, garnishes, bills and calls students to collect.
2. Students order out loud and often customise: half or full plate, parcel, "corn", "mixed", "no onion".
3. He writes each order in his own shorthand on a notepad (½ = half plate, ⓟ = parcel, bracket + total = one group's bill) and strikes it off later.
4. He calls out names at hand-over, but no names are written on the pad, so the link between an order and a student is only in his memory.
5. When several similar orders are open at once (e.g. two half veg maggi, one with corn), they are easy to mix up. I have to check this with my data: wrong hand-overs, "kiska hai?" moments, remakes.

---

## Existing solutions and why they don't fit him

### 1. Restaurant POS with KOT printing (e.g. Petpooja)
A billing tablet takes the order and prints a Kitchen Order Ticket with a token number, order type and modifiers.

**Why it doesn't fit:**
- Bhaiya is the only person on the order side, and between orders he is packing and garnishing. Typing every order into a tablet in the middle of a rush adds work instead of removing it.
- It needs hardware and a subscription. Petpooja doesn't publish its prices, and one competitor estimates printers at ₹4,000–8,000 each on top of the subscription. That's a lot for a small night counter.
- It replaces his notepad, which already works for him. It doesn't just fill the one gap the notepad has: who the order belongs to.

### 2. QR "scan and order" (students order and pay on their phones)
Students scan a QR code, choose items and pay, and the order shows up on a screen for the cook.

**Why it doesn't fit:**
- Every item has to be put online with half/full and modification options. Students at the counter shout orders and change them on the spot.
- Someone would need to keep watching a phone screen. The cook is at the stove and Bhaiya's hands are busy packing.
- Even with digital orders, he still has to match each plate to the right student at hand-over, which is the actual problem.

---

## Sources I read

### Source 1: Petpooja, "Kitchen Order Ticket (KOT): Meaning, Format & How It Works"
https://blog.petpooja.com/glossary/kitchen-order-ticket/
Read on: ___

- A KOT records a serial number, a table/token number, the order type (dine-in/takeaway), items with quantities, modifiers like "less spicy", and a time stamp. It does not record price or customer name.
- It says a handwritten chit does the same job as a printed KOT, but breaks down in a rush when a chit goes missing, and it has no time stamps.
- **What I took from it:** his notepad is already a KOT without two fields, a token number and the time. Those two missing fields are what my tool should focus on.

### Source 2: Intouch Insight, "2026 Drive-Thru Study: Key Drive-Thru Trends"
https://intouchinsight.com/blog/drive-thru-trends
Read on: ___

- Mystery-shop study of 13 US fast-food chains (165 visits per brand, June–July 2026). Order accuracy was 89.3%, so roughly 1 order in 10 was still wrong.
- The biggest cause was customisation: 43% of the wrong orders had a special request filled incorrectly.
- **What I took from it:** a very different setting (big US chains with screens and staff), but even there customised orders cause most errors. I'll check whether orders with modifications go wrong more often at this canteen too.

---

## Starting plan (will change after fieldwork)

Find out how an order gets from Bhaiya to the cook (spoken, or read off the pad?). Record each order's time placed, time served, item, portion, parcel, modifications, and any mix-up. Then replay the night to find (a) the largest number of orders open at once and (b) moments when similar-looking orders were open together. Use that to size a cheap numbered-token system that fits his existing notepad habit.
