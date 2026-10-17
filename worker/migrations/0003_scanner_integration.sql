ALTER TABLE messages ADD COLUMN content_hash TEXT;
CREATE INDEX messages_content_hash_idx ON messages(content_hash, job_id);
ALTER TABLE jobs ADD COLUMN explanation TEXT NOT NULL DEFAULT '';
ALTER TABLE jobs ADD COLUMN reasons_json TEXT NOT NULL DEFAULT '[]';
ALTER TABLE jobs ADD COLUMN evidence_json TEXT NOT NULL DEFAULT '[]';
ALTER TABLE scan_runs ADD COLUMN review_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE scan_runs ADD COLUMN notifications_sent INTEGER NOT NULL DEFAULT 0;
