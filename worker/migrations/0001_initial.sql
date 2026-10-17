PRAGMA foreign_keys = ON;

CREATE TABLE channels (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  telegram_channel_id INTEGER UNIQUE,
  username TEXT,
  title TEXT,
  original_reference TEXT NOT NULL UNIQUE,
  status TEXT NOT NULL DEFAULT 'PENDING_VERIFICATION' CHECK(status IN ('PENDING_VERIFICATION','ACTIVE','PAUSED','INACCESSIBLE','REMOVED')),
  access_status TEXT NOT NULL DEFAULT 'UNKNOWN' CHECK(access_status IN ('UNKNOWN','VERIFIED','INACCESSIBLE')),
  created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE TABLE channel_checkpoints (
  channel_id INTEGER PRIMARY KEY REFERENCES channels(id),
  last_message_id INTEGER NOT NULL DEFAULT 0 CHECK(last_message_id >= 0),
  updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE TABLE jobs (
  id TEXT PRIMARY KEY,
  company TEXT, role TEXT, experience TEXT, graduation_batch INTEGER, location TEXT,
  skills_json TEXT NOT NULL DEFAULT '[]', application_url TEXT,
  normalized_application_url TEXT, requisition_id TEXT,
  classification TEXT NOT NULL CHECK(classification IN ('MATCHED','REVIEW','REJECTED')),
  match_score INTEGER NOT NULL DEFAULT 0 CHECK(match_score BETWEEN 0 AND 100),
  created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE UNIQUE INDEX jobs_normalized_url_uq ON jobs(normalized_application_url) WHERE normalized_application_url IS NOT NULL;
CREATE UNIQUE INDEX jobs_requisition_uq ON jobs(company, requisition_id) WHERE requisition_id IS NOT NULL;
CREATE TABLE messages (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  channel_id INTEGER NOT NULL REFERENCES channels(id), telegram_message_id INTEGER NOT NULL,
  posted_at TEXT, processing_status TEXT NOT NULL CHECK(processing_status IN ('QUEUED','PROCESSING','PROCESSED','RETRY')),
  attempt_count INTEGER NOT NULL DEFAULT 0, failure_reason TEXT, job_id TEXT REFERENCES jobs(id),
  created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  UNIQUE(channel_id, telegram_message_id)
);
CREATE INDEX messages_status_idx ON messages(processing_status, channel_id, telegram_message_id);
CREATE TABLE job_sources (
  job_id TEXT NOT NULL REFERENCES jobs(id), channel_id INTEGER NOT NULL REFERENCES channels(id),
  telegram_message_id INTEGER NOT NULL, original_message_link TEXT,
  PRIMARY KEY(job_id, channel_id, telegram_message_id),
  FOREIGN KEY(channel_id, telegram_message_id) REFERENCES messages(channel_id, telegram_message_id)
);
CREATE TABLE notifications (
  id TEXT PRIMARY KEY, job_id TEXT NOT NULL REFERENCES jobs(id), recipient_telegram_user_id TEXT NOT NULL,
  delivery_status TEXT NOT NULL DEFAULT 'PENDING' CHECK(delivery_status IN ('PENDING','SENDING','SENT','FAILED')),
  attempt_count INTEGER NOT NULL DEFAULT 0, retry_at TEXT, sent_at TEXT, last_error TEXT,
  lease_owner TEXT, lease_until TEXT,
  UNIQUE(job_id, recipient_telegram_user_id)
);
CREATE INDEX notifications_pending_idx ON notifications(delivery_status, retry_at);
CREATE TABLE scan_runs (
  id TEXT PRIMARY KEY, started_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  finished_at TEXT, status TEXT NOT NULL CHECK(status IN ('RUNNING','COMPLETED','FAILED')),
  messages_scanned INTEGER NOT NULL DEFAULT 0, matches INTEGER NOT NULL DEFAULT 0,
  rejections INTEGER NOT NULL DEFAULT 0, duplicates INTEGER NOT NULL DEFAULT 0, errors INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE settings (
  key TEXT PRIMARY KEY, authorized_user_id TEXT, filtering_preferences_json TEXT NOT NULL DEFAULT '{}',
  notification_mode TEXT NOT NULL DEFAULT 'INSTANT', monitoring_preferences_json TEXT NOT NULL DEFAULT '{}',
  updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE TABLE scan_leases (
  name TEXT PRIMARY KEY, owner TEXT NOT NULL, expires_at TEXT NOT NULL,
  updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
