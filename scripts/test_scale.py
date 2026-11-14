"""Run the scale.js edge-case list through the real module in a browser.

Usage: python scripts/test_scale.py
Needs a running backend (default :8000; point it elsewhere with
RECIPEHUD_TEST_BASE=http://127.0.0.1:8010) and a system Chromium."""

import json
import os

from _browser import evaluate

BASE = os.environ.get("RECIPEHUD_TEST_BASE", "http://127.0.0.1:8000")

CASES = [
    ("1 cup flour", 2, "2 cup flour"),
    ("1 cup flour", 0.5, "½ cup flour"),
    ("1/2 tsp salt", 2, "1 tsp salt"),
    ("1/2 tsp salt", 0.5, "¼ tsp salt"),
    ("1 1/2 cups milk", 2, "3 cups milk"),
    ("1 1/2 cups milk", 0.5, "¾ cups milk"),
    ("½ cup sugar", 3, "1½ cup sugar"),
    ("1½ cups broth", 2, "3 cups broth"),
    ("2-3 tbsp oil", 2, "4-6 tbsp oil"),
    ("2 to 3 cloves", 2, "4 to 6 cloves"),
    ("1.5 oz gin", 2, "3 oz gin"),
    ("¼ tsp", 0.5, "⅛ tsp"),
    ("⅛ tsp", 0.5, "0.1 tsp"),
    ("1 (10.5 oz) can tomatoes", 2, "2 (10.5 oz) can tomatoes"),
    ("Salt to taste", 2, "Salt to taste"),
    ("a pinch of saffron", 2, "a pinch of saffron"),
    ("2-inch piece ginger", 2, "2-inch piece ginger"),
    ("1,000 g flour", 2, "1,000 g flour"),
    ("eggs", 2, "eggs"),
    ("⅓ cup rice", 2, "⅔ cup rice"),
    ("3/4 cup broth", 2, "1½ cup broth"),
    # A size is not an amount: a mixed number hyphenated to a unit must stay
    # untouched, not have its leading integer scaled on its own.
    ("1 1/2-inch piece ginger", 2, "1 1/2-inch piece ginger"),
    ("1 1/2-pound pork tenderloin", 2, "1 1/2-pound pork tenderloin"),
    ("1 ½-inch piece ginger", 2, "1 ½-inch piece ginger"),
    ("1½-inch cubes", 2, "1½-inch cubes"),
    ("1.5-inch knob ginger", 2, "1.5-inch knob ginger"),
    # ...and a range scales at both ends or not at all.
    ("1 to 1 1/2-pound flank steak", 2, "1 to 1 1/2-pound flank steak"),
    ("1 to 1 1/2 cups stock", 2, "2 to 3 cups stock"),
    ("3 1/2-4 lb chicken", 2, "7-8 lb chicken"),
    # Still an amount when the size merely follows it.
    ("2 14.5-ounce cans tomatoes", 2, "4 14.5-ounce cans tomatoes"),
    ("1 ½ cups water", 2, "3 cups water"),
    ("2 tomatoes, diced", 2, "4 tomatoes, diced"),
]

JS = """(async (cases) => {
    const { scaleLine } = await import("/recipe/scale.js");
    return cases.map(([text, factor, want]) => {
        const got = scaleLine(text, factor);
        return got === want
            ? null
            : "FAIL: [" + text + "] x" + factor + " -> [" + got + "] want [" + want + "]";
    }).filter(Boolean);
})(%s)"""


def main() -> None:
    failures = evaluate(BASE + "/", JS % json.dumps([list(c) for c in CASES]))
    if failures:
        print("\n".join(failures))
        raise SystemExit(1)
    print(f"ALL {len(CASES)} CASES PASS")


if __name__ == "__main__":
    main()
