CREATE TABLE sites (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    url TEXT NOT NULL,
    color TEXT NOT NULL DEFAULT '#e07a5f',
    icon TEXT NOT NULL DEFAULT '',
    position INTEGER NOT NULL DEFAULT 0,
    visit_count INTEGER NOT NULL DEFAULT 0,
    last_visited_at TEXT,
    open_mode TEXT NOT NULL DEFAULT 'direct' CHECK (open_mode IN ('direct', 'clean'))
);

CREATE TABLE settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE timer_presets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    label TEXT NOT NULL,
    seconds INTEGER NOT NULL,
    position INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE timers_snapshot (
    id TEXT PRIMARY KEY,
    label TEXT NOT NULL,
    duration_s REAL NOT NULL,
    ends_at REAL,
    remaining_s REAL,
    state TEXT NOT NULL
);

CREATE TABLE recipe_cache (
    url TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    title TEXT NOT NULL,
    image_url TEXT,
    yields TEXT,
    total_time_s INTEGER,
    ingredients_json TEXT NOT NULL,
    steps_json TEXT NOT NULL,
    source_host TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    saved INTEGER NOT NULL DEFAULT 0,
    saved_at TEXT,
    image_local TEXT,
    tags TEXT NOT NULL DEFAULT ''
);
