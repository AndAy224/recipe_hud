"""Seed the database with sample sites and timer presets (idempotent: only
inserts when the tables are empty). Uses the same schema/migration as the
backend."""

import os
import sqlite3
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "backend"))

from recipehud.db import SCHEMA_PATH, SCHEMA_VERSION  # noqa: E402

DB_PATH = Path(os.environ.get("RECIPEHUD_DB_PATH", REPO / "data" / "recipehud.db"))

SITES = [
    ("AllRecipes", "https://www.allrecipes.com/", "#e07a5f", "🍲", "direct"),
    ("Serious Eats", "https://www.seriouseats.com/", "#3d5a80", "🧑‍🍳", "direct"),
    ("BBC Good Food", "https://www.bbcgoodfood.com/", "#81b29a", "🥧", "direct"),
    ("Budget Bytes", "https://www.budgetbytes.com/", "#f2cc8f", "💰", "direct"),
]

PRESETS = [
    ("Soft eggs", 6 * 60),
    ("Pasta", 9 * 60),
    ("Rice", 15 * 60),
    ("Pizza", 12 * 60),
    ("Roast check", 30 * 60),
]


def main() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    if version < 1:
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

    if conn.execute("SELECT COUNT(*) FROM sites").fetchone()[0] == 0:
        conn.executemany(
            "INSERT INTO sites (name, url, color, icon, position, open_mode) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            [(n, u, c, i, pos, m) for pos, (n, u, c, i, m) in enumerate(SITES)],
        )
        print(f"seeded {len(SITES)} sites")

    if conn.execute("SELECT COUNT(*) FROM timer_presets").fetchone()[0] == 0:
        conn.executemany(
            "INSERT INTO timer_presets (label, seconds, position) VALUES (?, ?, ?)",
            [(label, seconds, pos) for pos, (label, seconds) in enumerate(PRESETS)],
        )
        print(f"seeded {len(PRESETS)} presets")

    conn.commit()
    conn.close()
    print(f"database ready at {DB_PATH}")


if __name__ == "__main__":
    main()
