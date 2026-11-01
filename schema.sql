CREATE TABLE IF NOT EXISTS links (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT NOT NULL UNIQUE,
    kind TEXT NOT NULL DEFAULT 'url',
    target_url TEXT NOT NULL,
    text_content TEXT,
    created_at TEXT NOT NULL,
    click_count INTEGER NOT NULL DEFAULT 0,
    last_clicked_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_links_created_at
    ON links (created_at DESC);
