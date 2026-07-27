-- Hermes Agent — D1 Database Schema for Cloudflare
-- Apply: npx wrangler d1 execute hermes-db --file ../schema.sql

CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY, platform TEXT NOT NULL, chat_id TEXT NOT NULL,
    user_id TEXT NOT NULL, user_name TEXT, title TEXT, source TEXT DEFAULT 'gateway',
    model TEXT, provider TEXT, status TEXT DEFAULT 'active',
    turn_count INTEGER DEFAULT 0, token_count INTEGER DEFAULT 0,
    created_at TEXT DEFAULT (datetime('now')), updated_at TEXT DEFAULT (datetime('now')),
    archived_at TEXT, UNIQUE(platform, chat_id, user_id)
);
CREATE INDEX IF NOT EXISTS idx_sessions_platform ON sessions(platform, updated_at DESC);

CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT REFERENCES sessions(id) ON DELETE CASCADE,
    role TEXT NOT NULL, content TEXT NOT NULL, tool_calls TEXT, tool_results TEXT,
    token_count INTEGER, created_at TEXT DEFAULT (datetime('now')),
    content_preview TEXT GENERATED ALWAYS AS (substr(content, 1, 1000)) STORED
);
CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id, created_at);

CREATE TABLE IF NOT EXISTS skills (
    id TEXT PRIMARY KEY, version INTEGER DEFAULT 1, title TEXT NOT NULL,
    description TEXT, category TEXT, prompt_text TEXT NOT NULL,
    metadata_json TEXT, usage_count INTEGER DEFAULT 0, success_rate REAL DEFAULT 0.0,
    created_at TEXT DEFAULT (datetime('now')), updated_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS memories (
    id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT NOT NULL, platform TEXT NOT NULL,
    key TEXT NOT NULL, value TEXT NOT NULL, confidence REAL DEFAULT 1.0,
    source TEXT, created_at TEXT DEFAULT (datetime('now')), updated_at TEXT DEFAULT (datetime('now')),
    UNIQUE(user_id, platform, key)
);

CREATE TABLE IF NOT EXISTS cron_jobs (
    id TEXT PRIMARY KEY, user_id TEXT NOT NULL, platform TEXT NOT NULL,
    chat_id TEXT NOT NULL, schedule TEXT NOT NULL, prompt TEXT NOT NULL,
    skills TEXT, enabled INTEGER DEFAULT 1, last_run_at TEXT, next_run_at TEXT,
    run_count INTEGER DEFAULT 0, created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS rate_limits (
    id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT NOT NULL, platform TEXT NOT NULL,
    window_start TEXT NOT NULL, counter INTEGER DEFAULT 0,
    UNIQUE(user_id, platform, window_start)
);

CREATE TABLE IF NOT EXISTS gateway_instances (
    id TEXT PRIMARY KEY, platform TEXT NOT NULL, bot_id TEXT NOT NULL,
    state TEXT DEFAULT 'disconnected', last_seen_at TEXT DEFAULT (datetime('now')),
    version TEXT, UNIQUE(platform, bot_id)
);

CREATE TABLE IF NOT EXISTS usage_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT NOT NULL, platform TEXT NOT NULL,
    session_id TEXT REFERENCES sessions(id), model TEXT NOT NULL, provider TEXT NOT NULL,
    prompt_tokens INTEGER DEFAULT 0, completion_tokens INTEGER DEFAULT 0,
    cached_tokens INTEGER DEFAULT 0, cost_usd REAL DEFAULT 0.0,
    created_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_usage_user ON usage_records(user_id, created_at DESC);

PRAGMA journal_mode = WAL;
PRAGMA busy_timeout = 5000;
PRAGMA cache_size = -64000;
