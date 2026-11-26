"""Suite 1.8 arithmetic, structured and context tracks (docs/SUITE_1_8_DESIGN.md), collected by suite_1_8.py.

These use `cases.Case` and the existing scoring (`cases.score_answer`), so they need no new scoring code.
Suite 1.7 cases that are realistic tasks survive here reworded, usually with a step added; bare calculator
sums, operator-precedence puzzles and one-line sorts are retired.

- Arithmetic: every expected value is computed below from the numbers in its prompt (exact fractions where
  it matters), so a typo in a hand-worked answer can't slip in.
- Structured: answers are written out, and each was checked by the blind solve.
- Context: logs are generated from a seeded random generator, and each answer is computed from the generated
  records, never typed by hand. Long logs stay at suite 1.7's maximum length.
"""
import random
from datetime import date, datetime, timedelta
from fractions import Fraction as F

from cases import Case
from suite_1_8 import JSON_NUMBER, JSON_STRING


def money(value):
    return float(round(F(value), 2))


def exact(value):
    """A whole-number answer, refusing to truncate one that isn't whole."""
    value = F(value)
    assert value.denominator == 1, value
    return int(value)


def arithmetic(case_id, prompt, answer, difficulty, *, suffix=JSON_NUMBER, tolerance=None):
    if tolerance is None:
        tolerance = 0.005 if isinstance(answer, float) else 0.0
    return Case(f"math-{case_id}", "arithmetic", prompt + suffix, answer, difficulty=difficulty, tolerance=tolerance)


def savings(start, rate, withdrawal, years):
    balance = F(start)
    for _ in range(years):
        balance = balance * (1 + F(rate)) - withdrawal
    return balance


def gross_pay(rate, weekday_hours, sunday_hours, limit):
    total = sum(weekday_hours)
    regular, overtime = min(total, limit), max(total - limit, 0)
    return F(rate) * regular + F(rate) * F(3, 2) * overtime + F(rate) * 2 * sunday_hours


def meeting_time(distance, slow, fast, stop_at, stop_minutes):
    """Two trains leave at 09:00 towards each other; the fast one stops at `stop_at` hours after 09:00."""
    gap = F(distance) - (slow + fast) * F(stop_at)
    gap -= slow * F(stop_minutes, 60)
    hours = F(stop_at) + F(stop_minutes, 60) + gap / (slow + fast)
    assert gap > 0
    moment = datetime(2026, 1, 1, 9) + timedelta(minutes=float(hours * 60))
    return moment.strftime("%H:%M")


