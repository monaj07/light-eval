"""Suite 1.8 cases written so far (docs/SUITE_1_8_DESIGN.md), staged outside the live suite.

These cases are not part of `cases.dataset()` yet: adding them there would change suite 1.7,
whose published results must stay comparable. When every 1.8 track is written and calibrated,
they move into `cases.py` and `VERSION` becomes 1.8.0. Images live in fixtures/v1_8/, which the
runner's fixture hashing (fixtures/*.png) does not include while 1.7 is current.

Vision: ten photographs taken by the repository author (fixtures/v1_8/photos/, prepared by
fixtures/prepare_photos.py) and ten drawings (fixtures/suite_1_8_fixtures.py, whose docstrings
work out each answer). Every question needs reading plus reasoning: counting among distractors,
following a diagram, combining values, or rejecting a near-identical decoy. Only two questions
are multiple choice, where an open answer would be ambiguous.
"""
from cases import Case

JSON_NUMBER = ' Return only a JSON object with one key "answer" containing the numerical answer.'
JSON_STRING = ' Return only a JSON object with one key "answer" containing the answer as a string.'
JSON_LETTER = (' Return only a JSON object with one key "answer" whose value is only the letter of the '
               'correct option, as a string.')


def photo(case_id, file, prompt, answer, difficulty, **extra):
    return Case(f"vision-{case_id}", "vision", prompt, answer, f"v1_8/photos/{file}", difficulty=difficulty, **extra)


def drawing(case_id, file, prompt, answer, difficulty, **extra):
    return Case(f"vision-{case_id}", "vision", prompt, answer, f"v1_8/{file}", difficulty=difficulty, **extra)


def vision_cases():
    return [
        # Controls: one clear fact from a real photo.
        photo("fruit-tree", "fruit-tree.jpg", "What fruit is growing on the tree under the net? The \"answer\" "
              "value must be only the fruit's name, in lowercase." + JSON_STRING, "pomegranate", "control"),
        photo("kangaroos", "kangaroos.jpg", "How many kangaroos are in this photo?" + JSON_NUMBER, 7, "control"),
        photo("selfie-jacket", "welcome-sign.jpg", "The welcome sign includes a photo of three people. What colour "
              "is the jacket of the person taking the selfie? A) Brown B) Red C) White D) Blue." + JSON_LETTER,
              "A", "control"),
        # Challenges.
        photo("penguin-band", "penguins.jpg", "Each bird in this photo wears a coloured band on one flipper. How "
              "many birds are to the right of the bird with the white band?" + JSON_NUMBER, 2, "challenge"),
        photo("salmon-cost", "supermarket.jpg", "In this supermarket, how much would 600 g of skin-on salmon cost, "
              "in dollars?" + JSON_NUMBER, 17.7, "challenge", tolerance=.005),
        photo("ice-cream-order", "ice-cream-menu.jpg", "At this ice-cream shop I buy 3 scoops in a cup, and my two "
              "children each get 1 scoop in a regular cone. What is the total cost in dollars?" + JSON_NUMBER,
              19.0, "challenge", tolerance=.005),
        photo("tv-model-line", "television.jpg", "What two words are printed just under the brand name on this "
              "television? Give them exactly as printed, with the same capital letters." + JSON_STRING,
              "Sensor Touch", "challenge"),
        drawing("visitor-months", "visitors-lines.png", "In how many months were there more park visitors in 2026 "
                "than in the same month of 2025?" + JSON_NUMBER, 7, "challenge"),
        drawing("next-train", "clock-departures.png", "The clock shows the time now. In how many minutes does the "
                "next train to Northport that will actually run leave?" + JSON_NUMBER, 40, "challenge"),
        drawing("tracking-number", "parcel-label.png", "What is the tracking number on this parcel label? Give it "
                "exactly as printed." + JSON_STRING, "TRK-58K2-9940", "challenge"),
        # Hard: several reading steps, a decoy, or a calculation on what was read.
        photo("gpio-pin-1", "pi-gpio.jpg", "Which jumper wire is connected closest to physical GPIO pin 1 of "
              "this Raspberry Pi? A) Orange B) Red C) Green D) Brown." + JSON_LETTER, "D", "hard"),
        photo("map-cottage", "park-map.jpg", "According to this map, on which road is cottage number 23? The "
              '"answer" value must be only the road name, in title case with the abbreviation as on the map, '
              'for example "Example Rd".' + JSON_STRING, "Scott Rd", "hard"),
        # On the receipt each price sits about half a line below its item, so pairing names with the
        # price on the same line gives 37.56; reading the layout (9 items, 9 prices, in order) is the task.
        photo("receipt-split", "restaurant-receipt.jpg", "This is our restaurant receipt. I had the Corn Cheese and "
              "the Half Original Boneless. We share the credit card surcharge in proportion to what each person "
              "ordered. How much do I owe in dollars, including my share of the surcharge, rounded to two decimal "
              "places?" + JSON_NUMBER, 38.57, "hard", tolerance=.005),
        drawing("promo-items", "grocery-receipt.png", "How much was paid for the items that had a promotion, after "
                "their promotional savings?" + JSON_NUMBER, 29.0, "hard", tolerance=.005),
        drawing("irrigation-flow", "flowchart.png", "Follow this irrigation flowchart with these readings: soil "
                "moisture 24%, no rain forecast in the next 6 hours, tank level 15%, temperature 38 °C. Which "
                'box does it end in? The "answer" value must be only the box letter.' + JSON_STRING, "D", "hard"),
        drawing("floor-route", "floor-plan.png", "Moving only through doorways, what is the smallest number of rooms "
                "you must pass through to get from the Entrance hall to the Study, not counting the Entrance hall "
                "or the Study?" + JSON_NUMBER, 4, "hard"),
        drawing("net-sales-change", "store-table.png", "A store's net result is its sales minus its returns. Which "
                'store\'s net result increased the most from Q1 to Q2? The "answer" value must be only the store '
                "name." + JSON_STRING, "Harbour", "hard"),
        drawing("heating-off", "thermostat-dashboard.png", "How many rooms are more than 1.5 °C below the target "
                "temperature and have their heating switched off?" + JSON_NUMBER, 3, "hard"),
        drawing("solar-share", "energy-stacked.png", "In which month did solar supply the largest share of that "
                'month\'s total electricity? The "answer" value must be only the month as written on the chart.'
                + JSON_STRING, "Feb", "hard"),
        drawing("saturday-bus", "bus-timetable.png", "It is Saturday and you reach the Park Street stop at 14:10. "
                "If you take the first bus you can, at what time do you arrive at Central Station? The "
                '"answer" value must be a 24-hour "HH:MM" time.' + JSON_STRING, "14:55", "hard"),
    ]
