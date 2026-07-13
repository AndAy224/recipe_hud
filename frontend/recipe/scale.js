// Ingredient-quantity scaling. Parses ONLY the leading quantity of a line —
// never amounts inside prose — and fails safe: anything unparseable renders
// unchanged. Behavior reference:
//   "1 cup flour"        2x -> "2 cup flour"      ½x -> "½ cup flour"
//   "1/2 tsp salt"       2x -> "1 tsp salt"       ½x -> "¼ tsp salt"
//   "1 1/2 cups milk"    2x -> "3 cups milk"      ½x -> "¾ cups milk"
//   "½ cup sugar"        3x -> "1½ cup sugar"
//   "1½ cups broth"      2x -> "3 cups broth"
//   "2-3 tbsp oil"       2x -> "4-6 tbsp oil"     (both ends, separator kept)
//   "2 to 3 cloves"      2x -> "4 to 6 cloves"
//   "1.5 oz gin"         2x -> "3 oz gin"
//   "¼ tsp"              ½x -> "⅛ tsp"
//   "⅛ tsp"              ½x -> "0.1 tsp"          (1/16 has no glyph)
//   "1 (10.5 oz) can"    2x -> "2 (10.5 oz) can"  (parenthetical untouched)
//   unchanged: "Salt to taste", "eggs", "a pinch of saffron",
//              "2-inch piece ginger" (lookahead), "1,000 g" (comma rejected)

export const VULGAR = {
  "¼": 1 / 4, "½": 1 / 2, "¾": 3 / 4,
  "⅓": 1 / 3, "⅔": 2 / 3,
  "⅕": 1 / 5, "⅖": 2 / 5, "⅗": 3 / 5, "⅘": 4 / 5,
  "⅙": 1 / 6, "⅚": 5 / 6,
  "⅛": 1 / 8, "⅜": 3 / 8, "⅝": 5 / 8, "⅞": 7 / 8,
};
const VULGAR_CLASS = `[${Object.keys(VULGAR).join("")}]`;

// Longest alternatives first: mixed ASCII, ASCII fraction, number with
// optional attached vulgar, bare vulgar.
// The space before an attached vulgar ("1 ½") is only consumed when the
// glyph follows — a bare \s* would swallow the separator space in "2 to 3".
export const QTY = `(?:\\d+\\s+\\d+\\s*/\\s*\\d+|\\d+\\s*/\\s*\\d+|\\d+(?:\\.\\d+)?(?:\\s?${VULGAR_CLASS})?|${VULGAR_CLASS})`;
// Trailing lookahead is load-bearing: rejects "2-inch piece" and "1,000 g".
const LEAD_RE = new RegExp(
  `^(\\s*)(${QTY})(?:(\\s*(?:[-–—]|to)\\s*)(${QTY}))?(?=\\s|$)`
);

export function parseQty(str) {
  str = str.trim();
  const mixed = str.match(/^(\d+)\s+(\d+)\s*\/\s*(\d+)$/);
  if (mixed) return +mixed[1] + +mixed[2] / +mixed[3];
  const frac = str.match(/^(\d+)\s*\/\s*(\d+)$/);
  if (frac) return +frac[1] / +frac[2];
  const withVulgar = str.match(new RegExp(`^(\\d+(?:\\.\\d+)?)\\s*(${VULGAR_CLASS})$`));
  if (withVulgar) return +withVulgar[1] + VULGAR[withVulgar[2]];
  if (str in VULGAR) return VULGAR[str];
  const plain = Number(str);
  return Number.isFinite(plain) ? plain : null;
}

export function fmtQty(value) {
  if (value <= 0) return String(value);
  const whole = Math.floor(value);
  const frac = value - whole;
  if (frac < 0.01) return String(whole);
  for (const denom of [2, 3, 4, 6, 8]) {
    const num = Math.round(frac * denom);
    if (num > 0 && num < denom && Math.abs(frac - num / denom) < 0.01) {
      const glyph = Object.keys(VULGAR).find(
        (g) => Math.abs(VULGAR[g] - num / denom) < 0.001);
      if (glyph) return (whole > 0 ? whole : "") + glyph;
    }
  }
  const rounded = value.toFixed(1).replace(/\.0$/, "");
  return rounded;
}

export function scaleLine(text, factor) {
  if (factor === 1) return text;
  try {
    const match = text.match(LEAD_RE);
    if (!match) return text;
    const [full, lead, first, sep, second] = match;
    const a = parseQty(first);
    if (a === null) return text;
    let scaled = lead + fmtQty(a * factor);
    if (second) {
      const b = parseQty(second);
      if (b === null) return text;
      scaled += sep + fmtQty(b * factor);
    }
    return scaled + text.slice(full.length);
  } catch {
    return text;
  }
}