def arithmetic_cases():
    trial_end = (date(2028, 1, 15) + timedelta(days=89)).isoformat()
    interest_days = (date(2028, 3, 5) - date(2028, 2, 20)).days
    tiles = -(-(F("4.2") * F("3.6") / (F("0.6") * F("0.6"))) * F(11, 10) // 1)
    return [
        # Controls: one realistic calculation.
        arithmetic("bill-split", "Dinner for four costs $86.40, plus a 12.5% service charge. The total is split "
                   "equally. How much does each person pay, in dollars?",
                   money(F("86.40") * F("1.125") / 4), "control"),
        arithmetic("unit-price", "A 750 g bag of rice costs $4.20 and a 2 kg bag costs $10.40. How much cheaper per "
                   "kilogram is the 2 kg bag, in dollars?",
                   money(F("4.20") / F("0.75") - F("10.40") / 2), "control"),
        arithmetic("checkout-discount", "A jacket priced at $64 gets 15% off at the checkout. What do I pay, in "
                   "dollars?", money(64 * F("0.85")), "control"),

        # Challenges: two or three steps (several are suite 1.7 cases with a step added).
        arithmetic("hose-fill", "A garden hose fills a 135-litre water butt in 9 minutes. A 600-litre tank already "
                   "holds 225 litres. How many minutes does the same hose take to fill the tank?",
                   exact((600 - 225) / F(135, 9)), "challenge"),
        arithmetic("rent-up-down", "A monthly rent of $1,250 rises by 20%. A year later the new rent is cut by 20%. "
                   "What is the monthly rent now, in dollars?", money(1250 * F("1.2") * F("0.8")), "challenge"),
        arithmetic("night-bus", "A night bus leaves at 22:47 and arrives at 03:18 the next morning. On the way it "
                   "waits at a depot for 25 minutes. For how many minutes is the bus moving?",
                   (24 * 60 - (22 * 60 + 47)) + 3 * 60 + 18 - 25, "challenge"),
        arithmetic("two-painters", "Ana can paint a fence alone in 6 hours, and Ben can paint it alone in 3 hours. "
                   "They paint together for 1 hour, then Ben leaves. How many more hours does Ana need to finish?",
                   exact((1 - (F(1, 6) + F(1, 3))) / F(1, 6)), "challenge"),
        arithmetic("lemonade-syrup", "A café has 40 litres of lemonade that is 15% syrup by volume. How many litres "
                   "of pure syrup must be added to make it 20% syrup?",
                   float((F("0.20") * 40 - F("0.15") * 40) / (1 - F("0.20"))), "challenge"),
        arithmetic("race-pace", "A runner finishes a 10 km race in 52 minutes 30 seconds. What is the average pace, "
                   "in seconds per kilometre?", (52 * 60 + 30) // 10, "challenge"),
        arithmetic("meal-total", "A meal costs $80 before tax. Sales tax is 8% of the meal price, and we tip 18% of "
                   "the meal price before tax. What is the total, in dollars?",
                   money(80 + 80 * F("0.08") + 80 * F("0.18")), "challenge"),

        # Hard: four to six steps, distractors, calendar boundaries, and shortcuts that give a wrong answer.
        arithmetic("savings-withdrawals", "A savings account starts with $3,000 and earns 4% interest once a year. "
                   "Straight after each yearly interest payment, $200 is withdrawn. What is the balance after the "
                   "third withdrawal, in dollars, to the nearest cent?",
                   money(savings(3000, "0.04", 200, 3)), "hard"),
        arithmetic("trial-end", "A 90-day free trial starts on 15 January 2028, which counts as day 1. On what date "
                   'is its last day? The "answer" value must be a date in the form "YYYY-MM-DD".',
                   trial_end, "hard", suffix=JSON_STRING),
        arithmetic("round-trip-speed", "A cyclist rides 30 km to work at 20 km/h and rides the same 30 km home at "
                   "30 km/h. What is the average speed for the whole round trip, in km/h?",
                   exact(60 / (F(30, 20) + F(30, 30))), "hard"),
        arithmetic("stacked-offers", "A coat has a price tag of $120. The shop takes 30% off the tag price, then a "
                   "further 20% off the reduced price at the checkout, then I use a $5 voucher, and finally 10% sales "
                   "tax is added to what remains. Members get another 5% off, but I am not a member. What do I pay, "
                   "in dollars, to the nearest cent?",
                   money((120 * F("0.7") * F("0.8") - 5) * F("1.1")), "hard"),
        arithmetic("heater-cost", "A heater draws 2.4 kW. It runs 3.5 hours a day on weekdays and 6 hours a day at "
                   "weekends. Electricity costs 28 cents per kWh on weekdays and 22 cents per kWh at weekends. What "
                   "does the heater cost to run for one week, in dollars, to the nearest cent?",
                   money(F("2.4") * F("3.5") * 5 * F("0.28") + F("2.4") * 6 * 2 * F("0.22")), "hard"),
        arithmetic("shift-pay", "I earn $24 an hour. Weekday hours beyond 38 in a week are paid at 1.5 times the "
                   "rate. Sunday hours are paid at twice the rate and never count towards the 38. This week I worked "
                   "Monday 9 hours, Tuesday 9, Wednesday 8, Thursday 10, Friday 7 and Sunday 5. What is my gross pay "
                   "for the week, in dollars?", exact(gross_pay(24, (9, 9, 8, 10, 7), 5, 38)), "hard"),
        arithmetic("currency-change", "I change £500 into euros at 1.16 euros per pound. The bureau keeps a 2% "
                   "commission on the euros and charges a flat €3 fee. After my trip I change my unused €100 back "
                   "into pounds at 0.84 pounds per euro, with no fees. In pounds, what is the net amount the euros "
                   "cost me, counting only pounds paid out and pounds received back?",
                   exact(500 - 100 * F("0.84")), "hard"),
        arithmetic("leap-interest", "A $5,000 loan accrues simple interest of 0.02% per day. Interest is charged for "
                   "each day from 20 February 2028 up to but not including 5 March 2028. How much interest is "
                   "charged, in dollars?", money(5000 * F("0.0002") * interest_days), "hard"),
        arithmetic("floor-tiles", "A room measures 4.2 m by 3.6 m. Tiles are 60 cm by 60 cm and come in boxes of 8. "
                   "Add 10% to the number of tiles needed to cover the floor, to allow for cuts, and round up to "
                   "whole tiles. How many boxes must I buy?", exact(-(-tiles // 8)), "hard"),
        arithmetic("trains-meet", "Two stations are 300 km apart. At 09:00 a train leaves each station towards the "
                   "other, one at 80 km/h and the other at 120 km/h. At 10:00 the faster train stops for 15 minutes, "
                   'then carries on at the same speed. At what time do they meet? The "answer" value must be a '
                   '24-hour "HH:MM" time.', meeting_time(300, 80, 120, 1, 15), "hard", suffix=JSON_STRING),
    ]


ARITHMETIC_CASES = arithmetic_cases()


def structured(case_id, prompt, answer, difficulty):
    return Case(f"json-{case_id}", "structured", prompt, answer, difficulty=difficulty)


ONLY_OBJECT = " Return only the JSON object."
ONLY_ARRAY = " Return only the JSON array."


def structured_cases():
    return [
        # Controls: a few typed fields from a realistic message.
        structured("crate-note", 'From this message, extract "id" (string), "units" (integer) and "fragile" '
                   '(boolean): "Please mark crate B-023 as fragile and send 12 units with it."' + ONLY_OBJECT,
                   {"id": "B-023", "units": 12, "fragile": True}, "control"),
        structured("unassigned-ticket", 'From this message, extract "ticket" (string) and "owner" (the owner\'s '
                   'name as a string, or null if nobody owns it): "Ticket P-42 is still waiting for someone to pick '
                   'it up."' + ONLY_OBJECT, {"ticket": "P-42", "owner": None}, "control"),
        structured("node-down", 'From this alert, extract "node" (string) and "connected" (boolean): "Node M-14 '
                   'dropped off the network at 03:12 and has not come back."' + ONLY_OBJECT,
                   {"node": "M-14", "connected": False}, "control"),

        # Challenges: nested extraction, arrays in source order, filtering and grouping.
        structured("order-items", 'Extract an object with keys "order" (the order number as a string) and '
                   '"items" (an array of objects with keys "sku" (string) and "qty" (integer), in the order the '
                   'items appear) from: "Order 7781: three keyboards (KB-2), one mouse (MS-9) and a dozen USB '
                   'cables (CB-1)."' + ONLY_OBJECT,
                   {"order": "7781", "items": [{"sku": "KB-2", "qty": 3}, {"sku": "MS-9", "qty": 1},
                                               {"sku": "CB-1", "qty": 12}]}, "challenge"),
        structured("dock-words", 'Extract "dock" (integer) and "crates" (integer) from: "Dock seven received '
                   'forty-two crates and two pallets this morning."' + ONLY_OBJECT,
                   {"dock": 7, "crates": 42}, "challenge"),
        structured("quoted-note", 'Return a JSON object with one key "note" whose value is exactly the text between '
                   'the angle brackets, character for character: <She said "ship it" from C:\\builds\\v2>'
                   + ONLY_OBJECT, {"note": 'She said "ship it" from C:\\builds\\v2'}, "challenge"),
        structured("dry-beds", "Soil moisture readings for the garden beds: B1 48%, B2 23%, B3 31%, B4 12%, B5 30%. "
                   "Return a JSON array of the IDs of the beds below 30%, ordered from driest to wettest."
                   + ONLY_ARRAY, ["B4", "B2"], "challenge"),
        structured("tasks-by-owner", "Sprint board: T1 owned by Ana, T2 by Ben, T3 by Ana, T4 by Cy, T5 by Ben, T6 "
                   "unassigned. Return a JSON object whose keys are the owners' names and whose values are arrays of "
                   "that owner's task IDs in the order listed. Leave out unassigned tasks." + ONLY_OBJECT,
                   {"Ana": ["T1", "T3"], "Ben": ["T2", "T5"], "Cy": ["T4"]}, "challenge"),
        structured("meeting-times", 'Today\'s meetings: Room B at 1:05 pm, Room A at 9:40 am, Room C at 12:15 am '
                   '(the overnight support handover). Return a JSON array of objects with keys "room" (the room '
                   'letter as a string) and "time" (a 24-hour "HH:MM" string), sorted from earliest to latest time '
                   'of day.' + ONLY_ARRAY,
                   [{"room": "C", "time": "00:15"}, {"room": "A", "time": "09:40"}, {"room": "B", "time": "13:05"}],
                   "challenge"),
        structured("nothing-available", 'Stock check: A1 expired, A2 damaged, A3 missing, A4 available from Monday. '
                   'Return a JSON array of the IDs that are available now.' + ONLY_ARRAY, [], "challenge"),

        # Hard: conflicting information, normalised values, conditional keys, enumerations.
        structured("latest-contact", 'From this email thread, extract the customer\'s current contact details as '
                   'an object with keys "name", "phone" and "email", all strings, using the latest value of each. '
                   'Messages, oldest first:\n'
                   '1. "Hi, I\'m Priya Shah. Reach me on 07700 900123 or priya.shah@example.com."\n'
                   '2. "Small update: my mobile is now 07700 900456."\n'
                   '3. "Please use priya@shahdesign.example for anything about the order. The number in my last '
                   'message is still right."\n'
                   'Write the phone number exactly as the customer wrote it.' + ONLY_OBJECT,
                   {"name": "Priya Shah", "phone": "07700 900456", "email": "priya@shahdesign.example"}, "hard"),
        structured("appointments-iso", 'Turn these appointments into a JSON array of objects with keys "date" '
                   '("YYYY-MM-DD") and "time" (24-hour "HH:MM"), sorted from earliest to latest: "Dentist on Tue 3rd '
                   'March 2026 at quarter past two in the afternoon; haircut on 28/02/2026 (day/month/year) at 9 am; '
                   'car service on March 3rd 2026 at 8.30 in the morning."' + ONLY_ARRAY,
                   [{"date": "2026-02-28", "time": "09:00"}, {"date": "2026-03-03", "time": "08:30"},
                    {"date": "2026-03-03", "time": "14:15"}], "hard"),
        structured("invoice-cents", 'Convert this invoice into a JSON object with keys "lines" (an array of objects '
                   'with keys "item" (the name exactly as written, as a string) and "cents" (integer), in invoice order) and "total_cents" (integer, '
                   'the sum of the lines). Invoice: "Design work $1,234.50; Hosting (annual) $96; Domain renewal '
                   '$12.99; Credit for late delivery -$50.00."' + ONLY_OBJECT,
                   {"lines": [{"item": "Design work", "cents": 123450}, {"item": "Hosting (annual)", "cents": 9600},
                              {"item": "Domain renewal", "cents": 1299},
                              {"item": "Credit for late delivery", "cents": -5000}], "total_cents": 129349}, "hard"),
        structured("conditional-orders", 'Extract the two orders in this message as a JSON array of objects, in the '
                   'order they appear. Each object has "customer" (string) and "qty" (integer). Add "discount_code" '
                   '(string) only if the customer gives one, and add "gift_note" (string) only if the order is a '
                   'gift; otherwise leave those keys out. Message: "Sam wants 2 mugs and is using code SPRING10. Lee '
                   'wants 1 mug as a gift for his sister, with the note \'Happy birthday, Mo\'; he has no code."'
                   + ONLY_ARRAY,
                   [{"customer": "Sam", "qty": 2, "discount_code": "SPRING10"},
                    {"customer": "Lee", "qty": 1, "gift_note": "Happy birthday, Mo"}], "hard"),
        structured("ticket-priority", 'Classify each ticket and return a JSON object mapping each ticket ID, written with its "#" as below, to its '
                   'priority, which must be one of "low", "medium" or "high". Rules: an outage is "high"; a bug that '
                   'blocks payment is "high"; any other bug is "medium"; questions and feature requests are "low". '
                   'Tickets: #11 "Can I export to CSV?"; #12 "The site is down for everyone"; #13 "Typo on the '
                   'pricing page"; #14 "Checkout button does nothing, so customers can\'t pay"; #15 "Please add dark '
                   'mode".' + ONLY_OBJECT,
                   {"#11": "low", "#12": "high", "#13": "medium", "#14": "high", "#15": "low"}, "hard"),
        structured("login-counts", 'Event log, in order: login u3, login u1, logout u3, failed_login u2, login u3, '
                   'login u2, logout u1, login u1, failed_login u3, login u2, login u4. Return a JSON object mapping '
                   'each user ID to its number of successful "login" events, including only users with at least two '
                   'of them.' + ONLY_OBJECT,
                   {"u3": 2, "u1": 2, "u2": 2}, "hard"),
        structured("address-fields", 'Split this address into a JSON object with keys "flat" (the flat '
                   'identifier as a string, without the word "flat", or null if there is none), "number" (integer), "street", "city" and "postcode" (strings). Write the postcode '
                   'in capitals with one space before its last three characters, and the city in title case. '
                   'Address: "flat 4b, 27 Mill Lane, YORK yo1 7hb"' + ONLY_OBJECT,
                   {"flat": "4b", "number": 27, "street": "Mill Lane", "city": "York", "postcode": "YO1 7HB"}, "hard"),
        structured("attendees", 'From this thread, return a JSON array of the names of everyone attending the '
                   'workshop, sorted alphabetically. Messages, oldest first: "Ana: I\'m in." "Ben: Count me in too." '
                   '"Cy: Can\'t make it, sorry." "Dee: Yes please." "Ana: Something came up, I have to drop out." '
                   '"Cy: Plans changed, I can come after all." "Eli: Maybe, I\'ll confirm tomorrow."' + ONLY_ARRAY,
                   ["Ben", "Cy", "Dee"], "hard"),
        structured("recipe-grams", 'Convert this ingredient list to a JSON object with the keys "flour", "sugar", '
                   '"butter" and "eggs", mapping each ingredient to its weight in grams as an integer, keeping the order. Use these conversions: 1 cup of flour = 120 g, '
                   '1 cup of sugar = 200 g, 1 tablespoon of butter = 14 g, 1 egg = 50 g. Ingredients: "2½ cups '
                   'flour, ¾ cup sugar, 3 tablespoons butter, 2 eggs".' + ONLY_OBJECT,
                   {"flour": 300, "sugar": 150, "butter": 42, "eggs": 100}, "hard"),
        structured("rebooked-itinerary", 'Build my itinerary from these airline messages as a JSON array of flight '
                   'objects with keys "flight" (the flight number exactly as written), "from" and "to" (airport codes), "departs" (24-hour "HH:MM") '
                   'and "minutes" (integer flight time), in travel order. Leave out cancelled flights. Messages, '
                   'oldest first:\n'
                   '1. "Booked: LX 318 LHR to ZRH, departs 7:10 am, arrives 9:55 am."\n'
                   '2. "Booked: LX 1612 ZRH to OTP, departs 11:20 am, arrives 3:00 pm."\n'
                   '3. "LX 1612 is cancelled. You are rebooked on LX 1614 ZRH to OTP, departs 1:45 pm, arrives '
                   '5:20 pm."\n'
                   'All times are local: London is 1 hour behind Zurich, and Zurich is 1 hour behind Bucharest '
                   '(OTP).' + ONLY_ARRAY,
                   [{"flight": "LX 318", "from": "LHR", "to": "ZRH", "departs": "07:10", "minutes": 105},
                    {"flight": "LX 1614", "from": "ZRH", "to": "OTP", "departs": "13:45", "minutes": 155}], "hard"),
    ]


STRUCTURED_CASES = structured_cases()


# Context: realistic logs generated from a seed, each answer computed from the generated records.
CONTEXT_NUMBER = JSON_NUMBER
CONTEXT_STRING = JSON_STRING
NOT_FOUND = ' If the log does not contain the answer, the "answer" value must be "not found".'


def context(case_id, intro, records, question, answer, difficulty, *, suffix):
    prompt = f"{intro}\n\n" + "\n".join(records) + f"\n\n{question}{suffix}"
    return Case(f"context-{case_id}", "context", prompt, answer, difficulty=difficulty,
                tolerance=0.005 if isinstance(answer, float) else 0.0)


def clock(rng, start, count, low=3, high=40):
    """`count` increasing timestamps from `start`, a few minutes apart."""
    moments, now = [], start
    for _ in range(count):
        now += timedelta(minutes=rng.randrange(low, high))
        moments.append(now)
    return moments


def stamp(moment):
    return moment.strftime("%Y-%m-%d %H:%M")


NAMES = ("Ana", "Ben", "Cy", "Dana", "Eli", "Fay", "Gus", "Hana", "Ivo", "Jae", "Kim", "Lena", "Moe", "Nia", "Omar",
         "Pia", "Quin", "Rui", "Sam", "Tomas", "Uma", "Vic", "Wen", "Yara", "Zed")
STREETS = ("Mill Lane", "Park Road", "Station Street", "Church Walk", "Harbour View", "Elm Grove", "King Street",
           "Bridge Road", "Orchard Way", "Castle Hill")


def ctx_door_code():
    rng = random.Random("door-code")
    jobs = ["Lift 2 serviced, no faults.", "Stairwell bulb replaced on floor 3.", "Gutter cleared above flat 6.",
            "Fire alarm test, all sounders working.", "Intercom handset in flat 9 replaced.",
            "Bin store door code changed to 2290.", "Car park barrier sensor cleaned.",
            "Bike store door code changed to 4817.", "Window cleaners, all floors.", "Leak under sink in flat 2 fixed.",
            "Garden hedge trimmed.", "Lobby carpet shampooed."]
    moments = clock(rng, datetime(2026, 3, 2, 8), len(jobs), 200, 900)
    records = [f"{stamp(moment)} {job}" for moment, job in zip(moments, jobs)]
    return context("door-code", "Building maintenance log:", records,
                   'What is the current door code for the bike store? The "answer" value must be the code as a '
                   "string.", "4817", "control", suffix=CONTEXT_STRING)


def ctx_parcel_locker():
    rng = random.Random("parcel-locker")
    senders = ["Orchard Books", "Halden Home", "Brisa Air", "Corvel Cycles", "Kew Garden Supplies", "Atlas Shoes"]
    lockers = rng.sample(range(1, 61), len(senders))
    moments = clock(rng, datetime(2026, 5, 11, 9), len(senders), 20, 120)
    records = [f"{stamp(moment)} Parcel from {sender} placed in locker {locker}."
               for moment, sender, locker in zip(moments, senders, lockers)]
    target = senders.index("Corvel Cycles")
    return context("parcel-locker", "Parcel locker notifications:", records,
                   "Which locker holds the parcel from Corvel Cycles?", lockers[target], "control",
                   suffix=CONTEXT_NUMBER)


def ctx_meter_reading():
    rng = random.Random("meter-reading")
    reading, records, answer = 18240, [], None
    for month in range(1, 9):
        reading += rng.randrange(180, 420)
        records.append(f"2026-{month:02d}-01 Electricity meter: {reading} kWh. Gas meter: {reading // 3 + 5021} m3.")
        if month == 5:
            answer = reading
    return context("meter-reading", "Meter readings, taken on the first of each month:", records,
                   "What was the electricity meter reading on 1 May 2026, in kWh?", answer, "control",
                   suffix=CONTEXT_NUMBER)


def server_filler(rng, moment):
    service = rng.choice(("api", "web", "worker", "search", "billing"))
    return rng.choice((
        f"{stamp(moment)} INFO {service}: request rate {rng.randrange(40, 900)}/s, p95 latency "
        f"{rng.randrange(40, 400)} ms",
        f"{stamp(moment)} INFO {service}: cache hit ratio {rng.randrange(70, 99)}%",
        f"{stamp(moment)} WARN {service}: slow query took {rng.randrange(2, 9)}.{rng.randrange(10)} s",
        f"{stamp(moment)} INFO {service}: rotated log file, {rng.randrange(20, 400)} MB archived",
        f"{stamp(moment)} INFO {service}: health check passed on {rng.randrange(2, 9)} instances"))


def ctx_backup_snapshot():
    rng = random.Random("backup-snapshot")
    moments = clock(rng, datetime(2026, 3, 9, 20), 74, 40, 70)
    records, answer = [], None
    for moment in moments:
        if moment.hour == 2 and not any(f"backup {moment:%Y-%m-%d}" in line for line in records):
            snapshot = f"snap-{rng.randrange(16 ** 6):06x}"
            records.append(f"{stamp(moment)} INFO backup {moment:%Y-%m-%d}: nightly backup completed, snapshot "
                           f"{snapshot}, {rng.randrange(40, 60)} GB")
            if moment.date() == date(2026, 3, 11):
                answer = snapshot
        else:
            records.append(server_filler(rng, moment))
    assert answer
    return context("backup-snapshot", "Server log:", records,
                   'Which snapshot did the nightly backup on 11 March 2026 produce? The "answer" value must be the '
                   "snapshot ID as a string.", answer, "challenge", suffix=CONTEXT_STRING)


def ctx_latest_address():
    rng = random.Random("latest-address")
    customers = ["C-1042", "C-1024", "C-1402", "C-0142", "C-2041", "C-1043"]
    moments = clock(rng, datetime(2025, 1, 6, 9), 44, 600, 4000)
    records, current = [], {}
    for index, moment in enumerate(moments):
        customer = customers[index % len(customers)] if index < 38 else rng.choice(customers[1:])
        if index == 30:
            customer = "C-1042"
        address = f"{rng.randrange(1, 120)} {rng.choice(STREETS)}"
        current[customer] = address
        records.append(f"{stamp(moment)} {customer} delivery address set to {address}")
    return context("latest-address", "Customer record changes, oldest first:", records,
                   'What is the current delivery address of customer C-1042? The "answer" value must be the address '
                   "exactly as written in the log.", current["C-1042"], "challenge", suffix=CONTEXT_STRING)


def ctx_ticket_status():
    rng = random.Random("ticket-status")
    tickets = [5531, 5513, 5553, 5351, 5530, 5532]
    flows = {5531: ["opened", "assigned to Ben", "waiting on customer", "resolved", "reopened by customer",
                    "assigned to Lena", "waiting on supplier"]}
    events = []
    for ticket in tickets:
        steps = flows.get(ticket) or ["opened", f"assigned to {rng.choice(NAMES)}",
                                      rng.choice(("resolved", "closed", "waiting on customer"))]
        events += [(ticket, step) for step in steps]
    # Shuffle the tickets together while keeping each ticket's own events in order: the k-th event of a
    # ticket gets a key in [k, k + 1).
    keys, seen = [], {}
    for ticket, _ in events:
        keys.append(seen.get(ticket, 0) + rng.random())
        seen[ticket] = seen.get(ticket, 0) + 1
    order = sorted(range(len(events)), key=keys.__getitem__)
    moments = clock(rng, datetime(2026, 4, 1, 8), len(events), 15, 240)
    records = [f"{stamp(moment)} Ticket #{events[i][0]}: {events[i][1]}" for moment, i in zip(moments, order)]
    last = [events[i][1] for i in order if events[i][0] == 5531][-1]
    return context("ticket-status", "Support ticket events:", records,
                   'What is the current status of ticket #5531? The "answer" value must be the status text exactly as '
                   'written in its latest event, for example "resolved".', last, "challenge", suffix=CONTEXT_STRING)


def ctx_house_expenses():
    rng = random.Random("house-expenses")
    people, categories = ("Sam", "Sami", "Lena", "Omar"), ("groceries", "cleaning supplies", "internet", "takeaway")
    moments = clock(rng, datetime(2026, 6, 1, 10), 40, 300, 1400)
    records, total = [], F(0)
    for index, moment in enumerate(moments):
        person, category = rng.choice(people), rng.choice(categories)
        if index % 8 == 3:
            person, category = "Sam", "groceries"
        amount = F(rng.randrange(300, 9000), 100)
        records.append(f"{moment:%Y-%m-%d} {person} paid ${float(amount):.2f} for {category}")
        if person == "Sam" and category == "groceries":
            total += amount
    return context("house-expenses", "Shared house expenses for June:", records,
                   "How much did Sam pay for groceries in June, in dollars?", money(total), "challenge",
                   suffix=CONTEXT_NUMBER)


def ctx_badge_absent():
    rng = random.Random("badge-absent")
    moments = clock(rng, datetime(2026, 6, 14, 7), 50, 5, 25)
    records = []
    for moment in moments:
        badge = rng.choice(("4417", "4471", "4741", "1447", "7144"))
        room = rng.choice(("main entrance", "server room", "lab 2", "loading bay"))
        if badge == "4471" and room == "server room":
            room = "lab 2"
        records.append(f"{stamp(moment)} badge {badge} entered {room}")
    records.insert(17, f"{stamp(moments[16])} badge 4471 denied at server room (no access)")
    return context("badge-absent", "Door access log:", records,
                   'At what time did badge 4471 first enter the server room on 14 June 2026? The "answer" value must '
                   'be a 24-hour "HH:MM" time.' + NOT_FOUND, "not found", "challenge", suffix=CONTEXT_STRING)


def ctx_billing_deploys():
    rng = random.Random("billing-deploys")
    moments = clock(rng, datetime(2026, 7, 7, 8), 60, 5, 25)
    records, count = [], 0
    for moment in moments:
        service = rng.choice(("billing", "billing-worker", "search", "api", "billing"))
        outcome = rng.choice(("succeeded", "succeeded", "failed", "rolled back"))
        records.append(f"{stamp(moment)} deploy {service} v{rng.randrange(2, 4)}.{rng.randrange(20)}.{rng.randrange(9)} "
                       f"{outcome}")
        count += service == "billing" and outcome == "succeeded"
    return context("billing-deploys", "Deployment log:", records,
                   'How many deploys of the service named exactly "billing" succeeded? Deploys of other services, '
                   "including billing-worker, don't count.", count, "challenge", suffix=CONTEXT_NUMBER)


def ctx_sensor_max():
    rng = random.Random("sensor-max")
    moments = clock(rng, datetime(2026, 8, 2, 0), 70, 10, 30)
    records, best = [], None
    for moment in moments:
        sensor = rng.choice(("GH-2", "GH-12", "GH-20", "GH-3"))
        value = F(rng.randrange(180, 330), 10)
        if sensor == "GH-12" and rng.random() < .3:
            value = F(rng.randrange(330, 360), 10)
        records.append(f"{stamp(moment)} {sensor} temperature {float(value):.1f} C humidity {rng.randrange(40, 90)}%")
        if sensor == "GH-2":
            best = max(best or value, value)
    return context("sensor-max", "Greenhouse sensor log:", records,
                   "What was the highest temperature recorded by sensor GH-2, in degrees Celsius?", float(best),
                   "challenge", suffix=CONTEXT_NUMBER)


def ctx_config_reverts():
    """A revert restores the value its change replaced; each reverted change is still that key's latest."""
    rng = random.Random("config-reverts")
    keys = ("max_connections", "max_connections_per_ip", "pool_size", "timeout_seconds", "log_level")
    values = {"max_connections": 200, "max_connections_per_ip": 20, "pool_size": 10, "timeout_seconds": 30,
              "log_level": "info"}
    history = {}  # change id -> (key, previous value)
    latest = {}   # key -> change id of its latest change
    records = [f"Initial settings: " + ", ".join(f"{key}={value}" for key, value in values.items()) + "."]
    moments = clock(rng, datetime(2026, 9, 1, 9), 64, 60, 600)
    plan = {12: "revert", 27: "revert", 41: "revert", 55: "revert"}
    for number, moment in enumerate(moments, start=1):
        change = f"CH-{number:03d}"
        if plan.get(number) == "revert" and latest.get("max_connections"):
            target = latest["max_connections"]
            key, previous = history[target]
            values[key] = previous
            latest[key] = None
            records.append(f"{stamp(moment)} {change} by {rng.choice(NAMES)}: revert {target}")
            continue
        key = "max_connections" if number % 5 == 0 else rng.choice(keys)
        new = rng.choice(("debug", "info", "warn")) if key == "log_level" else rng.randrange(2, 60) * (
            10 if key == "max_connections" else 1)
        history[change] = (key, values[key])
        values[key], latest[key] = new, change
        records.append(f"{stamp(moment)} {change} by {rng.choice(NAMES)}: set {key}={new}")
    return context("config-reverts", "Configuration change log. A revert undoes the change it names and restores "
                   "the value that setting had just before that change.", records,
                   "What is the current value of max_connections?", values["max_connections"], "hard",
                   suffix=CONTEXT_NUMBER)


def ctx_bike_last_ride():
    rng = random.Random("bike-last-ride")
    stations = ("Quayside", "Market Square", "Uni Library", "Castle Gate", "Riverside Park", "Old Mill", "Station")
    users = {f"U{rng.randrange(1000, 9999)}": f"{name.lower()}{rng.randrange(1, 99)}@example.com"
             for name in rng.sample(NAMES, 8)}
    target_user = list(users)[3]
    target_email = users[target_user]
    lookalike = target_email.replace("@", "1@", 1)
    users[f"U{rng.randrange(1000, 9999)}"] = lookalike
    records = [f"USER {user} email={email}" for user, email in users.items()]
    rides, moments = [], clock(rng, datetime(2026, 5, 4, 7), 52, 5, 50)
    for index, moment in enumerate(moments):
        user = target_user if index % 9 == 2 else rng.choice(list(users))
        start, end = rng.sample(stations, 2)
        rides.append((moment, user, start, end))
    rng.shuffle(rides)
    records += [f"RIDE user={user} start={stamp(moment)} from={start} to={end} bike=B{rng.randrange(100, 999)}"
                for moment, user, start, end in rides]
    answer = max((ride for ride in rides if ride[1] == target_user), key=lambda ride: ride[0])[3]
    return context("bike-last-ride", "Bike-share export. Rides are not in time order.", records,
                   f"At which station did the user with email {target_email} end their most recent ride? The "
                   '"answer" value must be the station name exactly as written.', answer, "hard",
                   suffix=CONTEXT_STRING)


def ctx_shipment_carrier():
    rng = random.Random("shipment-carrier")
    carriers = {f"CR-{index}": name for index, name in
                enumerate(("Parcelwise", "Northline", "QuickDrop", "Atlas Freight", "Swift Courier"), start=1)}
    orders = [8812, 8821, 8128, 8882, 8811, 8813, 8218, 8822, 8112, 8881, 8814, 8281, 1882, 8183]
    records = [f"CARRIER {code} name={name}" for code, name in carriers.items()]
    shipments, number = [], 500
    for order in orders:
        for sequence in range(1, rng.randrange(2, 4) + 1 if order != 8812 else 4):
            number += rng.randrange(1, 9)
            shipments.append((f"SH-{number}", order, sequence, rng.choice(list(carriers))))
    rng.shuffle(shipments)
    records += [f"SHIPMENT {sid} order={order} part={part} carrier={carrier}" for sid, order, part, carrier in shipments]
    answer = carriers[next(carrier for _, order, part, carrier in shipments if order == 8812 and part == 2)]
    rng.shuffle(records)
    return context("shipment-carrier", "Warehouse records (carriers and shipments, in no particular order):", records,
                   "Which carrier is delivering part 2 of order 8812? The \"answer\" value must be the carrier's name "
                   "exactly as written.", answer, "hard", suffix=CONTEXT_STRING)


def ctx_room_bookings():
    rng = random.Random("room-bookings")
    rooms = ("Kew", "Kew Annex", "Holly", "Ash")
    bookings, records = [], []
    for number in range(1, 49):
        room = "Kew" if number % 6 == 0 else rng.choice(rooms)
        day = 3 if number % 6 == 0 else rng.choice((2, 3, 3, 4))
        start = rng.randrange(8, 17) * 60 + rng.choice((0, 15, 30, 45))
        minutes = rng.choice((30, 45, 60, 60, 90, 120))
        bookings.append([f"BK-{number:02d}", room, day, start, minutes, False])
    cancel = rng.sample([b for b in bookings if b[1] == "Kew" and b[2] == 3 and b[4] >= 60], 2)
    for booking in bookings:
        bid, room, day, start, minutes, _ = booking
        records.append(f"BOOKED {bid} room={room} date=2026-04-{day:02d} start={start // 60:02d}:{start % 60:02d} "
                       f"minutes={minutes}")
    for booking in cancel:
        booking[5] = True
        records.insert(rng.randrange(len(records) // 2, len(records)), f"CANCELLED {booking[0]}")
    answer = sum(1 for _, room, day, _, minutes, cancelled in bookings
                 if room == "Kew" and day == 3 and minutes >= 60 and not cancelled)
    return context("room-bookings", "Meeting room booking log:", records,
                   "How many bookings of the room named exactly Kew on 2026-04-03 lasted at least 60 minutes and were "
                   "not cancelled?", answer, "hard", suffix=CONTEXT_NUMBER)


def ctx_night_pages():
    rng = random.Random("night-pages")
    moments = clock(rng, datetime(2026, 10, 20, 22), 64, 3, 12)
    records, count = [], 0
    for moment in moments:
        service = rng.choice(("payments", "payments-api", "search", "payments", "email"))
        auto = rng.random() < .35
        records.append(f"{stamp(moment)} PAGE service={service} severity={rng.choice(('high', 'low'))}"
                       + (" auto-resolved after 2 min" if auto else " acknowledged"))
        count += service == "payments" and not auto and 2 <= moment.hour < 6
    return context("night-pages", "On-call paging log for the night of 20 to 21 October 2026:", records,
                   'How many pages for the service named exactly "payments" between 02:00 and 06:00 (02:00 included, '
                   "06:00 not) were not auto-resolved?", count, "hard", suffix=CONTEXT_NUMBER)


def reports_to(manager, person, boss):
    """Whether `person` is `boss` or sits anywhere below `boss`; used to keep the org chart free of loops."""
    while person is not None:
        if person == boss:
            return True
        person = manager.get(person)
    return False


def ctx_manager_chain():
    rng = random.Random("manager-chain")
    people = list(NAMES)
    manager = {}
    records = []
    moments = clock(rng, datetime(2025, 1, 6, 9), 46, 1500, 9000)
    for index, moment in enumerate(moments):
        person = people[index % len(people)] if index < len(people) else rng.choice(people[1:])
        if person == people[0]:
            records.append(f"{moment:%Y-%m-%d} {person} joins as CEO")
            continue
        boss = rng.choice([p for p in people[:index % len(people) or 1] if p != person] or [people[0]])
        if index >= len(people) and person in manager:
            boss = rng.choice([p for p in manager if not reports_to(manager, p, person)] + [people[0]])
            records.append(f"{moment:%Y-%m-%d} {person} now reports to {boss}")
        else:
            records.append(f"{moment:%Y-%m-%d} {person} joins, reporting to {boss}")
        manager[person] = boss
    answer = manager[manager["Dana"]]
    return context("manager-chain", "HR reporting-line changes, oldest first:", records,
                   "Who is Dana's manager's manager now?", answer, "hard", suffix=CONTEXT_STRING)


def ctx_stock_transfers():
    rng = random.Random("stock-transfers")
    sites, skus = ("Leeds", "York", "Hull"), ("LMP-20", "LMP-200", "LMP-02", "DSK-20")
    stock = {(site, sku): rng.randrange(20, 80) for site in sites for sku in skus}
    records = [f"OPENING {site} {sku} {stock[site, sku]} units" for site in sites for sku in skus]
    moments = clock(rng, datetime(2026, 11, 2, 8), 50, 20, 200)
    transfers = {}
    for number, moment in enumerate(moments, start=1):
        sku = "LMP-20" if number % 3 == 0 else rng.choice(skus)
        kind = rng.choice(("SOLD", "RECEIVED", "TRANSFER", "TRANSFER"))
        quantity = rng.randrange(1, 12)
        if number == 34:
            source, target, sku, quantity = transfers["TR-21"]
            stock[source, sku] += quantity
            stock[target, sku] -= quantity
            records.append(f"{stamp(moment)} TR-21 CANCELLED, stock returned to {source}")
        elif kind == "TRANSFER" or number == 21:
            source, target = ("York", "Leeds") if number == 21 else rng.sample(sites, 2)
            sku = "LMP-20" if number == 21 else sku
            quantity = min(quantity, stock[source, sku])
            stock[source, sku] -= quantity
            stock[target, sku] += quantity
            transfers[f"TR-{number}"] = (source, target, sku, quantity)
            records.append(f"{stamp(moment)} TR-{number} TRANSFER {quantity} {sku} from {source} to {target}")
        else:
            site = rng.choice(sites)
            if kind == "SOLD":
                quantity = min(quantity, stock[site, sku])
            stock[site, sku] += quantity if kind == "RECEIVED" else -quantity
            records.append(f"{stamp(moment)} {kind} {quantity} {sku} at {site}")
    assert "TR-21" in transfers and min(stock.values()) >= 0
    return context("stock-transfers", "Stock movements. A cancelled transfer is undone in full.", records,
                   "How many units of LMP-20 are at Leeds now?", stock["Leeds", "LMP-20"], "hard",
                   suffix=CONTEXT_NUMBER)


def ctx_holds_queue():
    rng = random.Random("holds-queue")
    titles = ("The Salt Road", "Salt and Stone", "The Salt Road: A Guide", "North Wind")
    queues = {title: [] for title in titles}
    records = []
    moments = clock(rng, datetime(2026, 2, 2, 9), 48, 30, 400)
    for index, moment in enumerate(moments):
        title = "The Salt Road" if index % 3 == 0 else rng.choice(titles)
        queue = queues[title]
        action = rng.choice(("placed", "placed", "cancelled", "collected")) if queue else "placed"
        if action == "placed":
            member = rng.choice([name for name in NAMES if name not in queue])
            queue.append(member)
            records.append(f"{stamp(moment)} hold placed on \"{title}\" by {member}")
        elif action == "cancelled":
            member = rng.choice(queue)
            queue.remove(member)
            records.append(f"{stamp(moment)} hold on \"{title}\" cancelled by {member}")
        else:
            member = queue.pop(0)
            records.append(f"{stamp(moment)} \"{title}\" collected by {member}, who was first in the queue")
    assert queues["The Salt Road"]
    return context("holds-queue", "Library holds log. Holds are served in the order they were placed.", records,
                   'Who is now first in the queue for "The Salt Road"?', queues["The Salt Road"][0], "hard",
                   suffix=CONTEXT_STRING)


def ctx_settled_balance():
    rng = random.Random("settled-balance")
    balance = F(484250, 100)
    records = [f"2026-08-01 OPENING settled balance ${float(balance):,.2f}"]
    transactions = {}
    moments = clock(rng, datetime(2026, 8, 1, 9), 48, 200, 900)
    for number, moment in enumerate(moments, start=1):
        tid = f"TX{number:03d}"
        kind = rng.choice(("card", "card", "transfer in", "direct debit", "pending card", "reversal"))
        done = [t for t, (amount, state) in transactions.items() if state == "settled"]
        if kind == "reversal" and not done:
            kind = "card"
        if kind == "reversal":
            target = rng.choice(done)
            amount, _ = transactions[target]
            transactions[target] = (amount, "reversed")
            balance -= amount
            records.append(f"{moment:%Y-%m-%d} {tid} REVERSAL of {target}")
            continue
        value = F(rng.randrange(250, 40000), 100)
        amount = value if kind == "transfer in" else -value
        state = "pending" if kind == "pending card" else "settled"
        transactions[tid] = (amount, state)
        if state == "settled":
            balance += amount
        records.append(f"{moment:%Y-%m-%d} {tid} {kind.replace('pending ', '')} {'+' if amount > 0 else '-'}"
                       f"${float(abs(value)):,.2f} {state.upper()}")
    return context("settled-balance", "Bank account activity for August. Pending transactions are not settled yet, "
                   "and a reversal cancels the transaction it names.", records,
                   "What is the settled balance now, in dollars?", money(balance), "hard", suffix=CONTEXT_NUMBER)


def ctx_contract_approvals():
    rng = random.Random("contract-approvals")
    contracts = ("MSA-7", "MSA-17", "MSA-71", "NDA-7")
    records, approved = [], {}
    moments = clock(rng, datetime(2026, 3, 2, 9), 56, 60, 900)
    versions = {contract: 0 for contract in contracts}
    pending = []
    for index, moment in enumerate(moments):
        contract = "MSA-7" if index % 4 == 1 else rng.choice(contracts)
        action = rng.choice(("draft", "draft", "approve", "reject")) if pending else "draft"
        if action == "draft":
            versions[contract] += 1
            fee = rng.randrange(20, 90) * 500
            pending.append((contract, versions[contract], fee))
            records.append(f"{stamp(moment)} {contract} version {versions[contract]} drafted: annual fee "
                           f"${fee:,}")
        else:
            contract, version, fee = pending.pop(rng.randrange(len(pending)))
            records.append(f"{stamp(moment)} {contract} version {version} {action}{'d' if action == 'approve' else 'ed'} by legal")
            if action == "approve" and version > approved.get(contract, (0, 0))[0]:
                approved[contract] = (version, fee)
    return context("contract-approvals", "Contract workflow log. Versions are not approved in order, and a draft "
                   "that is rejected or still waiting does not count.", records,
                   "What is the annual fee, in dollars, in the highest-numbered approved version of contract MSA-7?",
                   approved["MSA-7"][1], "hard", suffix=CONTEXT_NUMBER)


def context_cases():
    return [ctx_door_code(), ctx_parcel_locker(), ctx_meter_reading(),
            ctx_backup_snapshot(), ctx_latest_address(), ctx_ticket_status(), ctx_house_expenses(),
            ctx_badge_absent(), ctx_billing_deploys(), ctx_sensor_max(),
            ctx_config_reverts(), ctx_bike_last_ride(), ctx_shipment_carrier(), ctx_room_bookings(),
            ctx_night_pages(), ctx_manager_chain(), ctx_stock_transfers(), ctx_holds_queue(), ctx_settled_balance(),
            ctx_contract_approvals()]


CONTEXT_CASES = context_cases()
