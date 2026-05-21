-- news-agent storage schema. SQLite, audit-first.
-- Every decision and every surface is logged.

CREATE TABLE IF NOT EXISTS articles (
    id              TEXT PRIMARY KEY,         -- ArticleId (uuid)
    source_id       TEXT NOT NULL,
    url             TEXT NOT NULL,
    title           TEXT NOT NULL,
    body            TEXT NOT NULL,
    content_hash    TEXT NOT NULL,
    published_at    TEXT NOT NULL,
    fetched_at      TEXT NOT NULL,
    raw_json        TEXT
);

CREATE INDEX IF NOT EXISTS idx_articles_content_hash ON articles(content_hash);
CREATE INDEX IF NOT EXISTS idx_articles_source ON articles(source_id);
CREATE INDEX IF NOT EXISTS idx_articles_fetched ON articles(fetched_at);

CREATE TABLE IF NOT EXISTS tags (
    article_id      TEXT NOT NULL REFERENCES articles(id),
    tag             TEXT NOT NULL,
    category        TEXT NOT NULL,
    confidence      REAL NOT NULL,
    model           TEXT NOT NULL,
    tagged_at       TEXT NOT NULL,
    PRIMARY KEY (article_id, tag)
);

CREATE INDEX IF NOT EXISTS idx_tags_tag ON tags(tag);

CREATE TABLE IF NOT EXISTS scores (
    article_id      TEXT NOT NULL REFERENCES articles(id),
    topic_id        TEXT NOT NULL,
    substance       REAL NOT NULL,
    tag_adj         REAL NOT NULL,
    decay           REAL NOT NULL,
    source_weight   REAL NOT NULL,
    final_score     REAL NOT NULL,
    scored_at       TEXT NOT NULL,
    PRIMARY KEY (article_id, topic_id)
);

CREATE INDEX IF NOT EXISTS idx_scores_final ON scores(final_score DESC);

CREATE TABLE IF NOT EXISTS surfaces (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    article_id      TEXT NOT NULL REFERENCES articles(id),
    topic_id        TEXT NOT NULL,
    cadence         TEXT NOT NULL,            -- digest | priority_dm | weekly | on_demand
    surface         TEXT NOT NULL,            -- slack | telegram
    channel         TEXT NOT NULL,
    message_id      TEXT NOT NULL,
    posted_at       TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_surfaces_article ON surfaces(article_id);
CREATE INDEX IF NOT EXISTS idx_surfaces_posted ON surfaces(posted_at);

CREATE TABLE IF NOT EXISTS corrections (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    article_id      TEXT NOT NULL REFERENCES articles(id),
    topic_id        TEXT NOT NULL,
    source_id       TEXT NOT NULL,
    kind            TEXT NOT NULL,            -- boost | demote | retag | save | skip
    new_topic_id    TEXT,
    user            TEXT NOT NULL,
    surface         TEXT NOT NULL,
    at              TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS source_priors (
    source_id       TEXT NOT NULL,
    topic_id        TEXT NOT NULL,
    weight          REAL NOT NULL,
    updated_at      TEXT NOT NULL,
    PRIMARY KEY (source_id, topic_id)
);

CREATE TABLE IF NOT EXISTS tag_suggestions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    tag             TEXT NOT NULL,
    proposed_category TEXT,
    sample_article  TEXT NOT NULL REFERENCES articles(id),
    frequency       INTEGER NOT NULL DEFAULT 1,
    status          TEXT NOT NULL DEFAULT 'pending',   -- pending | accepted | rejected | blacklisted
    first_seen_at   TEXT NOT NULL,
    last_seen_at    TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_tag_suggestions_status ON tag_suggestions(status);

CREATE TABLE IF NOT EXISTS source_stats (
    source_id       TEXT PRIMARY KEY,
    items_fetched   INTEGER NOT NULL DEFAULT 0,
    items_tagged    INTEGER NOT NULL DEFAULT 0,
    items_scored    INTEGER NOT NULL DEFAULT 0,
    items_surfaced  INTEGER NOT NULL DEFAULT 0,
    positive_corrections INTEGER NOT NULL DEFAULT 0,
    negative_corrections INTEGER NOT NULL DEFAULT 0,
    updated_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS llm_cost (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    model           TEXT NOT NULL,
    input_tokens    INTEGER NOT NULL,
    output_tokens   INTEGER NOT NULL,
    usd_cents       INTEGER NOT NULL,
    article_id      TEXT,
    purpose         TEXT NOT NULL,             -- tag | score | search
    at              TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_llm_cost_at ON llm_cost(at);

CREATE TABLE IF NOT EXISTS audit_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    event           TEXT NOT NULL,
    article_id      TEXT,
    payload_json    TEXT,
    at              TEXT NOT NULL
);
