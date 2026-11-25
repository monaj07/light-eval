"""Suite 1.8 instructions track (docs/SUITE_1_8_DESIGN.md), staged outside the live suite like suite_1_8.py.

Each case is a realistic writing task with constraints that a rule can check, in the style of IFEval. A
case passes only if every check passes. Every check corresponds to a rule stated in the prompt, with the
terms the rule depends on (a bullet, a word, a paragraph) defined there, so no hidden convention decides
the score. With thinking on, only the final reply is checked, never the reasoning.

Each case carries a `sample` reply that passes every check, and `misses`: plausible replies paired with
the exact set of checks they must fail. tests/test_suite_1_8_instructions.py runs both, so every checker
is shown to accept a right reply and to reject the mistake it exists for.
"""
import json
import re
from dataclasses import dataclass

from cases import parse_json, strip_code_fence

BULLET_RULE = "A bullet is a line that starts with '- '."
WORD_RULE = "Words are counted as the pieces of text separated by spaces."


@dataclass(frozen=True)
class Check:
    name: str
    test: object  # callable(reply text, stripped) -> bool


@dataclass(frozen=True)
class InstructionCase:
    id: str
    prompt: str
    checks: tuple
    difficulty: str
    sample: str
    misses: tuple = ()  # (reply, frozenset of check names it fails)
    json: bool = False  # the reply must be one JSON object (a surrounding code fence is tolerated)
    track: str = "instructions"


# Text helpers. Lines keep their order; trailing spaces never count against a reply.
def lines(text):
    return [line.rstrip() for line in text.split("\n")]


def nonblank(text):
    return [line for line in lines(text) if line.strip()]


def blocks(text):
    """Paragraphs: runs of non-blank lines separated by one or more blank lines."""
    result, current = [], []
    for line in lines(text):
        if line.strip():
            current.append(line)
        elif current:
            result.append(current)
            current = []
    if current:
        result.append(current)
    return result


def bullets(text):
    return [line[2:] for line in nonblank(text) if line.startswith("- ")]


def only_bullets(text, count):
    rows = nonblank(text)
    return len(rows) == count and all(line.startswith("- ") and line[2:].strip() for line in rows)


def word_count(text):
    return len(text.split())


def count_word(text, word):
    return len(re.findall(rf"(?<![A-Za-z]){re.escape(word)}(?![A-Za-z])", text, re.IGNORECASE))


def table_rows(text):
    return [[cell.strip() for cell in line.strip().strip("|").split("|")] for line in nonblank(text)]


def json_object(text):
    return parse_json(strip_code_fence(text))


def json_keys(text):
    """The keys of the outermost JSON object, in the order written (inner objects are built first)."""
    built = []
    json.loads(strip_code_fence(text).strip(),
               object_pairs_hook=lambda pairs: built.append([key for key, _ in pairs]) or dict(pairs))
    return built[-1] if built else None


def messages(text):
    return re.split(r"\n---\n", "\n".join(lines(text)))


def failed_checks(case, content):
    """The names of the checks a reply fails, in the case's order."""
    text = content.strip()
    failed = []
    for check in case.checks:
        try:
            ok = bool(check.test(text))
        except (ValueError, TypeError, KeyError, IndexError, AttributeError, RecursionError):
            ok = False
        if not ok:
            failed.append(check.name)
    return failed


def score_instructions(case, content):
    if case.json:
        try:
            json_object(content)
        except (ValueError, RecursionError):
            return "invalid_json"
    return "pass" if not failed_checks(case, content) else "failed_constraint"


def scaled_amount(value):
    return f"{value:.2f}".rstrip("0").rstrip(".")


RECIPE = (("300", "g", "flour"), ("250", "ml", "milk"), ("0.5", "tsp", "salt"), ("30", "g", "butter"),
          ("1.5", "tbsp", "sugar"))
SCALED = tuple(f"- {scaled_amount(float(amount) * 6 / 4)} {unit} {name}" for amount, unit, name in RECIPE)
GROCERIES = ("cumin", "eggs", "garlic", "olive oil", "onion", "red pepper", "tinned tomatoes")
WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday")


