PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS cycles (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  started_at TEXT    NOT NULL DEFAULT (datetime('now')),
  mode       TEXT    NOT NULL CHECK (mode IN ('ind','proto')),
  status     TEXT    NOT NULL CHECK (status IN ('DONE','STO','ABORT')),
  duration_s REAL    NOT NULL CHECK (duration_s >= 0),
  passes     INTEGER NOT NULL DEFAULT 0,
  fmax       REAL    NOT NULL DEFAULT 0,
  water_ml   REAL    NOT NULL DEFAULT 0,
  source     TEXT    NOT NULL DEFAULT 'web',
  notes      TEXT    NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS events (
  id       INTEGER PRIMARY KEY AUTOINCREMENT,
  ts       TEXT    NOT NULL DEFAULT (datetime('now')),
  cycle_id INTEGER REFERENCES cycles(id) ON DELETE SET NULL,
  level    TEXT    NOT NULL CHECK (level IN ('info','warning','critical')),
  message  TEXT    NOT NULL
);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS idx_cycles_started ON cycles(started_at DESC);
CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts DESC);
