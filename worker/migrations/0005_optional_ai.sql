CREATE TABLE ai_assessments (
  cache_key TEXT PRIMARY KEY CHECK(length(cache_key)=64),
  status TEXT NOT NULL CHECK(status IN ('COMPLETED','FAILED')),
  result_json TEXT,
  retry_after TEXT,
  provider TEXT NOT NULL,
  model TEXT NOT NULL,
  assessed_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  CHECK((status='COMPLETED' AND result_json IS NOT NULL) OR status='FAILED')
);
CREATE INDEX ai_assessments_status_idx ON ai_assessments(status, assessed_at);

CREATE TABLE ai_daily_usage (
  utc_date TEXT PRIMARY KEY,
  request_count INTEGER NOT NULL CHECK(request_count >= 0),
  updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