def instruction_cases():
    C = Check
    return [
        # Controls: one constraint on a short, real piece of writing.
        InstructionCase(
            "instr-sms-reminder",
            "Write a text message reminding Leo of a dental appointment with Dr Hale on Tuesday 14 May at 10:30. "
            "The whole message must be at most 160 characters, counting spaces and punctuation, and must "
            "include the time as 10:30 and the name Dr Hale. Reply with only the message.",
            (C("at most 160 characters", lambda t: 0 < len(t) <= 160),
             C("includes 10:30 and Dr Hale", lambda t: "10:30" in t and "Dr Hale" in t)),
            "control",
            "Hi Leo, a reminder of your dental appointment with Dr Hale on Tuesday 14 May at 10:30. Reply YES to "
            "confirm or call us to rearrange.",
            misses=(("Hi Leo! This is a friendly reminder from the practice that you have a dental appointment "
                     "booked with Dr Hale on Tuesday 14 May at 10:30. Please reply YES to confirm, or call us on "
                     "the usual number if you need to rearrange.", frozenset({"at most 160 characters"})),)),
        InstructionCase(
            "instr-hike-list",
            "List exactly 5 things to pack for a day hike. Reply with only a bullet list, one item per bullet and "
            "no other text. " + BULLET_RULE,
            (C("exactly 5 bullets and nothing else", lambda t: only_bullets(t, 5)),),
            "control",
            "- Water\n- Rain jacket\n- Map\n- Snacks\n- Sunscreen",
            misses=(("Here is a list:\n- Water\n- Rain jacket\n- Map\n- Snacks\n- Sunscreen",
                     frozenset({"exactly 5 bullets and nothing else"})),
                    ("- Water\n- Rain jacket\n- Map\n- Snacks\n- Sunscreen\n- First-aid kit",
                     frozenset({"exactly 5 bullets and nothing else"})))),
        InstructionCase(
            "instr-lowercase-caption",
            "Write a short caption for a travel blog photo of a lighthouse at sunset. Write the whole caption in "
            "lowercase, with no capital letters at all, and use the word 'lighthouse'. Reply with only the caption.",
            (C("no capital letters", lambda t: t == t.lower() and any(ch.isalpha() for ch in t)),
             C("mentions the lighthouse", lambda t: count_word(t, "lighthouse") > 0)),
            "control",
            "the last light of the day catches the old lighthouse on the headland.",
            misses=(("The last light of the day catches the old lighthouse.", frozenset({"no capital letters"})),)),

        # Challenges: two or three constraints.
        InstructionCase(
            "instr-commit-message",
            "Write a git commit message for this change: the app crashed on start-up when the config file was empty; "
            "it now falls back to the default settings and logs a warning. Format: a summary line of at most 50 "
            "characters, then one blank line, then a body of at least one line in which no line is longer than 72 "
            "characters. Reply with only the commit message.",
            (C("summary at most 50 characters", lambda t: 0 < len(lines(t)[0]) <= 50),
             C("blank second line", lambda t: lines(t)[1] == ""),
             C("body present", lambda t: any(line.strip() for line in lines(t)[2:])),
             C("body lines at most 72 characters", lambda t: all(len(line) <= 72 for line in lines(t)[2:]))),
            "challenge",
            "Fall back to default settings on empty config\n\nThe app crashed on start-up when the config file was "
            "empty. It now uses\nthe default settings and logs a warning instead.",
            misses=(("Fix crash on start-up when the configuration file is empty\n\nNow falls back to defaults.",
                     frozenset({"summary at most 50 characters"})),
                    ("Fall back to defaults on empty config\nThe app now logs a warning.",
                     frozenset({"blank second line", "body present"})),
                    ("Fall back to defaults on empty config\n\nThe app crashed on start-up when the config file was "
                     "empty, and it now uses the default settings and logs a warning.",
                     frozenset({"body lines at most 72 characters"})))),
        InstructionCase(
            "instr-alt-text",
            "Write alt text for a bar chart showing monthly rainfall in Perth, which peaks in June at 180 mm. Rules: "
            "at most 125 characters, on a single line, include the value as '180 mm', and do not begin with "
            "'Image of', 'Picture of' or 'Photo of' (in any capitalisation). Reply with only the alt text.",
            (C("at most 125 characters", lambda t: 0 < len(t) <= 125),
             C("single line", lambda t: "\n" not in t),
             C("includes 180 mm", lambda t: "180 mm" in t),
             C("no 'Image of' opening", lambda t: not re.match(r"(image|picture|photo) of\b", t, re.IGNORECASE))),
            "challenge",
            "Bar chart of monthly rainfall in Perth, rising from summer lows to a June peak of 180 mm.",
            misses=(("Image of a bar chart: Perth's monthly rainfall peaks in June at 180 mm.",
                     frozenset({"no 'Image of' opening"})),
                    ("Bar chart of monthly rainfall in Perth, rising to a June peak of 180mm.",
                     frozenset({"includes 180 mm"})))),
        InstructionCase(
            "instr-shopping-list",
            "Turn this recipe into a shopping list. Recipe: 'Shakshuka: warm the olive oil, soften the onion and the "
            "red pepper, add the garlic, the cumin and the tinned tomatoes, then crack in the eggs.' Reply with only "
            "a bullet list with one ingredient per bullet: write each ingredient's full name exactly as in the recipe, "
            "every word except 'the', in lowercase, with no quantities, and sort the bullets alphabetically. " + BULLET_RULE,
            (C("only bullets", lambda t: only_bullets(t, len(nonblank(t)))),
             C("every ingredient once", lambda t: sorted(bullets(t)) == sorted(GROCERIES)),
             C("alphabetical order", lambda t: bullets(t) == sorted(bullets(t)))),
            "challenge",
            "\n".join(f"- {item}" for item in GROCERIES),
            misses=(("- olive oil\n- onion\n- red pepper\n- garlic\n- cumin\n- tinned tomatoes\n- eggs",
                     frozenset({"alphabetical order"})),
                    ("- cumin\n- eggs\n- garlic\n- olive oil\n- onion\n- red pepper\n- tomatoes",
                     frozenset({"every ingredient once"})))),
        InstructionCase(
            "instr-out-of-office",
            "Write an out-of-office email reply for me. I am away until 3 June; for anything urgent, people should "
            "contact Sam Ortiz at sam.ortiz@example.com. Rules: exactly three paragraphs, counting any greeting and "
            "sign-off as part of the paragraphs, with paragraphs separated by a blank line; include the date as "
            "'3 June' and the email address exactly; and use no exclamation marks. Reply with only the email text.",
            (C("exactly three paragraphs", lambda t: len(blocks(t)) == 3),
             C("includes 3 June", lambda t: "3 June" in t),
             C("includes the address", lambda t: "sam.ortiz@example.com" in t),
             C("no exclamation marks", lambda t: "!" not in t)),
            "challenge",
            "Thank you for your email. I am out of the office until 3 June and will reply when I am back.\n\n"
            "For anything urgent, please contact Sam Ortiz at sam.ortiz@example.com.\n\nBest regards,\nAlex",
            misses=(("Hello,\n\nThank you for your email. I am away until 3 June.\n\nFor anything urgent, please "
                     "contact Sam Ortiz at sam.ortiz@example.com.\n\nBest regards,\nAlex",
                     frozenset({"exactly three paragraphs"})),
                    ("Thanks for your email! I am away until 3 June.\n\nFor anything urgent, please contact Sam "
                     "Ortiz at sam.ortiz@example.com.\n\nBest regards,\nAlex", frozenset({"no exclamation marks"})))),
        InstructionCase(
            "instr-meeting-agenda",
            "Write the agenda for a 30-minute team meeting about moving the office to a new building. Use exactly "
            "these three headings, each on its own line and in this order: '## Goals', '## Updates', '## Decisions'. "
            "Under each heading put at least one bullet, and write nothing else: no title, no other headings and no "
            "text outside the bullets. Blank lines are allowed. " + BULLET_RULE,
            (C("the three headings in order", lambda t: [line for line in nonblank(t) if line.startswith("#")]
               == ["## Goals", "## Updates", "## Decisions"]),
             C("starts with ## Goals", lambda t: nonblank(t)[0] == "## Goals"),
             C("a bullet under each heading", lambda t: all(
                 nonblank(t)[i + 1].startswith("- ") for i, line in enumerate(nonblank(t)) if line.startswith("#"))),
             C("only headings and bullets", lambda t: all(line.startswith(("## ", "- ")) for line in nonblank(t)))),
            "challenge",
            "## Goals\n- Agree the moving date\n- Assign packing owners\n\n## Updates\n- Lease signed for the new "
            "building\n- Movers booked for two quotes\n\n## Decisions\n- Choose the moving company",
            misses=(("# Office move agenda\n\n## Goals\n- Agree the moving date\n\n## Updates\n- Lease signed\n\n"
                     "## Decisions\n- Choose the movers", frozenset({"the three headings in order", "starts with ## Goals",
                                                                    "a bullet under each heading",
                                                                    "only headings and bullets"})),
                    ("## Goals\n- Agree the moving date\n\n## Updates\nThe lease is signed.\n\n## Decisions\n"
                     "- Choose the movers", frozenset({"a bullet under each heading", "only headings and bullets"})))),
        InstructionCase(
            "instr-listing-json",
            "Turn this shop listing into a JSON object: 'Trail runner backpack, 12 litres, £34.99, tags: running, "
            "hydration'. The object must have exactly these keys, in this order: \"name\", \"capacity_litres\", "
            "\"price_pence\", \"tags\". \"name\" is the product name as a string, \"capacity_litres\" and "
            "\"price_pence\" are integers (the price in pence), and \"tags\" is an array of the tags as strings, in "
            "the order given. Reply with only the JSON object.",
            (C("keys in order", lambda t: json_keys(t) == ["name", "capacity_litres", "price_pence", "tags"]),
             C("name is a string", lambda t: isinstance(json_object(t)["name"], str) and json_object(t)["name"].strip()),
             C("capacity 12", lambda t: type(json_object(t)["capacity_litres"]) is int
               and json_object(t)["capacity_litres"] == 12),
             C("price 3499 pence", lambda t: type(json_object(t)["price_pence"]) is int
               and json_object(t)["price_pence"] == 3499),
             C("tags in order", lambda t: json_object(t)["tags"] == ["running", "hydration"])),
            "challenge",
            '{"name": "Trail runner backpack", "capacity_litres": 12, "price_pence": 3499, '
            '"tags": ["running", "hydration"]}',
            json=True,
            misses=(('{"name": "Trail runner backpack", "price_pence": 3499, "capacity_litres": 12, '
                     '"tags": ["running", "hydration"]}', frozenset({"keys in order"})),
                    ('{"name": "Trail runner backpack", "capacity_litres": 12, "price_pence": 34.99, '
                     '"tags": ["running", "hydration"]}', frozenset({"price 3499 pence"})))),
        InstructionCase(
            "instr-library-post",
            "Write a post for a public library's social media account announcing that the library will open on "
            "Sundays from 5 October, 10:00 to 16:00. Rules: at most 280 characters, exactly two hashtags (a hashtag "
            "is '#' followed directly by letters or digits), and no links or web addresses. Reply with only the post.",
            (C("at most 280 characters", lambda t: 0 < len(t) <= 280),
             C("exactly two hashtags", lambda t: len(re.findall(r"#[A-Za-z0-9]+", t)) == 2),
             C("no links", lambda t: not re.search(r"https?://|www\.|\.(com|org|net|gov|uk)\b", t, re.IGNORECASE))),
            "challenge",
            "Good news: from 5 October the library opens on Sundays, 10:00 to 16:00. Come and browse, study or "
            "borrow a book. #SundayOpening #LoveYourLibrary",
            misses=(("From 5 October we open on Sundays, 10:00 to 16:00. Details at www.citylibrary.org "
                     "#SundayOpening #Library", frozenset({"no links"})),
                    ("From 5 October we open on Sundays, 10:00 to 16:00. #SundayOpening #Library #Reading",
                     frozenset({"exactly two hashtags"})))),

        # Hard: four or five constraints that interact.
        InstructionCase(
            "instr-zero-waste",
            "Give three tips for setting up a zero-waste kitchen. Rules: exactly three bullets; each bullet has "
            "fewer than 12 words, not counting the '- '; do not use the letter 'z' anywhere in your reply, in either case; and after the "
            "bullets, end with a final line containing only 'Done.'. Write no other text. " + BULLET_RULE + " "
            + WORD_RULE,
            (C("three bullets then Done.", lambda t: len(nonblank(t)) == 4 and only_bullets("\n".join(nonblank(t)[:3]), 3)
               and nonblank(t)[3] == "Done."),
             C("bullets under 12 words", lambda t: all(word_count(item) < 12 for item in bullets(t))),
             C("no letter z", lambda t: "z" not in t.lower())),
            "hard",
            "- Keep glass jars for buying dry food in bulk.\n- Compost vegetable peels and coffee grounds.\n"
            "- Swap paper towels for washable cloths.\nDone.",
            misses=(("- Buy loose food in bulk for a zero-waste pantry.\n- Compost vegetable peels.\n"
                     "- Use washable cloths.\nDone.", frozenset({"no letter z"})),
                    ("- Keep glass jars for buying dry food in bulk.\n- Compost vegetable peels.\n"
                     "- Use washable cloths.", frozenset({"three bullets then Done."})),
                    ("- Keep glass jars so that you can buy dry food like rice and oats in bulk.\n"
                     "- Compost vegetable peels.\n- Use washable cloths.\nDone.",
                     frozenset({"bullets under 12 words"})))),
        InstructionCase(
            "instr-study-table",
            "Plan a week of study for a driving theory test as a Markdown table. The first row is the header "
            "'| Day | Task | Hours |', the second row is a separator row of dashes, and then there is one row for "
            "each weekday from Monday to Friday, in order, with the day's full name. Each Task is at most 4 words, "
            "each Hours value is a whole number from 1 to 6, and the five Hours values add up to exactly 17. Reply "
            "with only the table. " + WORD_RULE,
            (C("header row", lambda t: table_rows(t)[0] == ["Day", "Task", "Hours"]),
             C("separator row", lambda t: all(re.fullmatch(r":?-{3,}:?", cell) for cell in table_rows(t)[1])
               and len(table_rows(t)[1]) == 3),
             C("Monday to Friday in order", lambda t: len(table_rows(t)) == 7
               and [row[0] for row in table_rows(t)[2:]] == list(WEEKDAYS)),
             C("tasks at most 4 words", lambda t: all(len(row) == 3 and 0 < word_count(row[1]) <= 4
                                                      for row in table_rows(t)[2:])),
             C("hours 1 to 6 adding to 17", lambda t: all(re.fullmatch(r"[1-6]", row[2]) for row in table_rows(t)[2:])
               and sum(int(row[2]) for row in table_rows(t)[2:]) == 17)),
            "hard",
            "| Day | Task | Hours |\n| --- | --- | --- |\n| Monday | Road signs | 3 |\n| Tuesday | Rules of the road "
            "| 4 |\n| Wednesday | Hazard perception practice | 3 |\n| Thursday | Mock test | 4 |\n| Friday | Review "
            "mistakes | 3 |",
            misses=(("| Day | Task | Hours |\n| --- | --- | --- |\n| Monday | Road signs | 3 |\n| Tuesday | Rules "
                     "| 4 |\n| Wednesday | Hazard perception | 4 |\n| Thursday | Mock test | 4 |\n| Friday | Review | 3 |",
                     frozenset({"hours 1 to 6 adding to 17"})),
                    ("| Day | Task | Hours |\n| --- | --- | --- |\n| Mon | Road signs | 3 |\n| Tue | Rules | 4 |\n"
                     "| Wed | Hazards | 3 |\n| Thu | Mock test | 4 |\n| Fri | Review | 3 |",
                     frozenset({"Monday to Friday in order"})),
                    ("| Day | Task | Hours |\n| --- | --- | --- |\n| Monday | Learn all the road signs | 3 |\n"
                     "| Tuesday | Rules | 4 |\n| Wednesday | Hazards | 3 |\n| Thursday | Mock test | 4 |\n"
                     "| Friday | Review | 3 |", frozenset({"tasks at most 4 words"})))),
        InstructionCase(
            "instr-jacket-description",
            "Write a product description for a hiking jacket that costs £89. Rules: between 40 and 60 words; use "
            "the word 'waterproof' exactly twice; never use the word 'very' (whole words only, so 'waterproofing' and "
            "'every' don't count); include the price as '£89'; and end the "
            "description with a question, so that its last character is '?'. Reply with only the description. " + WORD_RULE,
            (C("40 to 60 words", lambda t: 40 <= word_count(t) <= 60),
             C("waterproof exactly twice", lambda t: count_word(t, "waterproof") == 2),
             C("no 'very'", lambda t: count_word(t, "very") == 0),
             C("includes £89", lambda t: "£89" in t),
             C("ends with a question", lambda t: t.endswith("?"))),
            "hard",
            "Meet the Ridgeline, a light hiking jacket built for changeable mountain weather. Its waterproof shell "
            "and taped seams keep rain out, while underarm vents let heat escape on long climbs. The hood fits over "
            "a helmet and packs into the collar. Fully waterproof and only £89, why hike without it?",
            misses=(("Meet the Ridgeline, a very light hiking jacket built for changeable mountain weather. Its "
                     "waterproof shell and taped seams keep rain out, while underarm vents let heat escape on long "
                     "climbs. The hood packs into the collar. Fully waterproof and only £89, why hike without it?",
                     frozenset({"no 'very'"})),
                    ("Meet the Ridgeline, a light hiking jacket built for changeable mountain weather. Its waterproof "
                     "shell and taped seams keep rain out, while underarm vents let heat escape on long climbs. The "
                     "hood packs into the collar. At only £89, why hike without it?",
                     frozenset({"waterproof exactly twice"})),
                    ("Meet the Ridgeline, a light hiking jacket built for changeable mountain weather. Its waterproof "
                     "shell and taped seams keep rain out, while underarm vents let heat escape on long climbs. The "
                     "hood packs into the collar. Fully waterproof, and only £89.",
                     frozenset({"ends with a question"})))),
        InstructionCase(
            "instr-conventional-commit",
            "Write a commit message for this change: the CSV parser now accepts semicolons as separators, and a new "
            "test covers files that mix commas and semicolons. It closes issue 482. Rules: the first line has the "
            "form 'feat(parser): <summary>', is at most 50 characters, is all lowercase and does not end with a "
            "period; then one blank line; then exactly two body lines, each starting with '- ' and at most 72 "
            "characters; then one blank line; and finally the line 'Refs: #482'. Reply with only the commit message.",
            (C("feat(parser) prefix", lambda t: lines(t)[0].startswith("feat(parser): ")
               and lines(t)[0][len("feat(parser): "):].strip()),
             C("first line at most 50 characters", lambda t: len(lines(t)[0]) <= 50),
             C("first line lowercase without a period", lambda t: lines(t)[0] == lines(t)[0].lower()
               and not lines(t)[0].endswith(".")),
             C("layout", lambda t: len(lines(t)) == 6 and lines(t)[1] == "" and lines(t)[4] == ""
               and lines(t)[5] == "Refs: #482"),
             C("two short body bullets", lambda t: all(line.startswith("- ") and len(line) <= 72
                                                       for line in lines(t)[2:4]))),
            "hard",
            "feat(parser): accept semicolons as csv separators\n\n- Split fields on semicolons as well as commas\n"
            "- Add a test for files that mix both separators\n\nRefs: #482",
            misses=(("feat(parser): accept semicolons as CSV separators\n\n- Split fields on semicolons too\n"
                     "- Add a test for mixed separators\n\nRefs: #482",
                     frozenset({"first line lowercase without a period"})),
                    ("feat(parser): accept semicolons as csv field separators\n\n- Split fields on semicolons too\n"
                     "- Add a test for mixed separators\n\nRefs: #482",
                     frozenset({"first line at most 50 characters"})),
                    ("feat(parser): accept semicolons as csv separators\n\n- Split fields on semicolons too\n"
                     "- Add a test for mixed separators\nRefs: #482", frozenset({"layout"})))),
        InstructionCase(
            "instr-inner-tube-json",
            "Explain how to replace a bicycle's inner tube as a JSON object with exactly these keys, in this order: "
            "\"title\", \"steps\", \"warning\". \"title\" is a string of at most 6 words. \"steps\" is an array of "
            "exactly 5 strings, each at most 8 words, starting with a capital letter and not ending with a period. "
            "\"warning\" is one string that mentions the brakes, using the word 'brake' or 'brakes'. Reply with only "
            "the JSON object. " + WORD_RULE,
            (C("keys in order", lambda t: json_keys(t) == ["title", "steps", "warning"]),
             C("title at most 6 words", lambda t: isinstance(json_object(t)["title"], str)
               and 0 < word_count(json_object(t)["title"]) <= 6),
             C("five steps", lambda t: isinstance(json_object(t)["steps"], list) and len(json_object(t)["steps"]) == 5
               and all(isinstance(step, str) for step in json_object(t)["steps"])),
             C("steps at most 8 words", lambda t: all(0 < word_count(step) <= 8 for step in json_object(t)["steps"])),
             C("steps capitalised, no final period", lambda t: all(step[:1].isupper() and not step.rstrip().endswith(".")
                                                                   for step in json_object(t)["steps"])),
             C("warning mentions brakes", lambda t: isinstance(json_object(t)["warning"], str)
               and re.search(r"(?<![A-Za-z])brakes?(?![A-Za-z])", json_object(t)["warning"], re.IGNORECASE))),
            "hard",
            '{"title": "Replace a bicycle inner tube", "steps": ["Release the brake and remove the wheel", '
            '"Lever one side of the tyre off", "Pull out the old inner tube", "Fit the new tube and the tyre", '
            '"Inflate to the pressure on the tyre"], "warning": "Reconnect the brakes and test them before riding."}',
            json=True,
            misses=(('{"title": "Replace a bicycle inner tube", "steps": ["Release the brake and remove the wheel.", '
                     '"Lever one side of the tyre off.", "Pull out the old inner tube.", "Fit the new tube and the '
                     'tyre.", "Inflate to the pressure on the tyre."], "warning": "Test the brakes before riding."}',
                     frozenset({"steps capitalised, no final period"})),
                    ('{"title": "Replace a bicycle inner tube", "steps": ["Remove the wheel", "Lever the tyre off", '
                     '"Pull out the old tube", "Fit the new tube and the tyre"], "warning": "Test the brakes."}',
                     frozenset({"five steps"})),
                    ('{"steps": ["Remove the wheel", "Lever the tyre off", "Pull out the old tube", "Fit the new '
                     'tube", "Inflate the tyre"], "title": "Replace a tube", "warning": "Test the brakes."}',
                     frozenset({"keys in order"})))),
        InstructionCase(
            "instr-no-digits",
            "Rewrite this note so that it contains no digits at all, writing every number in words: 'Priya, the 3 pm "
            "review moved to 4 pm and is now on the 2nd floor in Room Orchid; bring 12 printed copies.' Keep the "
            "names Priya and Room Orchid, use at most 30 words, and reply with only the rewritten note. " + WORD_RULE,
            (C("no digits", lambda t: not re.search(r"[0-9]", t)),
             C("keeps the names", lambda t: "Priya" in t and "Room Orchid" in t),
             C("numbers in words", lambda t: all(count_word(t, word) for word in ("four", "second", "twelve"))),
             C("at most 30 words", lambda t: word_count(t) <= 30)),
            "hard",
            "Priya, the three pm review moved to four pm and is now on the second floor in Room Orchid; bring twelve "
            "printed copies.",
            misses=(("Priya, the three pm review moved to four pm and is now on the 2nd floor in Room Orchid; bring "
                     "twelve printed copies.", frozenset({"no digits", "numbers in words"})),
                    ("Priya, the review is now at four in the afternoon on the second floor in Room Orchid; bring "
                     "copies.", frozenset({"numbers in words"})))),
        InstructionCase(
            "instr-delay-texts",
            "A flight is delayed. Write two text messages to its passengers, separated by a line containing only "
            "'---'. The first message starts with '1/' and gives the new departure time, 12:15, and does not "
            "mention the gate. The second starts with '2/' and gives the new gate, B4, and does not mention the "
            "time. Each message is at most 160 characters. Reply with only the two messages and the separator line.",
            (C("two messages", lambda t: len(messages(t)) == 2),
             C("numbered 1/ and 2/", lambda t: [part.strip()[:2] for part in messages(t)] == ["1/", "2/"]),
             C("at most 160 characters each", lambda t: all(0 < len(part.strip()) <= 160
                                                            for part in messages(t))),
             C("time only in the first", lambda t: "12:15" in messages(t)[0]
               and "12:15" not in messages(t)[1]),
             C("gate only in the second", lambda t: "B4" in messages(t)[1]
               and "B4" not in messages(t)[0])),
            "hard",
            "1/ Your flight is delayed. The new departure time is 12:15. We are sorry for the wait.\n---\n"
            "2/ Your flight now leaves from gate B4. Please check the screens for boarding.",
            misses=(("1/ Your flight is delayed and now departs at 12:15 from gate B4.\n---\n"
                     "2/ Please go to gate B4 for boarding.", frozenset({"gate only in the second"})),
                    ("1/ Your flight is delayed. The new departure time is 12:15.\n\n"
                     "2/ Your flight now leaves from gate B4.",
                     frozenset({"two messages", "numbered 1/ and 2/", "time only in the first",
                                "gate only in the second"})))),
        InstructionCase(
            "instr-reschedule-email",
            "Write an email from Ana to Jordan asking to move Thursday's call to Friday at 14:00. Format: the first "
            "line is 'Subject: ' followed by a subject of at most 6 words; then a blank line; then the line "
            "'Hi Jordan,'; then a blank line and exactly two body paragraphs separated by a blank line; then a blank "
            "line and the last two lines 'Thanks,' and 'Ana'. Include the time as 14:00. Do not apologise: no "
            "'sorry', and no form of 'apologise', 'apologize' or 'apology'. Reply with only the email. " + WORD_RULE,
            (C("subject line", lambda t: blocks(t)[0][0].startswith("Subject: ") and len(blocks(t)[0]) == 1
               and 0 < word_count(blocks(t)[0][0][len("Subject: "):]) <= 6),
             C("greeting", lambda t: blocks(t)[1] == ["Hi Jordan,"]),
             C("two body paragraphs", lambda t: len(blocks(t)) == 5),
             C("sign-off", lambda t: blocks(t)[-1] == ["Thanks,", "Ana"] and lines(t)[1] == ""),
             C("includes 14:00", lambda t: "14:00" in t),
             C("no apology", lambda t: not re.search(r"sorry|apolog", t, re.IGNORECASE))),
            "hard",
            "Subject: Moving our call to Friday\n\nHi Jordan,\n\nCould we move Thursday's call to Friday at 14:00? "
            "Something has come up on Thursday afternoon.\n\nIf Friday doesn't suit you, let me know a time that "
            "does.\n\nThanks,\nAna",
            misses=(("Subject: Moving our call to Friday\n\nHi Jordan,\n\nSorry for the short notice, but could we "
                     "move Thursday's call to Friday at 14:00?\n\nLet me know if that works.\n\nThanks,\nAna",
                     frozenset({"no apology"})),
                    ("Subject: Moving our call to Friday\n\nHi Jordan,\n\nCould we move Thursday's call to Friday at "
                     "14:00? Let me know if that works.\n\nThanks,\nAna", frozenset({"two body paragraphs"})),
                    ("Subject: Request to move our Thursday call to Friday afternoon\n\nHi Jordan,\n\nCould we move "
                     "the call to Friday at 14:00?\n\nLet me know.\n\nThanks,\nAna", frozenset({"subject line"})))),
        InstructionCase(
            "instr-shoe-faq",
            "Write a three-question FAQ for an online shoe shop. Use exactly six lines, alternating question and "
            "answer and starting with a question. Each question line starts with 'Q: ' and ends with '?'. Each "
            "answer line starts with 'A: ' and has at most 20 words after the 'A: '. The first question contains "
            "the exact word 'delivery', the second 'returns' and the third 'warranty'. Use no exclamation marks. Reply "
            "with only the six lines. " + WORD_RULE,
            (C("six lines", lambda t: len(nonblank(t)) == 6),
             C("alternating Q and A", lambda t: all(line.startswith("Q: " if i % 2 == 0 else "A: ")
                                                    for i, line in enumerate(nonblank(t)))),
             C("questions end with ?", lambda t: all(line.endswith("?") for line in nonblank(t)[0::2])),
             C("answers at most 20 words", lambda t: all(0 < word_count(line[3:]) <= 20 for line in nonblank(t)[1::2])),
             C("topics in order", lambda t: [count_word(nonblank(t)[i], word) > 0 for i, word in
                                             ((0, "delivery"), (2, "returns"), (4, "warranty"))] == [True] * 3),
             C("no exclamation marks", lambda t: "!" not in t)),
            "hard",
            "Q: How long does delivery take?\nA: Standard delivery takes three to five working days, and express "
            "delivery arrives the next working day.\nQ: How do returns work?\nA: You can return unworn shoes within "
            "thirty days using the prepaid label in the box.\nQ: Is there a warranty on the shoes?\nA: Every pair has "
            "a one-year warranty against faults in materials and stitching.",
            misses=(("Q: How long does delivery take?\nA: Standard delivery takes three to five working days.\n"
                     "Q: Do you offer a warranty?\nA: Every pair has a one-year warranty.\nQ: How do returns work?\n"
                     "A: Return unworn shoes within thirty days.", frozenset({"topics in order"})),
                    ("Q: How long does delivery take?\nA: Standard delivery takes three to five working days, but "
                     "during sales and public holidays it can occasionally take a little longer than that to reach "
                     "you.\nQ: How do returns work?\nA: Return unworn shoes within thirty days.\nQ: Is there a "
                     "warranty?\nA: Every pair has a one-year warranty.", frozenset({"answers at most 20 words"})))),
        InstructionCase(
            "instr-scale-recipe",
            "This ingredient list serves 4. Rewrite it for 6 servings, scaling every amount in proportion. Keep the "
            "ingredients in the same order and write each one as a bullet in the form '- <amount> <unit> "
            "<ingredient>', with the amount in digits as a whole number or a decimal (no fractions, no trailing "
            "zeros after the decimal point, and a 0 before the point for amounts under 1), and the unit and "
            "ingredient exactly as given. Reply with only the list.\n\n"
            + "\n".join(f"- {amount} {unit} {name}" for amount, unit, name in RECIPE),
            (C("only five bullets", lambda t: only_bullets(t, 5)),
             *(C(f"line {i + 1}: {expected}", lambda t, i=i, expected=expected: nonblank(t)[i] == expected)
               for i, expected in enumerate(SCALED))),
            "hard",
            "\n".join(SCALED),
            misses=(("- 450 g flour\n- 375 ml milk\n- .75 tsp salt\n- 45 g butter\n- 2.25 tbsp sugar",
                     frozenset({"line 3: - 0.75 tsp salt"})),
                    ("- 450 g flour\n- 375 ml milk\n- 0.75 tsp salt\n- 45 g butter\n- 2 1/4 tbsp sugar",
                     frozenset({"line 5: - 2.25 tbsp sugar"})),
                    ("- 450.0 g flour\n- 375 ml milk\n- 0.75 tsp salt\n- 45 g butter\n- 2.25 tbsp sugar",
                     frozenset({"line 1: - 450 g flour"})))),
    ]


INSTRUCTION_CASES = instruction_cases()
