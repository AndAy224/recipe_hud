"""Run the scale.js edge-case list through the real module in a browser.
Usage: python scripts/test_scale.py  (backend must be running on :8000)"""

from playwright.sync_api import sync_playwright

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
]

JS = """async (cases) => {
    const { scaleLine } = await import("/recipe/scale.js");
    return cases.map(([text, factor, want]) => {
        const got = scaleLine(text, factor);
        return got === want
            ? null
            : "FAIL: [" + text + "] x" + factor + " -> [" + got + "] want [" + want + "]";
    }).filter(Boolean);
}"""


def main() -> None:
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page()
        page.goto("http://127.0.0.1:8000/")
        failures = page.evaluate(JS, [list(c) for c in CASES])
        browser.close()
    if failures:
        print("\n".join(failures))
        raise SystemExit(1)
    print(f"ALL {len(CASES)} CASES PASS")


if __name__ == "__main__":
    main()
