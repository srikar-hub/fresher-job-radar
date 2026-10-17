ALTER TABLE channels ADD COLUMN verification_error TEXT;

CREATE TABLE telegram_updates (
  update_id INTEGER PRIMARY KEY,
  status TEXT NOT NULL CHECK(status IN ('PROCESSING','DONE')),
  updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE INDEX telegram_updates_updated_idx ON telegram_updates(updated_at);

INSERT INTO settings(key, monitoring_preferences_json)
VALUES ('global', '{"paused":false}')
ON CONFLICT(key) DO NOTHING;
